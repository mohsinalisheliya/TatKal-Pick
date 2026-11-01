# sku_manager/pdf_orders.py
"""Read marketplace shipping-label PDFs (Meesho + Amazon) into order dicts.

One PDF can hold many orders (one label per page, or several per page), so the
public entry point is ``extract_labels`` which returns a *list* of dicts:

    {
        'platform': 'Meesho' | 'Amazon',
        'order_id': str,
        'seller_sku': str,
        'quantity': int,
        'customer_name': str,
        'shipping_address': str,
    }

``extract_meesho_label`` / ``parse_meesho_label_text`` are kept for backwards
compatibility (single order, no 'platform' key).
"""
import re

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PDF_PAGES = 60


class LabelParseError(Exception):
    """The PDF could not be read as a supported marketplace label."""


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _lines(text):
    return [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip()]


def _clean_address_line(line):
    return line.strip().strip(',').strip()


# --------------------------------------------------------------------------
# Meesho
# --------------------------------------------------------------------------
_MEESHO_ORDER_RE = re.compile(r'(\d{10,}(?:_\d+)?)\s*$')
_MEESHO_SIZE_SUFFIXES = (
    ('FREE', 'SIZE'),
    ('ONE', 'SIZE'),
    ('ADJUSTABLE',),
    ('ADJ',),
    ('XXL',),
    ('XL',),
    ('L',),
    ('M',),
    ('S',),
    ('FREE',),
)
_MEESHO_NUMERIC_SIZE_RE = re.compile(r'\d+(?:\.\d+)?')


def parse_meesho_label_text(text):
    if 'Customer Address' not in text or 'Order No' not in text:
        raise LabelParseError('This does not look like a Meesho shipping label.')

    lines = _lines(text)
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith('Customer Address'))
    except StopIteration:
        raise LabelParseError('Customer address block not found on the Meesho label.')

    block = lines[start + 1:]
    if not block:
        raise LabelParseError('Recipient name not found on the Meesho label.')
    customer_name = block[0]
    address_lines = []
    for line in block[1:]:
        if line.lower().startswith('if undelivered') or line.lower().startswith('product details'):
            break
        address_lines.append(_clean_address_line(line))
    shipping_address = ', '.join(part for part in address_lines if part)

    header_idx = next(
        (i for i, line in enumerate(lines) if line.startswith('SKU') and 'Order No' in line),
        None,
    )
    if header_idx is None:
        raise LabelParseError('Product table (SKU / Qty / Order No.) not found on the Meesho label.')

    row = ''
    match = None
    for extra in range(1, 4):  # long SKUs can wrap onto the next line
        row = ' '.join(lines[header_idx + 1:header_idx + 1 + extra])
        match = _MEESHO_ORDER_RE.search(row)
        if match:
            break
    if not match:
        raise LabelParseError('Order number not found on the Meesho label.')

    order_id = match.group(1)
    tokens = row[:match.start()].split()
    quantity_index = max(
        (i for i, token in enumerate(tokens) if token.isdigit()),
        default=None,
    )
    if quantity_index is None or quantity_index == 0:
        raise LabelParseError('SKU or quantity not found on the Meesho label.')
    quantity = int(tokens[quantity_index])
    if quantity < 1:
        raise LabelParseError('Quantity on the Meesho label must be at least 1.')

    sku_and_size = tokens[:quantity_index]
    size_start = len(sku_and_size)
    upper_tokens = [token.upper() for token in sku_and_size]
    for suffix in _MEESHO_SIZE_SUFFIXES:
        if len(upper_tokens) > len(suffix) and tuple(upper_tokens[-len(suffix):]) == suffix:
            size_start = len(upper_tokens) - len(suffix)
            break
    else:
        if len(upper_tokens) > 1 and _MEESHO_NUMERIC_SIZE_RE.fullmatch(sku_and_size[-1]):
            size_start -= 1
        else:
            size_start = 1

    # SKU is the variable-length first column; the known size column follows it.
    # Fall back to the legacy first-token parse when a label uses an unknown size.
    seller_sku = ' '.join(sku_and_size[:size_start]) if size_start else tokens[0]

    return {
        'seller_sku': seller_sku,
        'order_id': order_id,
        'quantity': quantity,
        'customer_name': customer_name,
        'shipping_address': shipping_address,
    }


