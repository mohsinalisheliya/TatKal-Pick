"""Run an import (dry-run or for real) over every row of the uploaded file."""
from django.db import transaction

from .kinds import KINDS, RowError, next_serial_start
from .parsing import read_table


def guess_mapping(kind, headers):
    """Best guess: header text equals / contains one of the field's aliases."""
    from .kinds import norm
    spec = KINDS[kind]['fields']()
    normed = [norm(h) for h in headers]   
    used, out = set(), {}
    for f in spec:                                  # exact matches first
        for i, h in enumerate(normed):
            if i not in used and h and h in f.aliases:
                out[f.key] = {'col': i}
                used.add(i)
                break
    for f in spec:                                  # then "header contains alias"
        if f.key in out:
            continue
        for i, h in enumerate(normed):
            if i not in used and h and any(len(a) >= 4 and a in h for a in f.aliases):
                out[f.key] = {'col': i}
                used.add(i)
                break
    return out


def load(job):
    with job.file.open('rb') as fh:
        return read_table(fh, job.filename)


def run(job, commit):
    """Returns {'counts': {...}, 'rows': [{n, status, message}], 'aborted': bool}."""
    headers, rows = load(job)
    kind = KINDS[job.kind]
    fields = kind['fields']()
    mapping, options = job.mapping or {}, job.options or {}
    ctx = {'serial': next_serial_start(), 'touched': set()}
    counts = {'created': 0, 'updated': 0, 'skipped': 0, 'errors': 0}
    out, error_rows = [], []

    def value_of(f, row):
        m = mapping.get(f.key)
        if not m:
            return ''
        if 'fixed' in m:
            return str(m['fixed']).strip()
        idx = m.get('col')
        return row[idx].strip() if isinstance(idx, int) and 0 <= idx < len(row) else ''

    with transaction.atomic():
        for n, row in enumerate(rows, start=2):                       # row 1 is the header
            v = {f.key: value_of(f, row) for f in fields}
            try:
                for f in fields:
                    if f.required and not v[f.key]:
                        raise RowError(f'{f.label} is empty.')
                with transaction.atomic():                            # a bad row undoes only itself
                    status, msg = kind['handler'](v, options, ctx)
            except RowError as exc:
                status, msg = 'error', str(exc)
            except Exception as exc:                                  # never let one odd row kill the file
                status, msg = 'error', f'Unexpected problem: {exc.__class__.__name__}: {exc}'
            counts['errors' if status == 'error' else status] += 1
            out.append({'n': n, 'status': status, 'message': msg})
            if status == 'error':
                error_rows.append((n, row, msg))
        aborted = bool(options.get('strict')) and counts['errors'] > 0
        if commit and not aborted:
            from sku_manager.models import JewelrySKU
            for pk in ctx['touched']:
                sku = JewelrySKU.objects.filter(pk=pk).first()
                if sku:
                    sku.sync_stock_from_batches()
        if not commit or aborted:
            transaction.set_rollback(True)
    return {'counts': counts, 'rows': out, 'aborted': aborted, 'headers': headers, 'error_rows': error_rows,
            'total': len(rows)}
