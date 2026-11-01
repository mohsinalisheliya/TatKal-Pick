# sku_manager/dashboard_data.py
"""Everything the dashboard shows, computed in one place.

Revenue = quantity x unit price saved on each DispatchLog. "Est. profit" =
revenue - quantity x the product's current buying price (before marketplace
commission, shipping and packaging).
"""
from collections import Counter, defaultdict
from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

from .models import (
    AppSettings, DispatchLog, JewelrySKU, MarketplaceOrder,
    PackagingMaterial, StockBatch, StorageBox,
)

PERIODS = (7, 30, 90)
WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']


def _f(value):
    return float(value or 0)


def inr(value, symbol='₹'):
    """Indian digit grouping: 1,23,456."""
    n = int(round(value))
    sign = '-' if n < 0 else ''
    s = str(abs(n))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ','.join(parts) + ',' + tail
    return f'{sign}{symbol}{s}'


def _delta(cur, prev):
    return round((cur - prev) * 100.0 / prev, 1) if prev else None


def _spark(values, width=100, height=26):
    if len(values) < 2 or max(values) == min(values):
        return ''
    lo, hi = min(values), max(values)
    step = width / (len(values) - 1)
    pts = [f'{i * step:.1f},{height - 2 - (v - lo) / (hi - lo) * (height - 4):.1f}' for i, v in enumerate(values)]
    return ' '.join(pts)


def _blank():
    return {'units': 0, 'revenue': 0.0, 'orders': 0, 'profit': 0.0}


def _has_field(model, name):
    return any(f.name == name for f in model._meta.get_fields())


