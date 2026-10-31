# pos/views.py
import json
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Sum
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from accounts import audit
from accounts.decorators import role_required
from accounts.roles import TIER_LABELS, allowed_tiers, can_void, default_tier, max_discount
from sku_manager.models import AppSettings, JewelrySKU

from . import services as pos
from .models import PosSale, ResellerPrice

ONLY_CASHIER = ('cashier',)     # the counter belongs to cashier accounts; nobody else can open it


def _brand():
    s = AppSettings.get_settings()
    return {'brand_name': s.brand_name, 'tagline': s.tagline, 'currency': s.currency_symbol or '₹',
            'support_contact': s.support_contact, 'logo_url': s.label_logo.url if s.label_logo else ''}


@role_required(*ONLY_CASHIER)
@ensure_csrf_cookie
def pos_screen(request):
    tiers = allowed_tiers(request.user)
    return render(request, 'pos/pos.html', dict(
        _brand(), active_tab='bill', max_discount=max_discount(request.user),
        tiers=[(t, TIER_LABELS[t]) for t in tiers], tier_keys=','.join(tiers), start_tier=default_tier(request.user)))


@role_required(*ONLY_CASHIER)
def pos_products_api(request):
    """Every listed product with its live stock. Only the price lists this user may use are included."""
    allowed = allowed_tiers(request.user)
    rows = []
    for p in JewelrySKU.objects.filter(is_listed=True).order_by('name'):
        rows.append({
            'id': p.pk, 'name': p.name, 'sku': p.sku, 'category': p.category, 'color': p.color, 'size': p.size,
            'stock': p.stock, 'prices': pos.all_prices(p, allowed), 'image': p.image.url if p.image else '',
        })
    return JsonResponse({'products': rows})


@role_required(*ONLY_CASHIER)
@require_POST
def pos_checkout(request):
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'message': 'Bad request.'}, status=400)
    try:
        sale, repeated = pos.checkout(
            user=request.user, items=body.get('items'), price_list=body.get('price_list', 'reselling'),
            discount_percent=body.get('discount_percent', 0), payment_method=body.get('payment_method', 'cash'),
            amount_received=body.get('amount_received'), customer_name=body.get('customer_name', ''),
            customer_phone=body.get('customer_phone', ''), token=body.get('token', ''),
            max_discount=max_discount(request.user), allowed_tiers=allowed_tiers(request.user))
    except pos.PosError as exc:
        audit.log(request, 'sale_failed', str(exc))
        return JsonResponse({'ok': False, 'message': str(exc), 'short': exc.short}, status=409 if exc.short else 400)
    if not repeated:
        audit.log(request, 'sale', f'Bill {sale.bill_no} · {sale.items_count} item(s) · {sale.price_list} · total {sale.total}')
    stock = {i.sku_id: i.sku.stock for i in sale.items.select_related('sku') if i.sku_id}
    return JsonResponse({
        'ok': True, 'repeated': repeated, 'sale_id': sale.pk, 'bill_no': sale.bill_no, 'total': str(sale.total),
        'subtotal': str(sale.subtotal), 'discount': str(sale.discount_amount), 'received': str(sale.amount_received),
        'change': str(sale.change_given), 'payment': sale.get_payment_method_display(), 'items': sale.items_count,
        'receipt_url': reverse('pos_receipt', args=[sale.pk]),
        'stock': stock,
    })


def _visible_sales(user):
    return PosSale.objects.filter(cashier=user)          # a cashier only ever sees their own bills


@role_required(*ONLY_CASHIER)
def pos_receipt(request, pk):
    sale = get_object_or_404(_visible_sales(request.user).prefetch_related('items'), pk=pk)
    return render(request, 'pos/receipt.html', dict(_brand(), sale=sale, auto_print=request.GET.get('print') == '1'))


@role_required(*ONLY_CASHIER)
def pos_sales(request):
    day = timezone.localdate()
    raw = request.GET.get('date', '')
    if raw:
        try:
            day = datetime.strptime(raw, '%Y-%m-%d').date()
        except ValueError:
            pass
    qs = _visible_sales(request.user).filter(created_at__date=day).prefetch_related('items')
    paid = qs.filter(status=PosSale.STATUS_PAID)
    totals = paid.aggregate(revenue=Sum('total'), discount=Sum('discount_amount'), bills=Count('pk'))
    by_payment = {row['payment_method']: row['t'] for row in paid.values('payment_method').annotate(t=Sum('total'))}
    units = sum(item.quantity for sale in paid for item in sale.items.all())
    bills = totals['bills'] or 0
    revenue = totals['revenue'] or Decimal('0')
    return render(request, 'pos/sales.html', dict(
        _brand(), active_tab='sales', day=day, sales=qs[:300], bills=bills, units=units, revenue=revenue,
        discount=totals['discount'] or Decimal('0'), avg=(revenue / bills) if bills else Decimal('0'),
        cash=by_payment.get('cash', 0), upi=by_payment.get('upi', 0), card=by_payment.get('card', 0),
        ))


@role_required('admin', 'master')
def pos_prices(request):
    """Admin-maintained reseller prices; blank or zero uses the configured markup."""
    products = JewelrySKU.objects.filter(is_listed=True).order_by('name')
    if request.method == 'POST':
        changed = 0
        for product in products:
            raw = request.POST.get(f'reseller_{product.pk}', '').strip()
            if not raw:
                ResellerPrice.objects.filter(sku=product).delete()
                continue
            try:
                value = Decimal(raw)
                if value < 0:
                    raise ValueError
            except Exception:
                messages.error(request, f'Enter a valid non-negative reseller price for {product.name}.')
                return redirect('pos_prices')
            obj, created = ResellerPrice.objects.get_or_create(sku=product, defaults={'price': value})
            if not created and obj.price != value:
                obj.price = value
                obj.save(update_fields=['price', 'updated_at'])
            changed += 1
        audit.log(request, 'price_update', f'Saved reseller prices for {changed} products')
        messages.success(request, 'Reseller prices saved.')
        return redirect('pos_prices')
    markup = Decimal(str(getattr(settings, 'POS_RESELLER_MARKUP_PERCENT', 10)))
    rows = []
    for product in products:
        custom = getattr(product, 'reseller_price', None)
        wholesale = Decimal(str(product.wholesale_price or 0))
        rows.append({'p': product, 'own': custom.price if custom else None,
                     'auto': (wholesale * (1 + markup / 100)).quantize(Decimal('0.01'))})
    return render(request, 'pos/prices.html', {'rows': rows, 'markup': markup})


@role_required(*ONLY_CASHIER)
@require_POST
def pos_void(request, pk):
    """Cancel one of your own bills and put the items back. Only for cashiers the Master Admin allowed."""
    if not can_void(request.user):
        raise Http404
    sale = get_object_or_404(_visible_sales(request.user), pk=pk)
    try:
        pos.void_sale(sale, request.user)
        audit.log(request, 'sale_void', f'Voided bill {sale.bill_no} (total {sale.total}), stock restored')
        messages.success(request, f'Bill {sale.bill_no} cancelled and stock restored.')
    except pos.PosError as exc:
        messages.error(request, str(exc))
    return redirect('pos_sales')
