"""What can be imported, which fields each import has, and how one row becomes database rows.

A row handler gets `v` (dict field_key -> cleaned text or '') and returns (status, message)
with status in created / updated / skipped, or raises RowError to reject the row.
"""
import re
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError

from sku_manager.models import (JewelrySKU, MarketplaceListingSKU, Platform, PlatformPrice, StockBatch, StorageBox)
from suppliers.models import Supplier

CATEGORIES = [
    ('BGL', 'Bangles / Kadas'), ('BRC', 'Bracelets'), ('NCK', 'Necklaces / Chains / Chokers'),
    ('EAR', 'Earrings'), ('SET', 'Jewelry Sets / Combos'), ('JHK', 'Jhumkas'), ('RNG', 'Rings'),
    ('PAY', 'Anklets / Payal'), ('TIK', 'Maang Tikka'),
]


class RowError(Exception):
    pass


class F:
    def __init__(self, key, label, required=False, help='', aliases=(), kind='text', group=''):
        self.key, self.label, self.required, self.help = key, label, required, help
        self.aliases, self.kind, self.group = [norm(a) for a in aliases] + [norm(label), norm(key)], kind, group


def norm(text):
    return re.sub(r'[^a-z0-9]', '', str(text).lower())


# ------------------------------------------------------------------ helpers
def to_int(text, label, minimum=0):
    try:
        d = Decimal(text.replace(',', ''))
    except InvalidOperation:
        raise RowError(f'{label} "{text}" is not a number.')
    if d != d.to_integral_value():
        raise RowError(f'{label} must be a whole number, got "{text}".')
    if int(d) < minimum:
        raise RowError(f'{label} must be at least {minimum}.')
    return int(d)


def to_money(text, label):
    if text == '':
        return None
    try:
        d = Decimal(re.sub(r'[₹,\s]|rs\.?', '', text, flags=re.I))
    except InvalidOperation:
        raise RowError(f'{label} "{text}" is not a number.')
    if d < 0:
        raise RowError(f'{label} cannot be negative.')
    return d.quantize(Decimal('0.01'))


def match_category(text):
    t = text.strip()
    if not t:
        return None
    up = t.upper()
    for code, _ in CATEGORIES:
        if up == code:
            return code
    words = {w.rstrip('s') for w in re.findall(r'[a-z]+', t.lower())}
    for code, label in CATEGORIES:
        lw = {w.rstrip('s') for w in re.findall(r'[a-z]+', label.lower())}
        if words and words <= lw:
            return code
    return None


def next_serial_start():
    best = 0
    for sku in JewelrySKU.objects.values_list('sku', flat=True):
        tail = sku.rsplit('-', 1)[-1]
        if tail.isdigit():
            best = max(best, int(tail))
    return best + 1


def yes(text):
    return text.strip().lower() in ('1', 'y', 'yes', 'true', 'listed', 'active', 'on')


# ------------------------------------------------------------------ SUPPLIERS
SUPPLIER_FIELDS = [
    F('name', 'Supplier name', True, aliases=['supplier', 'name', 'vendor', 'party', 'party name']),
    F('contact_person', 'Contact person', aliases=['contact', 'person', 'owner']),
    F('phone', 'Phone', aliases=['mobile', 'phone no', 'contact no', 'number', 'mobile no']),
    F('alt_phone', 'Alternate phone', aliases=['phone 2', 'alt phone', 'whatsapp']),
    F('email', 'Email', aliases=['mail', 'email id']),
    F('gstin', 'GST number', aliases=['gst', 'gstin', 'gst no']),
    F('address', 'Address', aliases=['street']),
    F('city', 'City', aliases=['town']),
    F('state', 'State'),
    F('notes', 'Notes', aliases=['remark', 'remarks', 'comment']),
]


