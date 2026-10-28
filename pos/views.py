# pos/views.py
import json
from datetime import datetime
from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from accounts import audit
from accounts.decorators import role_required
from accounts.roles import (TIER_KEYS, TIER_LABELS, allowed_tiers, default_tier, get_role, is_manager,
                            max_discount)
from sku_manager.models import AppSettings, JewelrySKU

from . import services as pos
from .models import PosSale, ResellerPrice

ANY_ROLE = ('master', 'admin', 'cashier')
MANAGERS = ('master', 'admin')


def _brand():
    s = AppSettings.get_settings()
    return {'brand_name': s.brand_name, 'tagline': s.tagline, 'currency': s.currency_symbol or '₹',
            'support_contact': s.support_contact, 'logo_url': s.label_logo.url if s.label_logo else ''}


@role_required(*ANY_ROLE)
@ensure_csrf_cookie
def pos_screen(request):
    tiers = allowed_tiers(request.user)
    return render(request, 'pos/pos.html', dict(
        _brand(), active_tab='bill', max_discount=max_discount(request.user),
        tiers=[(t, TIER_LABELS[t]) for t in tiers], tier_keys=','.join(tiers), start_tier=default_tier(request.user)))


@role_required(*ANY_ROLE)
def pos_products_api(request):
    """Every listed product with its live stock. Only the price lists this user may use are included."""
    allowed = allowed_tiers(request.user)
    rows = []
    for p in JewelrySKU.objects.filter(is_listed=True).select_related('reseller_price').order_by('name'):
        rows.append({
            'id': p.pk, 'name': p.name, 'sku': p.sku, 'category': p.category, 'color': p.color, 'size': p.size,
            'stock': p.stock, 'prices': pos.all_prices(p, allowed), 'image': p.image.url if p.image else '',
        })
    return JsonResponse({'products': rows})


@role_required(*ANY_ROLE)
@require_POST
def pos_checkout(request):
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'message': 'Bad request.'}, status=400)
    try:
        sale, repeated = pos.checkout(
            user=request.user, items=body.get('items'), price_list=body.get('price_list', 'retail'),
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
    qs = PosSale.objects.all()
    return qs if is_manager(user) else qs.filter(cashier=user)


@role_required(*ANY_ROLE)
def pos_receipt(request, pk):
    sale = get_object_or_404(_visible_sales(request.user).prefetch_related('items'), pk=pk)
    return render(request, 'pos/receipt.html', dict(_brand(), sale=sale, auto_print=request.GET.get('print') == '1'))


@role_required(*ANY_ROLE)
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


@role_required(*MANAGERS)
@require_POST
def pos_void(request, pk):
    if not is_manager(request.user):
        raise Http404
    sale = get_object_or_404(PosSale, pk=pk)
    try:
        pos.void_sale(sale, request.user)
        audit.log(request, 'sale_void', f'Voided bill {sale.bill_no} (total {sale.total}), stock restored')
        messages.success(request, f'Bill {sale.bill_no} voided and stock restored.')
    except pos.PosError as exc:
        messages.error(request, str(exc))
    back = request.META.get('HTTP_REFERER', '')
    if back and url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        return redirect(back)
    return redirect('pos_sales')


@role_required(*MANAGERS)
def pos_prices(request):
    """Set the reseller price per product (blank = wholesale + markup)."""
    skus = list(JewelrySKU.objects.filter(is_listed=True).select_related('reseller_price').order_by('name'))
    if request.method == 'POST':
        changed = 0
        for p in skus:
            raw = request.POST.get(f'reseller_{p.pk}', '').strip()
            try:
                value = Decimal(raw) if raw else Decimal('0')
            except Exception:
                continue
            if value < 0:
                continue
            current = getattr(getattr(p, 'reseller_price', None), 'price', Decimal('0'))
            if value != current:
                if value == 0:
                    ResellerPrice.objects.filter(sku=p).delete()
                else:
                    ResellerPrice.objects.update_or_create(sku=p, defaults={'price': value})
                changed += 1
        audit.log(request, 'prices_update', f'Reseller prices changed for {changed} product(s)')
        messages.success(request, f'Reseller prices saved ({changed} changed).')
        return redirect('pos_prices')
    rows = [{'p': p, 'own': getattr(getattr(p, 'reseller_price', None), 'price', None),
             'auto': pos.unit_price(p, 'reseller')} for p in skus]
    return render(request, 'pos/prices.html', dict(_brand(), active_tab='prices', rows=rows))
