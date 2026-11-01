import json

from django.contrib import messages
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts import audit
from accounts.decorators import role_required
from sku_manager.models import StockBatch

from .forms import SupplierForm
from .models import Supplier

admin_only = role_required('admin')


@admin_only
def supplier_list(request):
    q = request.GET.get('q', '').strip()
    show = request.GET.get('show', 'active')
    qs = Supplier.objects.all()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(city__icontains=q)
                       | Q(contact_person__icontains=q) | Q(gstin__icontains=q))
    if show == 'active':
        qs = qs.filter(is_active=True)
    elif show == 'disabled':
        qs = qs.filter(is_active=False)
    qs = qs.annotate(batch_count=Count('batches', distinct=True), units=Sum('batches__quantity'))
    return render(request, 'suppliers/list.html', {
        'suppliers': qs, 'q': q, 'show': show, 'active_page': 'suppliers',
        'total': Supplier.objects.count()})


@admin_only
def supplier_form(request, pk=None):
    obj = get_object_or_404(Supplier, pk=pk) if pk else None
    form = SupplierForm(request.POST or None, instance=obj)
    if request.method == 'POST' and form.is_valid():
        saved = form.save()
        audit.log(request, 'supplier_save', f'{"Updated" if obj else "Added"} supplier "{saved.name}"')
        messages.success(request, f'Supplier "{saved.name}" saved.')
        return redirect('supplier_list')
    return render(request, 'suppliers/form.html', {'form': form, 'obj': obj, 'active_page': 'suppliers'})


@admin_only
def supplier_detail(request, pk):
    obj = get_object_or_404(Supplier, pk=pk)
    batches = (StockBatch.objects.filter(supplier=obj).select_related('sku').order_by('-created_at'))
    agg = batches.aggregate(units=Sum('quantity'), value=Sum(ExpressionWrapper(
        F('quantity') * F('purchase_price'), output_field=DecimalField(max_digits=14, decimal_places=2))))
    return render(request, 'suppliers/detail.html', {
        'obj': obj, 'batches': batches[:300], 'batch_total': batches.count(),
        'units': agg['units'] or 0, 'value': agg['value'] or 0, 'active_page': 'suppliers'})


@admin_only
@require_POST
def supplier_toggle(request, pk):
    obj = get_object_or_404(Supplier, pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=['is_active'])
    audit.log(request, 'supplier_save', f'{"Enabled" if obj.is_active else "Disabled"} supplier "{obj.name}"')
    messages.success(request, f'"{obj.name}" is now {"active" if obj.is_active else "disabled"}.')
    return redirect('supplier_list')


@admin_only
@require_POST
def supplier_delete(request, pk):
    obj = get_object_or_404(Supplier, pk=pk)
    if obj.batches.exists():
        messages.error(request, f'"{obj.name}" has stock batches, so it cannot be deleted. Disable it instead.')
    else:
        name = obj.name
        obj.delete()
        audit.log(request, 'supplier_save', f'Deleted supplier "{name}"')
        messages.success(request, f'Supplier "{name}" deleted.')
    return redirect('supplier_list')


# ---- JSON for the searchable dropdown on the stock form ----
@admin_only
def supplier_search_api(request):
    q = request.GET.get('q', '').strip()
    qs = Supplier.objects.filter(is_active=True)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(city__icontains=q) | Q(contact_person__icontains=q))
    rows = [{'id': s.pk, 'name': s.name, 'phone': s.phone, 'city': s.city} for s in qs.order_by('name')[:25]]
    exact = bool(q) and Supplier.objects.filter(name__iexact=q).exists()
    return JsonResponse({'results': rows, 'exact': exact})


@admin_only
@require_POST
def supplier_quick_add_api(request):
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except ValueError:
        body = request.POST
    name = ' '.join(str(body.get('name', '')).split())
    if not name:
        return JsonResponse({'ok': False, 'message': 'Type the supplier name first.'}, status=400)
    existing = Supplier.objects.filter(name__iexact=name).first()
    if existing:
        return JsonResponse({'ok': True, 'created': False, 'id': existing.pk, 'name': existing.name,
                             'phone': existing.phone, 'city': existing.city})
    s = Supplier.objects.create(name=name[:120], phone=str(body.get('phone', '')).strip()[:20])
    audit.log(request, 'supplier_save', f'Added supplier "{s.name}" from the stock form')
    return JsonResponse({'ok': True, 'created': True, 'id': s.pk, 'name': s.name, 'phone': s.phone, 'city': ''})