def do_supplier(v, opt, ctx):
    name = ' '.join(v['name'].split())[:120]
    data = {k: v[k].strip() for k in ('contact_person', 'phone', 'alt_phone', 'email', 'gstin', 'address', 'city', 'state', 'notes')}
    data['gstin'] = data['gstin'].upper()
    data['phone'], data['alt_phone'] = data['phone'][:20], data['alt_phone'][:20]
    obj = Supplier.objects.filter(name__iexact=name).first()
    if obj:
        if opt.get('on_duplicate') != 'update':
            return 'skipped', f'"{name}" already exists'
        for k, val in data.items():
            if val:
                setattr(obj, k, val)
        obj.save()
        return 'updated', name
    Supplier.objects.create(name=name, **data)
    return 'created', name


# ------------------------------------------------------------------ PRODUCTS
def product_fields():
    fields = [
        F('name', 'Product name', True, aliases=['product', 'title', 'item', 'item name', 'product title']),
        F('category', 'Category', True, 'Code (BGL, NCK ...) or words like "Bangles"',
          aliases=['type', 'product type', 'cat', 'category code']),
        F('style', 'Style', aliases=['model style']),
        F('design_model', 'Design / model', aliases=['design', 'design no', 'model', 'design code']),
        F('color', 'Colour', aliases=['color', 'colour']),
        F('size', 'Size', aliases=['sizes']),
        F('number', 'Serial number', help='Leave unmapped to number them automatically', aliases=['serial', 'serial no', 'sr no', 'no']),
        F('is_listed', 'Listed (yes/no)', help='Blank = yes', aliases=['listed', 'active', 'visible']),
    ]
    for p in Platform.objects.order_by('id'):
        fields.append(F(f'listing::{p.pk}', f'{p.name} seller SKU', help='The SKU you use on this marketplace',
                        aliases=[f'{p.name} sku', f'{p.name} seller sku', f'{p.name} listing sku'], group='Marketplace SKUs'))
    for p in Platform.objects.order_by('id'):
        fields.append(F(f'price::{p.pk}', f'{p.name} selling rate', help='Rate on this marketplace',
                        aliases=[f'{p.name} price', f'{p.name} rate', f'{p.name} selling price'], group='Marketplace rates'))
    return fields


def do_product(v, opt, ctx):
    cat = match_category(v['category'])
    if cat is None:
        raise RowError('Category "%s" is not one of: %s.' % (v['category'], ', '.join(c for c, _ in CATEGORIES)))
    name = v['name'].strip().upper()[:100]
    style = (v['style'].strip().upper() or 'STD')[:50]
    color = (v['color'].strip().upper() or 'BLK')[:30]
    size = (v['size'].strip().upper() or 'FREE')[:30]
    design = v['design_model'].strip()[:50]

    cand = JewelrySKU(category=cat, style=style, name=name, design_model=design, color=color, size=size)
    number = v['number'].strip()
    explicit = bool(number)
    if explicit:
        cand.number = number.zfill(3) if number.isdigit() and len(number) < 3 else number
    # the same product = same category / style / name / colour / size, whatever its serial number
    same = [p for p in JewelrySKU.objects.filter(category=cat, name=name) if
            p.style.upper() == style and p.color.upper() == color and p.size.upper() == size]
    if explicit and not same:
        same = list(JewelrySKU.objects.filter(sku=cand.generate_sku()))
    existing = same[0] if same else None

    listing, rates = {}, {}
    for key, val in v.items():
        if key.startswith('listing::') and val.strip():
            listing[int(key.split('::')[1])] = val.strip()[:100]
        elif key.startswith('price::') and val.strip():
            rates[int(key.split('::')[1])] = to_money(val, 'Marketplace rate')

    if existing and opt.get('on_duplicate') != 'update':
        return 'skipped', f'already exists as {existing.sku}'

    if existing:
        sku = existing
        if design:
            sku.design_model = design
        if v['is_listed'].strip():
            sku.is_listed = yes(v['is_listed'])
        sku.save()
        status = 'updated'
    else:
        if not explicit:
            cand.number = str(ctx['serial']).zfill(3)
            ctx['serial'] += 1
        cand.is_listed = yes(v['is_listed']) if v['is_listed'].strip() else True
        if JewelrySKU.objects.filter(sku=cand.generate_sku()).exists():
            raise RowError(f'SKU {cand.generate_sku()} already exists.')
        cand.save()
        sku, status = cand, 'created'

    platforms = {p.pk: p for p in Platform.objects.all()}
    for pid, p in platforms.items():
        code = listing.get(pid)
        if code:
            clash = MarketplaceListingSKU.objects.filter(platform=p).exclude(sku=sku).filter(
                listing_sku__iexact=code, source=MarketplaceListingSKU.SOURCE_MANUAL)
            if clash.exists():
                raise RowError(f'{p.name} seller SKU "{code}" is already used by another product.')
            MarketplaceListingSKU.objects.update_or_create(
                sku=sku, platform=p, defaults={'source': MarketplaceListingSKU.SOURCE_MANUAL, 'listing_sku': code})
        elif status == 'created':
            MarketplaceListingSKU.objects.get_or_create(
                sku=sku, platform=p, defaults={'source': MarketplaceListingSKU.SOURCE_DATABASE, 'listing_sku': ''})
    for pid, price in rates.items():
        if pid in platforms and price is not None:
            PlatformPrice.objects.update_or_create(sku=sku, platform=platforms[pid], defaults={'price': price})
    return status, sku.sku


