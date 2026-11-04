"""Complete sample files for every import: all columns, realistic rows, and a 'How to fill' sheet.
Built from the same field list the mapping page uses, so the sample can never drift from the importer."""
import csv
import io

from sku_manager.models import JewelrySKU, Platform

from .kinds import CATEGORIES, KINDS

# what to type in each column (shown on the "How to fill" sheet)
GUIDE = {
    # products
    'name': ('The product name as you want it shown.', 'Gold Bangle Set'),
    'category': ('A category code or its name. Codes: ' + ', '.join(c for c, _ in CATEGORIES) + '.', 'BGL  or  Bangles'),
    'style': ('Optional. Blank = STD.', 'Antique'),
    'design_model': ('Optional design / model number.', 'DSN-12'),
    'color': ('Optional. Blank = BLK.', 'Gold'),
    'size': ('Optional. Blank = FREE.', '2.6'),
    'number': ('Optional serial number. Leave EMPTY to get the next free number automatically.', '(empty)'),
    'is_listed': ('yes or no. Blank = yes.', 'yes'),
    # inventory
    'sku': ('The SKU ID of an existing product. Use this OR the Product name column.', 'BGL-STD-GOLDBANGLESET-GOLD-26-001'),
    'product': ('Only when SKU ID is empty. The name must match exactly one product.', 'Heart Necklace'),
    'batch_no': ('Required. The batch number from your supplier bill. Never created for you.', 'B-2411-01'),
    'quantity': ('Required. Units received, a whole number of 1 or more. Never defaulted.', '50'),
    'purchase_price': ('Buying price per unit. Blank = the product\'s latest buying price.', '110'),
    'wholesale_price': ('Wholesale price per unit. Blank = the product\'s latest price.', '140'),
    'selling_price': ('Reselling price per unit. Blank = the product\'s latest price.', '190'),
    'retail_price': ('Retail price per unit. Blank = the product\'s latest retail price.', '249'),
    'supplier': ('Supplier name. Must exist already, or tick "Add new suppliers" on the import page.', 'Shree Traders'),
    'storage_box': ('Storage box name. Must exist already, or tick "Create new boxes" on the import page.', 'BOX-A1'),
    'section_name': ('Section / compartment inside the box.', 'Shelf 2'),
    # suppliers
    'contact_person': ('Who you talk to.', 'Ramesh'),
    'phone': ('Mobile number, typed as text so a leading 0 is kept.', '9876543210'),
    'alt_phone': ('A second number or WhatsApp number.', '9123456780'),
    'email': ('Email address.', 'ramesh@example.com'),
    'gstin': ('15-character GST number.', '24ABCDE1234F1Z5'),
    'address': ('Street / shop address.', '12, Textile Market'),
    'city': ('City.', 'Surat'),
    'state': ('State.', 'Gujarat'),
    'notes': ('Anything you want to remember.', 'Pays in 15 days'),
}
TEXT_KEYS = {'number', 'phone', 'alt_phone', 'gstin', 'batch_no', 'sku', 'size'}   # keep as text (leading zeros, 2.60)