def _meesho_labels_from_page(text):
    """A page may hold one label or several; split on the address heading."""
    found = []
    for block in re.split(r'(?=Customer Address)', text):
        if 'Customer Address' not in block:
            continue
        try:
            found.append(parse_meesho_label_text(block))
        except LabelParseError:
            continue  # e.g. an invoice section that repeats the heading
    return found


# --------------------------------------------------------------------------
# Amazon
# --------------------------------------------------------------------------
_AMAZON_ORDER_RE = re.compile(r'\b(\d{3}-\d{7}-\d{7})\b')
_AMAZON_ADDRESS_HEAD = re.compile(
    r'^(?:ship(?:ping)?\s*to|deliver(?:y)?\s*to|shipping\s*address|delivery\s*address)\s*:?\s*(.*)$',
    re.IGNORECASE,
)
_AMAZON_STOP = re.compile(
    r'^(?:order\s*id|order\s*date|invoice|phone|mobile|tel|sold\s*by|seller|product|qty|quantity|'
    r'item|asin|sku|bill(?:ing)?\s*to|payment|cod|prepaid|if\s+undelivered|return\s+address|state/ut|place\s+of)\b',
    re.IGNORECASE,
)
_PIN_RE = re.compile(r'\b\d{6}\b')


def _amazon_recipient(lines):
    for idx, line in enumerate(lines):
        match = _AMAZON_ADDRESS_HEAD.match(line)
        if not match:
            continue
        rest = match.group(1).strip()
        block = ([rest] if rest else []) + lines[idx + 1:idx + 10]
        collected = []
        for entry in block:
            if collected and _AMAZON_STOP.match(entry):
                break
            if not collected and _AMAZON_STOP.match(entry):
                continue
            collected.append(entry)
            if _PIN_RE.search(entry) and len(collected) > 1:
                break
        if len(collected) >= 2:
            return collected[0], ', '.join(_clean_address_line(part) for part in collected[1:] if part)
    return '', ''


def parse_amazon_label_text(text):
    order_match = (
        re.search(r'Order\s*(?:Number|ID)\s*:?\s*(\d{3}-\d{7}-\d{7})', text, re.IGNORECASE)
        or _AMAZON_ORDER_RE.search(text)
    )
    if not order_match or ('amazon' not in text.lower() and 'order id' not in text.lower()):
        raise LabelParseError('This does not look like an Amazon shipping label or packing slip.')

    lines = _lines(text)
    name, address = _amazon_recipient(lines)

    # Invoice layout: "... | B0HHYW3PM4 ( MY-SELLER-SKU / 110 )"; packing slip: "SKU: MY-SKU"
    sku_match = re.search(r'\bB0[A-Z0-9]{8}\s*\(\s*([^()]+?)\s*\)', text)
    if sku_match:
        seller_sku = re.sub(r'\s+', ' ', sku_match.group(1)).strip()
    else:
        plain = re.search(r'\bSKU\s*[:#]\s*([A-Za-z0-9][A-Za-z0-9._\-/]*)', text, re.IGNORECASE)
        seller_sku = plain.group(1) if plain else ''

    qty_match = (
        re.search(r'-?\u20b9[\d,.]+\s+-?\u20b9[\d,.]+\s+(\d+)\s+\u20b9', text)  # unit price, discount, QTY, net
        or re.search(r'\bQuantity\s*[:\-]?\s*(\d+)', text, re.IGNORECASE)
        or re.search(r'\bQty\s*[:\-]?\s*(\d+)', text, re.IGNORECASE)
    )
    quantity = int(qty_match.group(1)) if qty_match else 1

    # Invoices repeat the recipient name as the first two lines; drop the duplicate.
    if address.startswith(name + ', '):
        address = address[len(name) + 2:]

    return {
        'seller_sku': seller_sku,
        'order_id': order_match.group(1),
        'quantity': max(1, quantity),
        'customer_name': name,
        'shipping_address': address,
    }