# ------------------------------------------------------------------ INVENTORY (stock batches)
INVENTORY_FIELDS = [
    F('sku', 'SKU ID', help='Your product SKU. Use this OR Product name.', aliases=['sku', 'sku id', 'skuid', 'product sku', 'item code', 'code']),
    F('product', 'Product name', help='Used when there is no SKU ID column (the name must match exactly one product)',
      aliases=['product', 'item', 'item name', 'name', 'product name', 'title']),
    F('batch_no', 'Batch number', True, 'Never generated for you - it must be in the file', aliases=['batch', 'batch no', 'lot', 'lot no', 'bill no', 'invoice no']),
    F('quantity', 'Quantity', True, 'Never defaulted - must be in the file', aliases=['qty', 'units', 'stock', 'pieces', 'pcs', 'quantity received']),
    F('purchase_price', 'Buying price', help='Blank = the product\'s latest buying price', aliases=['buying price', 'purchase price', 'cost', 'cost price', 'buy price', 'rate']),
    F('wholesale_price', 'Wholesale price', help='Blank = the product\'s latest price', aliases=['wholesale', 'wholesale rate']),
    F('selling_price', 'Reselling price', help='Blank = the product\'s latest price', aliases=['reselling price', 'reselling', 'selling price', 'sale price', 'resell price']),
    F('retail_price', 'Retail price', help='Blank = the product\'s latest retail price', aliases=['retail price', 'retail', 'retail rate', 'mrp']),
    F('supplier', 'Supplier', help='Matched by name; new names are added if you tick the option', aliases=['supplier name', 'vendor', 'party', 'purchased from']),
    F('storage_box', 'Storage box', aliases=['box', 'box name', 'storage', 'location']),
    F('section_name', 'Section / compartment', aliases=['section', 'compartment', 'slot', 'shelf']),
]


