# sku_manager/analytics.py
"""State-wise product + customer analytics built from MarketplaceOrder rows.

Counted orders: dispatched (sales) and cancelled (returns / RTO). Pending orders
are ignored. Revenue = quantity x the unit price saved on the dispatch log.
"""
import re
from collections import Counter, defaultdict
from datetime import timedelta

from django.utils import timezone

from .geo import CODE_TO_NAME, STATE_TO_CODE
from .models import JewelrySKU, MarketplaceOrder


def _blank():
    return {
        'orders': 0, 'units': 0, 'revenue': 0.0, 'cost': 0.0, 'cancelled': 0,
        'platforms': Counter(),
        'products': defaultdict(lambda: {'units': 0, 'revenue': 0.0}),
        'pins': Counter(),
        'customers': Counter(),
        'spent': defaultdict(float),
        'monthly': defaultdict(lambda: {'orders': 0, 'revenue': 0.0, 'new_customers': 0, 'repeat_orders': 0}),
    }


def customer_key(order):
    """Marketplaces hide phone / email, so a customer is name + PIN code (approximate)."""
    name = re.sub(r'\s+', ' ', (order.customer_name or '').strip().lower())
    return f'{name}|{order.pincode}' if name else ''


def _pct(part, whole):
    return round(part * 100.0 / whole, 1) if whole else 0.0


def _finalize(bucket, meta, names, total_units=0):
    sold = bucket['orders']
    repeat = sum(1 for c in bucket['customers'].values() if c >= 2)
    customers = len(bucket['customers'])
    top_products = sorted(bucket['products'].items(), key=lambda kv: (-kv[1]['units'], -kv[1]['revenue']))[:6]
    top_customers = sorted(bucket['customers'].items(), key=lambda kv: (-kv[1], -bucket['spent'][kv[0]]))[:6]
    return {
        'orders': sold,
        'units': bucket['units'],
        'revenue': round(bucket['revenue'], 2),
        'profit': round(bucket['revenue'] - bucket['cost'], 2),
        'aov': round(bucket['revenue'] / sold, 2) if sold else 0,
        'cancelled': bucket['cancelled'],
        'rto_pct': _pct(bucket['cancelled'], sold + bucket['cancelled']),
        'customers': customers,
        'repeat_customers': repeat,
        'repeat_pct': _pct(repeat, customers),
        'share_pct': _pct(bucket['units'], total_units),
        'platforms': [{'name': n, 'orders': c} for n, c in bucket['platforms'].most_common()],
        'top_products': [
            {'name': meta.get(pid, {}).get('name', '?'), 'sku': meta.get(pid, {}).get('sku', ''),
             'units': v['units'], 'revenue': round(v['revenue'], 2)}
            for pid, v in top_products
        ],
        'top_pincodes': [{'pin': p, 'orders': c} for p, c in bucket['pins'].most_common(5)],
        'top_customers': [
            {'name': names.get(k, k.split('|')[0].title()), 'orders': c, 'spent': round(bucket['spent'][k], 2)}
            for k, c in top_customers if k
        ],
        'monthly': [{'month': m, **{k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}}
                    for m, d in sorted(bucket['monthly'].items())[-12:]],
    }


