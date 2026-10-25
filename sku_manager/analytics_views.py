# sku_manager/analytics_views.py
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, Http404

from .analytics import build_payload
from .geo import CODE_TO_NAME
from .models import JewelrySKU, Platform

KINDS = ('products', 'customers')


def _int(value, default=None):
    value = (value or '').strip()
    return int(value) if value.isdigit() else default


@login_required(login_url='login')
def analytics_page(request, kind):
    if kind not in KINDS:
        raise Http404
    return render(request, 'sku_manager/analytics.html', {
        'kind': kind,
        'platforms': Platform.objects.order_by('name'),
        'skus': JewelrySKU.objects.order_by('name').values('pk', 'name', 'sku')[:500],
        'state_names': CODE_TO_NAME,
        'active_page': f'analytics_{kind}',
    })


@login_required(login_url='login')
def analytics_data(request, kind):
    if kind not in KINDS:
        raise Http404
    days = _int(request.GET.get('days'), 0)
    if days not in (0, 7, 30, 90, 365):
        days = 0
    payload = build_payload(
        kind,
        days=days,
        platform_id=_int(request.GET.get('platform')),
        sku_id=_int(request.GET.get('sku')) if kind == 'products' else None,
    )
    payload['days'] = days
    return JsonResponse(payload)
