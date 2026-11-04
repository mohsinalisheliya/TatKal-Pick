import csv
import json

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts import audit
from accounts.decorators import role_required

from . import engine, samples
from .kinds import KINDS
from .models import ImportJob, ImportMapping
from .parsing import FileProblem, read_table

admin_only = role_required('admin')


def _kind(kind):
    if kind not in KINDS:
        raise Http404
    return KINDS[kind]


@admin_only
def import_center(request):
    cards = [{'key': k, **v} for k, v in KINDS.items()]
    return render(request, 'importer/center.html', {
        'cards': cards, 'jobs': ImportJob.objects.all()[:15], 'saved': ImportMapping.objects.all(),
        'active_page': 'import'})


@admin_only
def import_start(request, kind):
    spec = _kind(kind)
    if request.method == 'POST':
        up = request.FILES.get('file')
        if not up:
            messages.error(request, 'Choose a .csv or .xlsx file.')
        else:
            try:
                headers, rows = read_table(up, up.name)
                up.seek(0)
            except FileProblem as exc:
                messages.error(request, str(exc))
            else:
                job = ImportJob.objects.create(kind=kind, filename=up.name[:200], file=up, created_by=request.user)
                return redirect('import_map', pk=job.pk)
    return render(request, 'importer/upload.html', {'kind': kind, 'spec': spec, 'active_page': 'import'})


def _job(pk):
    return get_object_or_404(ImportJob, pk=pk)


@admin_only
def import_map(request, pk):
    job = _job(pk)
    spec = KINDS[job.kind]
    try:
        headers, rows = engine.load(job)
    except Exception as exc:
        messages.error(request, f'Could not read the file again: {exc}')
        return redirect('import_center')
    fields = spec['fields']()

    if request.method == 'POST':
        mapping = {}
        for f in fields:
            choice = request.POST.get(f'map_{f.key}', '')
            if choice == '__fixed__':
                fixed = request.POST.get(f'fixed_{f.key}', '').strip()
                if fixed:
                    mapping[f.key] = {'fixed': fixed}
            elif choice.isdigit() and int(choice) < len(headers):
                mapping[f.key] = {'col': int(choice)}
        missing = [f.label for f in fields if f.required and f.key not in mapping]
        if job.kind == 'inventory' and 'sku' not in mapping and 'product' not in mapping:
            missing.append('SKU ID or Product name')
        if missing:
            messages.error(request, 'Map these required fields: ' + ', '.join(missing) + '.')
        else:
            job.mapping = mapping
            job.options = {'on_duplicate': request.POST.get('on_duplicate', 'skip'),
                           'create_suppliers': request.POST.get('create_suppliers') == 'on',
                           'create_boxes': request.POST.get('create_boxes') == 'on',
                           'strict': request.POST.get('strict') == 'on'}
            job.status = 'mapped'
            job.save()
            name = request.POST.get('save_as', '').strip()[:80]
            if name:
                by_header = {k: ({'col': headers[m['col']]} if 'col' in m else m) for k, m in mapping.items()}
                ImportMapping.objects.update_or_create(kind=job.kind, name=name, defaults={'mapping': by_header})
                messages.success(request, f'Mapping saved as "{name}".')
            return redirect('import_preview', pk=job.pk)
        current = {f.key: ({'fixed': request.POST.get(f'fixed_{f.key}', '')} if request.POST.get(f'map_{f.key}') == '__fixed__'
                           else {'col': int(request.POST[f'map_{f.key}'])} if request.POST.get(f'map_{f.key}', '').isdigit() else None)
                   for f in fields}
        current = {k: m for k, m in current.items() if m}
    else:
        current = {}
        saved_id = request.GET.get('use', '')
        saved = ImportMapping.objects.filter(pk=saved_id, kind=job.kind).first() if saved_id.isdigit() else None
        if saved:
            lower = [h.strip().lower() for h in headers]
            for key, m in saved.mapping.items():
                if 'col' in m and m['col'].strip().lower() in lower:
                    current[key] = {'col': lower.index(m['col'].strip().lower())}
                elif 'fixed' in m:
                    current[key] = m
        elif job.mapping:
            current = job.mapping
        if not current:
            current = engine.guess_mapping(job.kind, headers)

    sample = rows[:5]
    cols = []
    for i, h in enumerate(headers):
        cols.append({'i': i, 'name': h, 'examples': ' · '.join(r[i] for r in sample if r[i])[:70]})
    rows_ui = []
    for f in fields:
        m = current.get(f.key) or {}
        rows_ui.append({'f': f, 'col': m.get('col'), 'fixed': m.get('fixed', ''), 'is_fixed': 'fixed' in m})
    groups = []
    for r in rows_ui:
        g = r['f'].group
        if not groups or groups[-1]['name'] != g:
            groups.append({'name': g, 'rows': []})
        groups[-1]['rows'].append(r)
    return render(request, 'importer/map.html', {
        'job': job, 'spec': spec, 'headers': headers, 'cols': cols, 'sample': sample, 'groups': groups,
        'total': len(rows), 'saved': ImportMapping.objects.filter(kind=job.kind), 'opts': job.options or {},
        'options': spec['options'], 'active_page': 'import'})