PRODUCT_ROWS = [  # name, category, style, design, colour, size, serial, listed, base price
    ('Gold Bangle Set', 'Bangles', 'Antique', 'DSN-12', 'Gold', '2.6', '', 'yes', 190),
    ('Kundan Kada', 'BGL', 'Kundan', 'DSN-14', 'Gold', '2.4', '', 'yes', 260),
    ('Oxidised Bracelet', 'Bracelets', '', 'OX-03', 'Silver', 'Free Size', '', 'yes', 150),
    ('Heart Necklace', 'NCK', '', '', 'Silver', 'Free Size', '', 'yes', 320),
    ('Pearl Choker', 'Necklaces', 'Pearl', 'PC-07', 'White', 'Free Size', '', 'yes', 410),
    ('Stud Earrings', 'EAR', '', 'ST-21', 'Gold', 'Free Size', '', 'yes', 120),
    ('Meenakari Jhumka', 'Jhumkas', 'Meenakari', 'MJ-05', 'Multi', 'Free Size', '', 'yes', 280),
    ('Bridal Set', 'SET', 'Bridal', 'BR-01', 'Red', 'Free Size', '', 'yes', 1450),
    ('Adjustable Ring', 'RNG', '', 'AR-09', 'Gold', 'Free Size', '', 'yes', 90),
    ('Silver Payal', 'Anklets', '', 'PY-02', 'Silver', 'Free Size', '', 'yes', 220),
    ('Maang Tikka', 'TIK', 'Kundan', 'MT-04', 'Gold', 'Free Size', '', 'no', 180),
    ('Old Design Kada', 'BGL', '', 'DSN-01', 'Black', '2.8', '120', 'yes', 130),   # explicit serial
]
SUPPLIER_ROWS = [
    ('Shree Traders', 'Ramesh Patel', '9876543210', '9123456780', 'ramesh@example.com', '24ABCDE1234F1Z5', '12, Textile Market', 'Surat', 'Gujarat', 'Pays in 15 days'),
    ('Mahalaxmi Imitation', 'Suresh Shah', '9123456780', '', '', '', 'Sonivad Road', 'Rajkot', 'Gujarat', ''),
    ('Jai Ambe Jewels', 'Kiran Mehta', '9898012345', '9898054321', 'kiran@jaiambe.example', '24AAAPM0001A1Z9', 'Opera House', 'Ahmedabad', 'Gujarat', 'Bangles and kadas'),
    ('Rajasthan Fashion Hub', 'Mohan Lal', '9414011223', '', '', '08AABCR1234C1ZP', 'Johri Bazaar', 'Jaipur', 'Rajasthan', 'Kundan work, ask for new catalogue'),
    ('Dilli Gift House', 'Amit Gupta', '9810055667', '', 'amit@example.com', '', 'Sadar Bazaar', 'Delhi', 'Delhi', ''),
]
INVENTORY_TEMPLATE = [  # which key is filled for each example row
    dict(use='sku', batch='B-2411-01', qty='50', p='110', w='140', s='190', r='249', sup='Shree Traders', box='BOX-A1', sec='Shelf 1'),
    dict(use='sku', batch='B-2411-02', qty='30', p='108', w='138', s='188', r='245', sup='Mahalaxmi Imitation', box='BOX-A1', sec='Shelf 2'),
    dict(use='sku', batch='B-2411-03', qty='100', p='60', w='85', s='120', r='159', sup='Jai Ambe Jewels', box='BOX-B2', sec='Shelf 1'),
    dict(use='name', batch='B-2411-04', qty='25', p='200', w='260', s='320', r='399', sup='Rajasthan Fashion Hub', box='BOX-B2', sec='Shelf 3'),
    dict(use='sku', batch='B-2411-05', qty='40', p='', w='', s='', r='', sup='', box='', sec=''),   # prices/supplier blank = reuse product's
    dict(use='sku', batch='B-2411-06', qty='12', p='95', w='130', s='175', r='229', sup='Dilli Gift House', box='', sec=''),
]
DEMO_SKUS = ['BGL-STD-GOLDBANGLESET-GOLD-26-001', 'NCK-STD-HEARTNECKLACE-SILVER-FREE-002', 'EAR-STD-STUDEARRINGS-GOLD-FREE-003',
             'SET-BRIDAL-BRIDALSET-RED-FREE-004', 'RNG-STD-ADJUSTABLERING-GOLD-FREE-005', 'PAY-STD-SILVERPAYAL-SILVER-FREE-006']


def _platform_value(field, idx, base):
    """Marketplace columns: a SKU like MEE-012 or a rate close to the base price (every 4th left blank to show it is optional)."""
    kind, pk = field.key.split('::')
    pf = _platforms()[int(pk)]
    if (idx + int(pk)) % 4 == 3:
        return ''
    if kind == 'listing':
        return f'{pf[:3].upper()}-{idx + 11:03d}'
    return str(base + (int(pk) % 3) * 10)


_cache = {}


def _platforms():
    return {p.pk: p.name for p in Platform.objects.all()}


