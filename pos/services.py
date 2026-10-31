# pos/services.py
"""Checkout + void for counter sales. All stock movement happens here, in one transaction."""
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.roles import TIER_KEYS, TIER_LABELS, normalize_tier
from sku_manager.models import DispatchLog, JewelrySKU, StockBatch

from .models import PosSale, PosSaleItem

CENT = Decimal('0.01')
PLATFORM_NAME = 'Counter (POS)'
MAX_QTY_PER_LINE = 999


class PosError(Exception):
    """A sale that cannot be completed. `short` lists products that lack stock."""

    def __init__(self, message, short=None):
        super().__init__(message)
        self.short = short or []


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def unit_price(sku, tier):
    """Price of one unit on a given price list - the same three prices as your stock form."""
    if tier == 'buying':
        return sku.purchase_price          # "Buying Price"
    if tier == 'wholesale':
        return sku.wholesale_price         # "Wholesale Price"
    return sku.selling_price               # "Reselling Price" (saved as the product's selling price)


def all_prices(sku, allowed):
    """{tier: price} but only for the tiers this person may see - the others never leave the server."""
    return {t: float(unit_price(sku, t)) for t in allowed}


def _clean_items(items):
    merged = {}
    for row in items or []:
        try:
            sku_id, qty = int(row.get('id')), int(row.get('qty'))
        except (TypeError, ValueError, AttributeError):
            raise PosError('The cart contains an invalid line.')
        if qty < 1 or qty > MAX_QTY_PER_LINE:
            raise PosError(f'Quantity must be between 1 and {MAX_QTY_PER_LINE}.')
        merged[sku_id] = merged.get(sku_id, 0) + qty
    if not merged:
        raise PosError('The cart is empty.')
    return merged