def _merge_amazon_pages(parsed_pages):
    """An Amazon order often spans label + packing-slip pages; merge by order id."""
    merged = {}
    for item in parsed_pages:
        current = merged.setdefault(item['order_id'], dict(item))
        for key, value in item.items():
            if value and not current.get(key):
                current[key] = value
        if item['seller_sku'] and item['quantity'] > current['quantity'] and item['seller_sku'] == current['seller_sku']:
            current['quantity'] = item['quantity']
    return list(merged.values())


# --------------------------------------------------------------------------
# PDF reading
# --------------------------------------------------------------------------
def _read_pages(uploaded_file):
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError:
        raise LabelParseError(
            'PDF scanning needs the "pypdf" package. Install it with "pip install pypdf" '
            'in the same Python environment that runs Django, then restart the server.'
        )

    size = getattr(uploaded_file, 'size', None)
    if size is not None and size > MAX_PDF_BYTES:
        raise LabelParseError('This PDF is larger than 10 MB. Split it and upload again.')

    if hasattr(uploaded_file, 'seek'):
        uploaded_file.seek(0)
    try:
        reader = PdfReader(uploaded_file)
        if reader.is_encrypted:
            raise LabelParseError('This PDF is password-protected and cannot be read.')
        if len(reader.pages) > MAX_PDF_PAGES:
            raise LabelParseError(f'This PDF has more than {MAX_PDF_PAGES} pages. Split it and upload again.')
        return [(page.extract_text() or '') for page in reader.pages]
    except LabelParseError:
        raise
    except (PdfReadError, ValueError, OSError, KeyError):
        raise LabelParseError('This file could not be read as a PDF.')


def detect_platform(text):
    if 'Customer Address' in text and 'Order No' in text:
        return 'Meesho'
    if _AMAZON_ORDER_RE.search(text) and ('amazon' in text.lower() or 'order id' in text.lower()):
        return 'Amazon'
    return None


def extract_labels(uploaded_file):
    """Return (labels, skipped_pages). Raises LabelParseError if nothing readable."""
    page_texts = _read_pages(uploaded_file)

    labels = []
    amazon_pages = []
    skipped = []
    seen = set()

    for number, text in enumerate(page_texts, start=1):
        platform = detect_platform(text)
        if not text.strip():
            continue  # image-only page (e.g. Amazon's shipping label); the invoice page has the data
        if platform == 'Meesho':
            parsed = _meesho_labels_from_page(text)
            if not parsed:
                skipped.append(number)
            for item in parsed:
                key = ('Meesho', item['order_id'])
                if key not in seen:
                    seen.add(key)
                    labels.append({'platform': 'Meesho', **item})
        elif platform == 'Amazon':
            try:
                amazon_pages.append(parse_amazon_label_text(text))
            except LabelParseError:
                skipped.append(number)
        else:
            skipped.append(number)

    for item in _merge_amazon_pages(amazon_pages):
        key = ('Amazon', item['order_id'])
        if key not in seen:
            seen.add(key)
            labels.append({'platform': 'Amazon', **item})

    if not labels:
        if not any(text.strip() for text in page_texts):
            raise LabelParseError(
                'No text found in this PDF. Image-only or scanned PDFs are not supported yet - '
                'download the label PDF directly from the marketplace panel.'
            )
        raise LabelParseError('No Meesho or Amazon orders were found in this PDF.')
    return labels, skipped


# --------------------------------------------------------------------------
# backwards compatible single-label helper
# --------------------------------------------------------------------------
def extract_meesho_label(uploaded_file):
    labels, _ = extract_labels(uploaded_file)
    first = next((item for item in labels if item['platform'] == 'Meesho'), None)
    if first is None:
        raise LabelParseError('No Meesho order was found in this PDF.')
    return {key: value for key, value in first.items() if key != 'platform'}