def build(kind):
    """-> (fields, headers, rows, guide_rows)"""
    spec = KINDS[kind]
    fields = spec['fields']()
    rows = []
    if kind == 'products':
        for i, (name, cat, style, design, color, size, serial, listed, base) in enumerate(PRODUCT_ROWS):
            vals = {'name': name, 'category': cat, 'style': style, 'design_model': design, 'color': color,
                    'size': size, 'number': serial, 'is_listed': listed}
            rows.append([vals[f.key] if f.key in vals else _platform_value(f, i, base) for f in fields])
    elif kind == 'suppliers':
        keys = [f.key for f in fields]
        order = ['name', 'contact_person', 'phone', 'alt_phone', 'email', 'gstin', 'address', 'city', 'state', 'notes']
        for r in SUPPLIER_ROWS:
            d = dict(zip(order, r))
            rows.append([d.get(k, '') for k in keys])
    else:  # inventory
        real = list(JewelrySKU.objects.order_by('pk').values_list('sku', 'name')[:6])
        pool = real if len(real) >= 3 else [(s, '') for s in DEMO_SKUS]
        for i, t in enumerate(INVENTORY_TEMPLATE):
            sku, pname = pool[i % len(pool)]
            d = {'sku': sku if t['use'] == 'sku' else '', 'product': (pname or 'Heart Necklace') if t['use'] == 'name' else '',
                 'batch_no': t['batch'], 'quantity': t['qty'], 'purchase_price': t['p'], 'wholesale_price': t['w'],
                 'selling_price': t['s'], 'retail_price': t['r'], 'supplier': t['sup'], 'storage_box': t['box'], 'section_name': t['sec']}
            rows.append([d.get(f.key, '') for f in fields])
    guide = []
    for f in fields:
        if f.key.startswith('listing::'):
            what, ex = 'Optional. The SKU you use on this marketplace for this product.', rows[0][fields.index(f)] or 'MEE-011'
        elif f.key.startswith('price::'):
            what, ex = 'Optional. Selling rate on this marketplace.', rows[0][fields.index(f)] or '199'
        else:
            what, ex = GUIDE.get(f.key, (f.help or '', ''))
        guide.append((f.label, 'Required' if f.required else 'Optional', what, ex))
    if kind == 'inventory':
        guide.insert(0, ('SKU ID or Product name', 'One of the two', 'Every row needs the SKU ID, or the exact product name when the SKU ID is empty.', ''))
    return fields, [f.label for f in fields], rows, guide


def as_csv(kind):
    fields, headers, rows, _ = build(kind)
    out = io.StringIO()
    out.write('﻿')
    w = csv.writer(out)
    w.writerow(headers)
    w.writerows(rows)
    return out.getvalue()


def as_xlsx(kind):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.datavalidation import DataValidation

    fields, headers, rows, guide = build(kind)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Data'                                  # the importer reads the FIRST sheet
    req = PatternFill('solid', fgColor='F59E0B')
    opt = PatternFill('solid', fgColor='E5E7EB')
    ws.append(headers)
    for c, f in enumerate(fields, 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = req if f.required else opt
        cell.font = Font(bold=True, color='1C1B19')
        cell.alignment = Alignment(vertical='center')
        ws.column_dimensions[get_column_letter(c)].width = max(14, min(30, len(f.label) + 6))
    for r in rows:
        ws.append(r)
    for c, f in enumerate(fields, 1):                  # text columns stay text
        if f.key in TEXT_KEYS:
            for r in range(2, len(rows) + 2):
                ws.cell(row=r, column=c).number_format = '@'
    ws.freeze_panes = 'A2'
    ws.row_dimensions[1].height = 24
    if kind == 'products':
        keys = [f.key for f in fields]
        dv = DataValidation(type='list', formula1='"yes,no"', allow_blank=True)
        ws.add_data_validation(dv)
        col = get_column_letter(keys.index('is_listed') + 1)
        dv.add(f'{col}2:{col}2000')

    g = wb.create_sheet('How to fill')
    g.append(['Column', 'Required?', 'What to type', 'Example'])
    for c in range(1, 5):
        g.cell(row=1, column=c).font = Font(bold=True)
        g.cell(row=1, column=c).fill = req
    for row in guide:
        g.append(list(row))
    for col, w in zip('ABCD', (26, 16, 80, 36)):
        g.column_dimensions[col].width = w
    for r in g.iter_rows(min_row=2):
        for cell in r:
            cell.alignment = Alignment(wrap_text=True, vertical='top')
    n = g.max_row + 2
    notes = ['Amber column titles are required, grey ones are optional.',
             'Delete the example rows in the Data sheet and type your own. Keep the first row (the titles).',
             'You can rename, reorder or delete optional columns. The next step lets you match any column to a field.',
             'Nothing is saved until you press Import on the preview page.']
    if kind == 'products':
        notes.insert(0, 'The SKU ID is created automatically for every product, you do not type it.')
    if kind == 'inventory':
        notes.insert(0, 'Import the products first. The example SKU IDs are real ones from your database when you have products.')
    for i, t in enumerate(notes):
        g.cell(row=n + i, column=1, value=t).font = Font(italic=True)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
