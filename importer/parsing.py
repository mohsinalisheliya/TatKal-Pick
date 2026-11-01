"""Read an uploaded CSV / Excel file into (headers, rows) of plain strings."""
import csv
import io
from datetime import date, datetime
from decimal import Decimal

MAX_ROWS = 20000
MAX_BYTES = 8 * 1024 * 1024


class FileProblem(Exception):
    pass


def _cell(v):
    if v is None:
        return ''
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    if isinstance(v, float):
        return str(int(v)) if v == int(v) else repr(round(v, 6))
    if isinstance(v, (int, Decimal)):
        return str(v)
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S') if (v.hour or v.minute or v.second) else v.strftime('%Y-%m-%d')
    if isinstance(v, date):
        return v.strftime('%Y-%m-%d')
    return str(v).strip()


def _csv_rows(raw):
    for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=',;\t|')
    except csv.Error:
        dialect = csv.excel
    return [[_cell(c) for c in r] for r in csv.reader(io.StringIO(text), dialect)]


def _xlsx_rows(raw):
    from openpyxl import load_workbook
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise FileProblem('This Excel file could not be opened. Save it as .xlsx or .csv and try again.')
    ws = wb.worksheets[0]
    rows = []
    for r in ws.iter_rows(values_only=True):
        rows.append([_cell(c) for c in r])
        if len(rows) > MAX_ROWS + 5:
            break
    return rows


def read_table(fileobj, filename):
    raw = fileobj.read()
    if len(raw) > MAX_BYTES:
        raise FileProblem('The file is larger than 8 MB. Split it into smaller files.')
    name = (filename or '').lower()
    if name.endswith(('.xlsx', '.xlsm')):
        rows = _xlsx_rows(raw)
    elif name.endswith(('.csv', '.txt', '.tsv')):
        rows = _csv_rows(raw)
    else:
        raise FileProblem('Upload a .csv or .xlsx file.')
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise FileProblem('The file is empty.')
    headers = [h.strip() or f'Column {i + 1}' for i, h in enumerate(rows[0])]
    width = len(headers)
    body = [(r + [''] * width)[:width] for r in rows[1:]]
    if not body:
        raise FileProblem('The file has a header row but no data rows.')
    if len(body) > MAX_ROWS:
        raise FileProblem(f'The file has more than {MAX_ROWS} rows. Split it into smaller files.')
    return headers, body