def build_payload(kind, days=0, platform_id=None, sku_id=None):
    qs = (MarketplaceOrder.objects
          .filter(status__in=[MarketplaceOrder.STATUS_DISPATCHED, MarketplaceOrder.STATUS_CANCELLED])
          .select_related('sku', 'dispatch_log')
          .order_by('created_at', 'pk'))
    if days:
        qs = qs.filter(created_at__gte=timezone.now() - timedelta(days=days))
    if platform_id:
        qs = qs.filter(platform_id=platform_id)
    if sku_id:
        qs = qs.filter(sku_id=sku_id)

    total = _blank()
    by_code = defaultdict(_blank)
    meta, names = {}, {}
    seen_customers = set()
    located = unlocated = 0
    sold_sku_ids = set()

    for o in qs:
        code = STATE_TO_CODE.get(o.state, '')
        buckets = [total] + ([by_code[code]] if code else [])
        if code:
            located += 1
        else:
            unlocated += 1

        if o.status == MarketplaceOrder.STATUS_CANCELLED:
            for b in buckets:
                b['cancelled'] += 1
            continue

        unit_price = float(o.dispatch_log.sold_price) if o.dispatch_log_id else 0.0
        revenue = unit_price * o.quantity
        cost = float(o.sku.purchase_price) * o.quantity if o.sku_id else 0.0
        key = customer_key(o)
        month = o.created_at.strftime('%Y-%m')
        if key and key not in names:
            names[key] = o.customer_name.strip()
        is_new = bool(key) and key not in seen_customers
        if key:
            seen_customers.add(key)
        if o.sku_id:
            meta[o.sku_id] = {'name': o.sku.name, 'sku': o.sku.sku}
            sold_sku_ids.add(o.sku_id)

        for b in buckets:
            b['orders'] += 1
            b['units'] += o.quantity
            b['revenue'] += revenue
            b['cost'] += cost
            b['platforms'][o.platform_name] += 1
            if o.sku_id:
                b['products'][o.sku_id]['units'] += o.quantity
                b['products'][o.sku_id]['revenue'] += revenue
            if o.pincode:
                b['pins'][o.pincode] += 1
            if key:
                b['customers'][key] += 1
                b['spent'][key] += revenue
            m = b['monthly'][month]
            m['orders'] += 1
            m['revenue'] += revenue
        # new-vs-repeat is a whole-business number, kept on the total bucket only
        if key:
            total['monthly'][month]['new_customers' if is_new else 'repeat_orders'] += 1

    total_units = total['units']
    states = {code: dict(_finalize(b, meta, names, total_units), name=CODE_TO_NAME[code], code=code)
              for code, b in by_code.items()}
    overall = _finalize(total, meta, names, total_units)

    payload = {
        'kind': kind,
        'totals': overall,
        'states': states,
        'coverage': {'located': located, 'unlocated': unlocated},
        'platform_mix': overall['platforms'],
        'monthly': overall['monthly'],
        'insights': _insights(kind, states, overall),
    }

    if kind == 'products':
        top_all = sorted(total['products'].items(), key=lambda kv: (-kv[1]['units'], -kv[1]['revenue']))[:10]
        payload['top_products'] = [
            {'name': meta[pid]['name'], 'sku': meta[pid]['sku'], 'units': v['units'], 'revenue': round(v['revenue'], 2)}
            for pid, v in top_all
        ]
        stock_rows = {s.pk: s for s in JewelrySKU.objects.filter(is_listed=True)}
        if not sku_id:
            payload['slow_movers'] = [
                {'name': s.name, 'sku': s.sku, 'stock': s.stock}
                for s in sorted((s for pk, s in stock_rows.items() if s.stock > 0 and pk not in sold_sku_ids),
                                key=lambda s: -s.stock)[:8]
            ]
        else:
            payload['slow_movers'] = []
        if days:
            cover = []
            for pid, v in total['products'].items():
                item = stock_rows.get(pid)
                per_day = v['units'] / days
                if item and per_day > 0:
                    cover.append({'name': item.name, 'sku': item.sku, 'stock': item.stock,
                                  'per_day': round(per_day, 2), 'cover_days': round(item.stock / per_day, 1)})
            payload['stock_cover'] = sorted(cover, key=lambda r: r['cover_days'])[:8]
        else:
            payload['stock_cover'] = []
    else:
        payload['top_customers'] = overall['top_customers']
    return payload


def _insights(kind, states, overall):
    tips = []
    if not states:
        return ['No orders with a detectable state yet. Scan label PDFs and run the backfill command.']
    if kind == 'customers':
        top = max(states.values(), key=lambda s: s['customers'])
        share = round(top['customers'] * 100.0 / overall['customers'], 1) if overall['customers'] else 0
        tips.append(f"{top['name']} has the most customers: {share}% ({top['customers']}).")
    else:
        top = max(states.values(), key=lambda s: s['units'])
        tips.append(f"{top['name']} is your biggest market: {top['share_pct']}% of units ({top['units']}).")
    risky = [s for s in states.values() if s['orders'] + s['cancelled'] >= 5 and s['cancelled']]
    if risky:
        worst = max(risky, key=lambda s: s['rto_pct'])
        tips.append(f"{worst['name']} has the highest returns: {worst['rto_pct']}% "
                    f"({worst['cancelled']} of {worst['orders'] + worst['cancelled']} orders).")
    rich = [s for s in states.values() if s['orders'] >= 3]
    if rich:
        best = max(rich, key=lambda s: s['aov'])
        tips.append(f"Highest average order value: {best['name']} at ₹{best['aov']:.0f}.")
    empty = len(CODE_TO_NAME) - len(states)
    if empty > 0:
        tips.append(f"No orders yet from {empty} states / UTs: a chance to grow with marketplace ads.")
    if kind == 'customers' and overall['customers']:
        tips.append(f"{overall['repeat_pct']}% of identified customers ordered more than once.")
        loyal = [s for s in states.values() if s['customers'] >= 3 and s['repeat_customers']]
        if loyal:
            best = max(loyal, key=lambda s: s['repeat_pct'])
            tips.append(f"Most loyal state: {best['name']} ({best['repeat_pct']}% repeat customers).")
    return tips