@admin_only
def import_preview(request, pk):
    job = _job(pk)
    if not job.mapping:
        return redirect('import_map', pk=job.pk)
    res = engine.run(job, commit=False)
    shown = [r for r in res['rows'] if r['status'] == 'error'][:60] + [r for r in res['rows'] if r['status'] != 'error'][:40]
    return render(request, 'importer/preview.html', {
        'job': job, 'spec': KINDS[job.kind], 'counts': res['counts'], 'rows': shown, 'total': res['total'],
        'would_abort': bool((job.options or {}).get('strict')) and res['counts']['errors'] > 0,
        'active_page': 'import'})


@admin_only
@require_POST
def import_commit(request, pk):
    job = _job(pk)
    if job.status == 'done':
        return redirect('import_result', pk=job.pk)
    res = engine.run(job, commit=True)
    job.result = {'counts': res['counts'], 'aborted': res['aborted'], 'total': res['total'],
                  'rows': [r for r in res['rows'] if r['status'] in ('error', 'skipped')][:500],
                  'errors': [{'n': n, 'row': row, 'message': msg} for n, row, msg in res['error_rows']][:2000],
                  'headers': res['headers']}
    job.status = 'failed' if res['aborted'] else 'done'
    job.finished_at = timezone.now()
    job.save()
    c = res['counts']
    audit.log(request, 'import', f'{KINDS[job.kind]["title"]} import "{job.filename}": {c["created"]} created, '
                                 f'{c["updated"]} updated, {c["skipped"]} skipped, {c["errors"]} errors'
                                 + (' (cancelled: strict mode)' if res['aborted'] else ''))
    return redirect('import_result', pk=job.pk)


@admin_only
def import_result(request, pk):
    job = _job(pk)
    after = KINDS[job.kind]['after']
    return render(request, 'importer/result.html', {
        'job': job, 'spec': KINDS[job.kind], 'res': job.result or {}, 'after_url': reverse(after), 'active_page': 'import'})


@admin_only
def import_errors_csv(request, pk):
    job = _job(pk)
    res = job.result or {}
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="import-{job.pk}-errors.csv"'
    resp.write('﻿')
    w = csv.writer(resp)
    w.writerow(['Row'] + res.get('headers', []) + ['Problem'])
    for e in res.get('errors', []):
        w.writerow([e['n']] + e['row'] + [e['message']])
    return resp


@admin_only
def import_sample(request, kind):
    """A complete sample: every column of the importer, example rows, and a 'How to fill' sheet. ?format=csv for CSV."""
    _kind(kind)
    if request.GET.get('format') == 'csv':
        resp = HttpResponse(samples.as_csv(kind), content_type='text/csv; charset=utf-8')
        resp['Content-Disposition'] = f'attachment; filename="sample-{kind}.csv"'
        return resp
    resp = HttpResponse(samples.as_xlsx(kind), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="sample-{kind}.xlsx"'
    return resp


@admin_only
@require_POST
def import_mapping_delete(request, pk):
    ImportMapping.objects.filter(pk=pk).delete()
    messages.success(request, 'Saved mapping deleted.')
    return redirect('import_center')