def do_inventory(v, opt, ctx):
    code, pname = v['sku'].strip(), v['product'].strip()
    if code:
        sku = JewelrySKU.objects.filter(sku__iexact=code).first()
        if sku is None:
            raise RowError(f'No product with SKU "{code}". Import the products first.')
    elif pname:
        matches = list(JewelrySKU.objects.filter(name__iexact=pname)[:3])
        if not matches:
            raise RowError(f'No product named "{pname}". Import the products first.')
        if len(matches) > 1:
            raise RowError(f'{len(JewelrySKU.objects.filter(name__iexact=pname))} products are named "{pname}" - map the SKU ID column instead.')
        sku = matches[0]
    else:
        raise RowError('Both SKU ID and Product name are empty.')
    batch_no = v['batch_no'].strip()
    if not batch_no:
        raise RowError('Batch number is empty.')
    if len(batch_no) > 120:
        raise RowError('Batch number is longer than 120 characters.')
    if v['quantity'].strip() == '':
        raise RowError('Quantity is empty.')
    qty = to_int(v['quantity'], 'Quantity', minimum=1)
    if sku.batches.filter(batch_no__iexact=batch_no).exists():
        return 'skipped', f'{sku.sku}: batch {batch_no} already exists'

    prices = {}
    for key, label in (('purchase_price', 'Buying price'), ('wholesale_price', 'Wholesale price'), ('selling_price', 'Reselling price'), ('retail_price', 'Retail price')):
        val = to_money(v[key], label)
        prices[key] = val if val is not None else getattr(sku, key)

    supplier = None
    sname = ' '.join(v['supplier'].split())
    if sname:
        supplier = Supplier.objects.filter(name__iexact=sname).first()
        if supplier is None:
            if not opt.get('create_suppliers'):
                raise RowError(f'Supplier "{sname}" does not exist (tick "add missing suppliers" or import suppliers first).')
            supplier = Supplier.objects.create(name=sname[:120])
    box = None
    bname = v['storage_box'].strip()
    if bname:
        box = StorageBox.objects.filter(name__iexact=bname).first()
        if box is None:
            if not opt.get('create_boxes'):
                raise RowError(f'Storage box "{bname}" does not exist (tick "add missing storage boxes").')
            box = StorageBox.objects.create(name=bname[:50])
    section = v['section_name'].strip()[:50] or (sku.section_name if not box else 'Main Compartment') or 'Main Compartment'
    StockBatch.objects.create(
        sku=sku, batch_no=batch_no, quantity=qty, storage_box=box or sku.storage_box, section_name=section,
        supplier=supplier, supplier_name=supplier.name if supplier else '', status='active', **prices)
    ctx['touched'].add(sku.pk)
    return 'created', f'{sku.sku}: batch {batch_no} x{qty}'


# ------------------------------------------------------------------ registry
KINDS = {
    'products': {
        'title': 'Products', 'icon': 'fa-tags', 'color': 'amber',
        'blurb': 'Create products in bulk. The SKU ID and serial number are generated automatically, exactly like the Add Product form.',
        'fields': product_fields, 'handler': do_product, 'sample_rows': [
            ['Gold Bangle Set', 'Bangles', 'Standard', 'DSN-12', 'Gold', '2.6', ''],
            ['Heart Necklace', 'NCK', '', '', 'Silver', 'Free Size', '']],
        'sample_headers': ['Product name', 'Category', 'Style', 'Design', 'Colour', 'Size', 'Meesho seller SKU'],
        'options': ['on_duplicate'], 'after': 'product_list',
    },
    'inventory': {
        'title': 'Inventory (stock batches)', 'icon': 'fa-boxes-stacked', 'color': 'emerald',
        'blurb': 'Add stock batches to existing products. Each row is one batch with its own supplier and prices.',
        'fields': lambda: INVENTORY_FIELDS, 'handler': do_inventory, 'sample_rows': [
            ['GEN-STD-DEMO-BLK-FREE-001', 'B-2410-01', '50', '110', '140', '190', '249', 'Shree Traders'],
            ['', 'B-2410-02', '20', '95', '130', '175', '229', 'Mahalaxmi Imitation']],
        'sample_headers': ['SKU ID', 'Batch number', 'Quantity', 'Buying price', 'Wholesale price', 'Reselling price', 'Retail price', 'Supplier'],
        'options': ['create_suppliers', 'create_boxes'], 'after': 'inventory_list',
    },
    'suppliers': {
        'title': 'Suppliers', 'icon': 'fa-truck-field', 'color': 'sky',
        'blurb': 'Import your supplier / vendor list.',
        'fields': lambda: SUPPLIER_FIELDS, 'handler': do_supplier, 'sample_rows': [
            ['Shree Traders', 'Ramesh', '9876543210', 'ramesh@example.com', '24ABCDE1234F1Z5', 'Surat'],
            ['Mahalaxmi Imitation', 'Suresh', '9123456780', '', '', 'Rajkot']],
        'sample_headers': ['Supplier name', 'Contact person', 'Phone', 'Email', 'GST number', 'City'],
        'options': ['on_duplicate'], 'after': 'supplier_list',
    },
}
