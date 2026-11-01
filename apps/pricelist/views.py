"""Price Lists - every product's four prices on one page, editable, with Save buttons (Admin only)."""
import json
from decimal import Decimal, InvalidOperation

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from accounts import audit
from accounts.decorators import role_required
from sku_manager.models import JewelrySKU

admin_only = role_required('admin')
FIELDS = [('purchase_price', 'Buying'), ('wholesale_price', 'Wholesale'), ('selling_price', 'Reselling'), ('retail_price', 'Retail')]
PAGE = 50
MAX_ROWS = 200


@admin_only
def price_list(request):
    q = request.GET.get('q', '').strip()
    cat = request.GET.get('category', '').strip()
    missing = request.GET.get('missing') == '1'
    qs = JewelrySKU.objects.all().order_by('category', 'name', 'sku')
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(category__icontains=q) | Q(color__icontains=q))
    if cat:
        qs = qs.filter(category=cat)
    if missing:
        qs = qs.filter(Q(purchase_price=0) | Q(wholesale_price=0) | Q(selling_price=0) | Q(retail_price=0))
    page = Paginator(qs, PAGE).get_page(request.GET.get('page'))
    cats = JewelrySKU.objects.order_by('category').values_list('category', flat=True).distinct()
    keep = request.GET.copy(); keep.pop('page', None)
    return render(request, 'pricelist/list.html', {
        'page': page, 'q': q, 'category': cat, 'missing': missing, 'categories': [c for c in cats if c],
        'fields': FIELDS, 'qs': keep.urlencode(), 'total': page.paginator.count, 'active_page': 'price_list'})


def _money(raw, label):
    raw = ('' if raw is None else str(raw)).strip()
    if raw == '':
        return Decimal('0.00')
    try:
        val = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f'{label} must be a number')
    if val < 0 or val > Decimal('99999999'):
        raise ValueError(f'{label} is out of range')
    return val.quantize(Decimal('0.01'))


@admin_only
@require_POST
def price_save(request):
    """Body: {"rows": [{"id": 5, "purchase_price": "110", "wholesale_price": ..., "selling_price": ..., "retail_price": ...}]}
    A row with a bad value is reported and skipped; the others are saved."""
    try:
        rows = json.loads(request.body or b'{}').get('rows') or []
    except ValueError:
        return JsonResponse({'ok': False, 'message': 'Bad request.'}, status=400)
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        return JsonResponse({'ok': False, 'message': f'Send between 1 and {MAX_ROWS} rows.'}, status=400)

    saved, errors = [], []
    for row in rows:
        try:
            pk = int(row.get('id'))
            new = {f: _money(row.get(f), label + ' price') for f, label in FIELDS}
        except (TypeError, ValueError, AttributeError) as exc:
            errors.append({'id': row.get('id') if isinstance(row, dict) else None, 'message': str(exc) or 'Invalid row'})
            continue
        with transaction.atomic():
            sku = JewelrySKU.objects.select_for_update().filter(pk=pk).first()
            if not sku:
                errors.append({'id': pk, 'message': 'Product no longer exists'})
                continue
            old = {f: getattr(sku, f) for f, _ in FIELDS}
            if old == new:
                saved.append({'id': pk, 'changed': False, **{f: str(v) for f, v in new.items()}})
                continue
            for f, v in new.items():
                setattr(sku, f, v)
            sku.save(update_fields=[f for f, _ in FIELDS] + ['updated_at'])
            # the product shows the prices of its newest batch (see sync_stock_from_batches), so keep that batch in step
            latest = sku.batches.filter(status='active').order_by('-created_at').first() or sku.batches.order_by('-created_at').first()
            if latest:
                for f, v in new.items():
                    setattr(latest, f, v)
                latest.save(update_fields=[f for f, _ in FIELDS] + ['updated_at'])
            diff = ', '.join(f'{lab} {old[f]}->{new[f]}' for f, lab in FIELDS if old[f] != new[f])
            audit.log(request, 'price_update', f'{sku.sku}: {diff}')
            saved.append({'id': pk, 'changed': True, **{f: str(v) for f, v in new.items()}})
    n = sum(1 for s in saved if s['changed'])
    return JsonResponse({'ok': not errors, 'saved': saved, 'errors': errors, 'changed': n,
                         'message': f'{n} product{"s" if n != 1 else ""} updated' + (f', {len(errors)} not saved' if errors else '')})