def build_dashboard(days=30):
    if days not in PERIODS:
        days = 30
    settings_obj = AppSettings.get_settings()
    cur_sym = settings_obj.currency_symbol or '₹'
    threshold = settings_obj.low_stock_threshold
    now = timezone.now()
    today = timezone.localdate()
    start = now - timedelta(days=days)
    prev_start = start - timedelta(days=days)

    logs = list(DispatchLog.objects.filter(dispatched_at__gte=prev_start)
                .select_related('sku').order_by('dispatched_at'))
    cur = [l for l in logs if l.dispatched_at >= start]
    prev = [l for l in logs if l.dispatched_at < start]

    def totals(rows):
        t = _blank()
        for l in rows:
            rev = _f(l.sold_price) * l.quantity
            t['units'] += l.quantity
            t['orders'] += 1
            t['revenue'] += rev
            t['profit'] += rev - _f(l.sku.purchase_price) * l.quantity
        return t

    tc, tp = totals(cur), totals(prev)
    aov_c = tc['revenue'] / tc['orders'] if tc['orders'] else 0
    aov_p = tp['revenue'] / tp['orders'] if tp['orders'] else 0

    # ---- daily series, channels, products, categories, heatmap ----
    day_list = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    daily = {d: _blank() for d in day_list}
    by_platform = defaultdict(_blank)
    by_sku = defaultdict(lambda: dict(_blank(), name='', sku='', category=''))
    by_category = defaultdict(_blank)
    heat = [[0] * 24 for _ in range(7)]
    for l in cur:
        local = timezone.localtime(l.dispatched_at)
        rev = _f(l.sold_price) * l.quantity
        prof = rev - _f(l.sku.purchase_price) * l.quantity
        for bucket in (daily.get(local.date()), by_platform[l.platform_name], by_category[l.sku.category]):
            if bucket is not None:
                bucket['units'] += l.quantity
                bucket['orders'] += 1
                bucket['revenue'] += rev
                bucket['profit'] += prof
        s = by_sku[l.sku_id]
        s.update(name=l.sku.name, sku=l.sku.sku, category=l.sku.category)
        s['units'] += l.quantity
        s['orders'] += 1
        s['revenue'] += rev
        s['profit'] += prof
        heat[local.weekday()][local.hour] += 1

    # ---- returns (RTO) ----
    cancelled = MarketplaceOrder.objects.filter(status=MarketplaceOrder.STATUS_CANCELLED, cancelled_at__gte=start)
    returns_by_platform = Counter(cancelled.values_list('platform_name', flat=True))
    returns_total = sum(returns_by_platform.values())
    shipped_orders = MarketplaceOrder.objects.filter(
        status=MarketplaceOrder.STATUS_DISPATCHED, dispatched_at__gte=start).count()
    rto_base = shipped_orders + returns_total
    rto_rate = round(returns_total * 100.0 / rto_base, 1) if rto_base else 0.0

    # ---- inventory ----
    skus = list(JewelrySKU.objects.all())
    listed = [s for s in skus if s.is_listed]
    out_of_stock = [s for s in listed if s.stock == 0]
    low_stock = [s for s in listed if 0 < s.stock <= threshold]
    healthy = len(listed) - len(out_of_stock) - len(low_stock)

    cost_value = retail_value = 0.0
    held_units = 0
    units_by_box = Counter()
    box_names = {b.pk: b for b in StorageBox.objects.all()}
    for b in StockBatch.objects.filter(status='active', quantity__gt=0):
        cost_value += b.quantity * _f(b.purchase_price)
        retail_value += b.quantity * _f(b.selling_price)
        if not b.sales_enabled:
            held_units += b.quantity
        units_by_box[b.storage_box_id] += b.quantity
    total_stock = sum(s.stock for s in skus)

    last_sale = dict(DispatchLog.objects.values_list('sku_id').annotate(m=Max('dispatched_at')))
    dead_cut = now - timedelta(days=30)
    dead = sorted(
        ({'pk': s.pk, 'name': s.name, 'sku': s.sku, 'stock': s.stock,
          'value': s.stock * _f(s.purchase_price),
          'last': timezone.localtime(last_sale[s.pk]).strftime('%d %b %Y') if s.pk in last_sale else 'Never sold'}
         for s in listed if s.stock > 0 and not (s.pk in last_sale and last_sale[s.pk] >= dead_cut)),
        key=lambda r: -r['value'])[:6]
    dead_value = sum(r['value'] for r in dead)

    stock_by_pk = {s.pk: s for s in skus}
    cover = []
    for pk, row in by_sku.items():
        item = stock_by_pk.get(pk)
        per_day = row['units'] / days
        if item and per_day > 0 and item.is_listed:
            cover.append({'pk': pk, 'name': item.name, 'stock': item.stock, 'per_day': round(per_day, 2),
                          'days': round(item.stock / per_day, 1)})
    cover.sort(key=lambda r: r['days'])
    cover = cover[:6]

    top_products = []
    for pk, row in sorted(by_sku.items(), key=lambda kv: (-kv[1]['revenue'], -kv[1]['units']))[:8]:
        item = stock_by_pk.get(pk)
        top_products.append({
            'pk': pk, 'name': row['name'], 'sku': row['sku'], 'units': row['units'],
            'revenue': inr(row['revenue'], cur_sym), 'profit': inr(row['profit'], cur_sym),
            'margin': round(row['profit'] * 100 / row['revenue'], 1) if row['revenue'] else 0,
            'stock': item.stock if item else 0,
            'days': next((c['days'] for c in cover if c['pk'] == pk), None),
            'share': round(row['revenue'] * 100 / tc['revenue'], 1) if tc['revenue'] else 0,
        })

    channels = []
    for name, row in sorted(by_platform.items(), key=lambda kv: -kv[1]['revenue']):
        ret = returns_by_platform.get(name, 0)
        channels.append({
            'name': name, 'units': row['units'], 'orders': row['orders'],
            'revenue': inr(row['revenue'], cur_sym), 'revenue_raw': round(row['revenue'], 2),
            'profit': inr(row['profit'], cur_sym),
            'aov': inr(row['revenue'] / row['orders'] if row['orders'] else 0, cur_sym),
            'share': round(row['revenue'] * 100 / tc['revenue'], 1) if tc['revenue'] else 0,
            'returns': ret,
            'return_pct': round(ret * 100.0 / (row['orders'] + ret), 1) if (row['orders'] + ret) else 0,
        })

    categories = [{'name': name or 'General', 'units': r['units'], 'revenue': inr(r['revenue'], cur_sym),
                   'share': round(r['revenue'] * 100 / tc['revenue'], 1) if tc['revenue'] else 0}
                  for name, r in sorted(by_category.items(), key=lambda kv: -kv[1]['revenue'])[:6]]

    boxes = [{'name': box_names[pk].name if pk in box_names else 'No box', 'units': u,
              'color': box_names[pk].color_tag if pk in box_names else '#64748b'}
             for pk, u in units_by_box.most_common(6)]
    box_max = max([b['units'] for b in boxes] + [1])
    for b in boxes:
        b['pct'] = max(4, round(b['units'] * 100 / box_max))

    # ---- heatmap ----
    heat_max = max(max(r) for r in heat) or 1
    heat_rows = [{'day': WEEKDAYS[i], 'cells': [{'n': n, 'a': round(0.08 + 0.92 * (n / heat_max) ** 0.7, 2) if n else 0}
                                                for n in row]} for i, row in enumerate(heat)]
    flat = [(n, d, h) for d, row in enumerate(heat) for h, n in enumerate(row)]
    peak = max(flat) if flat and max(flat)[0] else None
    peak_label = f"{WEEKDAYS[peak[1]]} {peak[2]:02d}:00-{(peak[2] + 1) % 24:02d}:00" if peak else ''
    day_totals = [sum(r) for r in heat]
    best_weekday = WEEKDAYS[day_totals.index(max(day_totals))] if any(day_totals) else ''
    best_day = max(daily.items(), key=lambda kv: kv[1]['revenue'])
    best_day_label = (f"{best_day[0].strftime('%d %b')} ({inr(best_day[1]['revenue'], cur_sym)})"
                      if best_day[1]['revenue'] else '')

    # ---- today vs yesterday ----
    def day_total(d):
        rows = [l for l in logs if timezone.localtime(l.dispatched_at).date() == d]
        return {'orders': len(rows), 'units': sum(l.quantity for l in rows),
                'revenue': sum(_f(l.sold_price) * l.quantity for l in rows)}
    t_today, t_yest = day_total(today), day_total(today - timedelta(days=1))

    # ---- action centre ----
    pending_qs = MarketplaceOrder.objects.filter(status=MarketplaceOrder.STATUS_PENDING)
    pending_total = pending_qs.count()
    pending_unmatched = pending_qs.filter(sku__isnull=True).count()
    low_packaging = list(PackagingMaterial.objects.filter(stock_quantity__gt=0, stock_quantity__lte=threshold)
                         .order_by('stock_quantity')[:4])

    # ---- top states (needs MarketplaceOrder.state from the analytics package) ----
    top_states = []
    states_enabled = _has_field(MarketplaceOrder, 'state')
    if states_enabled:
        counter = Counter()
        for st, q in MarketplaceOrder.objects.filter(status=MarketplaceOrder.STATUS_DISPATCHED,
                                                     dispatched_at__gte=start).exclude(state='').values_list('state', 'quantity'):
            counter[st] += q
        total_state_units = sum(counter.values()) or 1
        top_states = [{'name': n, 'units': u, 'pct': round(u * 100 / total_state_units, 1)} for n, u in counter.most_common(6)]

    # ---- KPI tiles ----
    rev_series = [daily[d]['revenue'] for d in day_list]
    unit_series = [daily[d]['units'] for d in day_list]
    ord_series = [daily[d]['orders'] for d in day_list]
    aov_series = [daily[d]['revenue'] / daily[d]['orders'] if daily[d]['orders'] else 0 for d in day_list]
    margin = round(tc['profit'] * 100 / tc['revenue'], 1) if tc['revenue'] else 0
    kpis = [
        {'label': 'Revenue', 'icon': 'fa-indian-rupee-sign', 'color': 'emerald', 'value': inr(tc['revenue'], cur_sym),
         'delta': _delta(tc['revenue'], tp['revenue']), 'spark': _spark(rev_series), 'sub': f"prev {inr(tp['revenue'], cur_sym)}"},
        {'label': 'Units dispatched', 'icon': 'fa-cubes', 'color': 'amber', 'value': f"{tc['units']:,}",
         'delta': _delta(tc['units'], tp['units']), 'spark': _spark(unit_series), 'sub': f"prev {tp['units']:,}"},
        {'label': 'Orders', 'icon': 'fa-truck-fast', 'color': 'blue', 'value': f"{tc['orders']:,}",
         'delta': _delta(tc['orders'], tp['orders']), 'spark': _spark(ord_series), 'sub': f"prev {tp['orders']:,}"},
        {'label': 'Avg order value', 'icon': 'fa-receipt', 'color': 'teal', 'value': inr(aov_c, cur_sym),
         'delta': _delta(aov_c, aov_p), 'spark': _spark(aov_series), 'sub': f"prev {inr(aov_p, cur_sym)}"},
        {'label': 'Est. profit', 'icon': 'fa-sack-dollar', 'color': 'violet', 'value': inr(tc['profit'], cur_sym),
         'delta': _delta(tc['profit'], tp['profit']), 'spark': '', 'sub': f"{margin}% margin (before fees)"},
        {'label': 'Return rate', 'icon': 'fa-rotate-left', 'color': 'rose', 'value': f"{rto_rate}%",
         'delta': None, 'spark': '', 'sub': f"{returns_total} returned / {rto_base} orders", 'invert': True},
        {'label': 'Stock value (cost)', 'icon': 'fa-warehouse', 'color': 'indigo', 'value': inr(cost_value, cur_sym),
         'delta': None, 'spark': '', 'sub': f"retail {inr(retail_value, cur_sym)}"},
        {'label': 'Units in stock', 'icon': 'fa-boxes-stacked', 'color': 'sky', 'value': f"{total_stock:,}",
         'delta': None, 'spark': '', 'sub': f"{len(listed)} listed products, {len(box_names)} bins"},
    ]

    return {
        'days': days, 'periods': PERIODS, 'currency': cur_sym, 'threshold': threshold,
        'kpis': kpis,
        'today': {'orders': t_today['orders'], 'units': t_today['units'], 'revenue': inr(t_today['revenue'], cur_sym),
                  'orders_delta': t_today['orders'] - t_yest['orders'], 'yest_orders': t_yest['orders'],
                  'yest_revenue': inr(t_yest['revenue'], cur_sym)},
        'chart': {
            'labels': [d.strftime('%d %b') for d in day_list],
            'units': unit_series, 'revenue': [round(v, 2) for v in rev_series],
            'channels': [{'name': c['name'], 'value': c['revenue_raw']} for c in channels],
            'stock': {'healthy': healthy, 'low': len(low_stock), 'out': len(out_of_stock)},
        },
        'channels': channels, 'top_products': top_products, 'categories': categories,
        'heat_rows': heat_rows, 'heat_hours': list(range(24)), 'peak_label': peak_label,
        'best_weekday': best_weekday, 'best_day_label': best_day_label,
        'stock_health': {'healthy': healthy, 'low': len(low_stock), 'out': len(out_of_stock), 'held_units': held_units,
                         'cost': inr(cost_value, cur_sym), 'retail': inr(retail_value, cur_sym),
                         'potential': inr(retail_value - cost_value, cur_sym)},
        'low_stock_items': sorted(low_stock + out_of_stock, key=lambda s: s.stock)[:8],
        'dead_stock': dead, 'dead_value': inr(dead_value, cur_sym), 'cover': cover, 'boxes': boxes,
        'pending_total': pending_total, 'pending_unmatched': pending_unmatched, 'low_packaging': low_packaging,
        'pending_orders': list(pending_qs.select_related('sku').order_by('-created_at')[:5]),
        'recent_dispatches': DispatchLog.objects.select_related('sku').order_by('-dispatched_at')[:10],
        'top_states': top_states, 'states_enabled': states_enabled,
        'total_products': len(skus), 'total_stock': total_stock, 'total_boxes': len(box_names),
    }