def checkout(*, user, items, price_list='reselling', discount_percent=0, payment_method='cash',
             amount_received=None, customer_name='', customer_phone='', token='', max_discount=100,
             allowed_tiers=None):
    """Create a paid sale and take the units out of stock (oldest batch first)."""
    token = (token or '').strip()[:64] or None
    if token:
        existing = PosSale.objects.filter(token=token).first()
        if existing:
            return existing, True          # already billed: same answer, no second deduction

    allowed = list(allowed_tiers) if allowed_tiers is not None else list(TIER_KEYS)
    price_list = normalize_tier(price_list)          # old names (retail/reseller) -> reselling
    if price_list not in TIER_KEYS:
        raise PosError('Choose a price list.')
    if price_list not in allowed:
        raise PosError(f'Your account is not allowed to sell at {TIER_LABELS[price_list]} prices.')
    if payment_method not in dict(PosSale.PAYMENT_CHOICES):
        raise PosError('Choose a payment method.')
    try:
        discount_percent = Decimal(str(discount_percent or 0))
    except Exception:
        raise PosError('Discount is not a valid number.')
    if discount_percent < 0 or discount_percent > Decimal(str(max_discount)):
        raise PosError(f'Discount must be between 0 and {max_discount}%.')
    if price_list == 'buying' and discount_percent:
        raise PosError('No discount can be given on buying (cost) price.')
    wanted = _clean_items(items)

    try:
        with transaction.atomic():
            skus = {s.pk: s for s in JewelrySKU.objects.select_for_update().filter(pk__in=list(wanted), is_listed=True)}
            missing = [pk for pk in wanted if pk not in skus]
            if missing:
                raise PosError('A product in the cart is no longer available. Refresh and try again.')

            batches, short = {}, []
            for pk, qty in wanted.items():
                rows = list(StockBatch.objects.select_for_update()
                            .filter(sku_id=pk, status='active', sales_enabled=True, quantity__gt=0)
                            .order_by('created_at', 'pk'))
                have = sum(b.quantity for b in rows)
                batches[pk] = rows
                if have < qty:
                    short.append({'id': pk, 'name': skus[pk].name, 'wanted': qty, 'available': have})
                if unit_price(skus[pk], price_list) <= 0:
                    raise PosError(f'No {TIER_LABELS[price_list]} price is set for "{skus[pk].name}". Set it in Inventory first.')
            if short:
                names = ', '.join(f"{s['name']} (only {s['available']} left)" for s in short)
                raise PosError(f'Not enough stock: {names}', short)

            lines, subtotal = [], Decimal('0')
            for pk, qty in wanted.items():
                sku, left, taken = skus[pk], qty, []
                for batch in batches[pk]:
                    if left <= 0:
                        break
                    take = min(batch.quantity, left)
                    batch.quantity -= take
                    batch.save(update_fields=['quantity', 'updated_at'])
                    taken.append([batch.pk, take])
                    left -= take
                price = money(unit_price(sku, price_list))
                line_total = money(price * qty)
                subtotal += line_total
                lines.append((sku, qty, price, line_total, taken))

            discount_amount = money(subtotal * discount_percent / 100)
            total = money(subtotal - discount_amount)
            if payment_method == 'cash':
                received = money(amount_received) if amount_received not in (None, '') else total
                if received < total:
                    raise PosError(f'Cash received ({received}) is less than the total ({total}).')
            else:
                received = total
            change = money(received - total)

            sale = PosSale.objects.create(
                bill_no=f'TMP-{uuid.uuid4().hex[:16]}', token=token, cashier=user if getattr(user, 'pk', None) else None,
                cashier_name=getattr(user, 'get_username', lambda: '')(), customer_name=(customer_name or '').strip()[:120],
                customer_phone=(customer_phone or '').strip()[:20], price_list=price_list, payment_method=payment_method,
                subtotal=subtotal, discount_percent=discount_percent, discount_amount=discount_amount, total=total,
                amount_received=received, change_given=change)
            sale.bill_no = f'TP{timezone.localtime(sale.created_at):%y%m%d}-{sale.pk:05d}'
            sale.save(update_fields=['bill_no'])

            for sku, qty, price, line_total, taken in lines:
                sku.sync_stock_from_batches()
                # revenue on the dashboard uses the price after the bill discount
                net_unit = money(price * (1 - discount_percent / 100))
                log = DispatchLog.objects.create(sku=sku, platform=None, platform_name=PLATFORM_NAME, quantity=qty,
                                                 sold_price=net_unit, stock_after=sku.stock)
                PosSaleItem.objects.create(sale=sale, sku=sku, sku_code=sku.sku, name=sku.name, quantity=qty,
                                           unit_price=price, line_total=line_total, allocations=taken, dispatch_log=log)
    except IntegrityError:
        # two taps raced with the same token: return the sale the other request created
        existing = PosSale.objects.filter(token=token).first() if token else None
        if existing:
            return existing, True
        raise
    return sale, False


def void_sale(sale, user):
    """Cancel a paid sale and put every unit back into the batch it came from."""
    with transaction.atomic():
        sale = PosSale.objects.select_for_update().get(pk=sale.pk)
        if sale.status == PosSale.STATUS_VOID:
            raise PosError('This bill is already void.')
        for item in sale.items.select_related('sku'):
            for batch_id, qty in item.allocations:
                batch = StockBatch.objects.select_for_update().filter(pk=batch_id).first()
                if batch is not None:
                    batch.quantity += qty
                    batch.save(update_fields=['quantity', 'updated_at'])
                elif item.sku_id:
                    StockBatch.objects.create(sku=item.sku, batch_no=f'VOID-{sale.bill_no}', quantity=qty,
                                              purchase_price=item.sku.purchase_price,
                                              wholesale_price=item.sku.wholesale_price,
                                              selling_price=item.sku.selling_price)
            if item.sku_id:
                item.sku.sync_stock_from_batches()
            if item.dispatch_log_id:
                item.dispatch_log.delete()     # a voided sale is not revenue
        sale.status = PosSale.STATUS_VOID
        sale.voided_at = timezone.now()
        sale.voided_by_name = user.get_username()
        sale.save(update_fields=['status', 'voided_at', 'voided_by_name'])
    return sale
