import re


MAX_LABEL_PDF_BYTES = 10 * 1024 * 1024
MAX_LABEL_PDF_PAGES = 60
AMAZON_ORDER_ID = re.compile(r'\b\d{3}-\d{7}-\d{7}\b')


class LabelParseError(ValueError):
    pass


def parse_meesho_label_text(text):
    customer_match = re.search(
        r'Customer Address\s*(.*?)\s*If undelivered, return to:',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not customer_match:
        raise LabelParseError('Could not find the recipient details on this Meesho label.')

    customer_lines = [line.strip() for line in customer_match.group(1).splitlines() if line.strip()]
    if len(customer_lines) < 2:
        raise LabelParseError('The label does not contain a recipient name and address.')

    details_match = re.search(
        r'Product Details\s+SKU\s+Size\s+Qty\s+Color\s+Order No\.(.*?)TAX INVOICE',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not details_match:
        raise LabelParseError('Could not find the product and order details on this Meesho label.')

    product_text = ' '.join(details_match.group(1).split())
    row_match = re.search(
        r'(?P<seller_sku>.+?)\s+'
        r'(?P<size>Free\s+Size|One\s+Size|[A-Za-z0-9]+(?:\s*[x×]\s*[A-Za-z0-9]+)?)\s+'
        r'(?P<quantity>\d{1,5})\s+'
        r'(?P<color>.+?)\s+'
        r'(?P<order_id>\d{8,}(?:_\d+)?)$',
        product_text,
        flags=re.IGNORECASE,
    )
    if not row_match:
        raise LabelParseError('Could not read the item SKU, quantity, and sub-order ID from this label.')

    seller_sku = row_match.group('seller_sku').strip()
    quantity = int(row_match.group('quantity'))
    if not seller_sku or quantity < 1:
        raise LabelParseError('The label contains an invalid item SKU or quantity.')

    return {
        'seller_sku': seller_sku,
        'order_id': row_match.group('order_id'),
        'quantity': quantity,
        'customer_name': customer_lines[0],
        'shipping_address': ', '.join(customer_lines[1:]),
    }


def parse_amazon_label_text(text):
    order_match = AMAZON_ORDER_ID.search(text)
    sku_match = re.search(r'\bSKU\s*:\s*([^|\s]+)', text, flags=re.IGNORECASE)
    quantity_match = re.search(r'\bQuantity\s*:?\s*(\d{1,5})\b', text, flags=re.IGNORECASE)
    ship_to_match = re.search(
        r'Ship\s+To\s*:\s*(.*?)(?=\bOrder\s+ID\s*:|$)',
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not order_match or not sku_match or not quantity_match:
        raise LabelParseError('Could not read the Amazon order ID, seller SKU, and quantity.')

    recipient_lines = (
        [line.strip() for line in ship_to_match.group(1).splitlines() if line.strip()]
        if ship_to_match
        else []
    )
    if not recipient_lines:
        raise LabelParseError('Could not find the recipient details on this Amazon label.')

    return {
        'platform': 'Amazon',
        'seller_sku': sku_match.group(1).strip(),
        'order_id': order_match.group(0),
        'quantity': int(quantity_match.group(1)),
        'customer_name': recipient_lines[0],
        'shipping_address': ', '.join(recipient_lines[1:]),
    }


def _read_pdf_pages(uploaded_file):
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as error:
        raise LabelParseError(
            'PDF scanning needs pypdf. Install the project dependencies with '
            '"python -m pip install -r requirements.txt" using the same Python '
            'environment that runs Django.'
        ) from error

    uploaded_file.seek(0, 2)
    file_size = getattr(uploaded_file, 'size', uploaded_file.tell())
    uploaded_file.seek(0)
    if file_size > MAX_LABEL_PDF_BYTES:
        raise LabelParseError('PDF must be 10 MB or smaller.')

    if uploaded_file.read(5) != b'%PDF-':
        raise LabelParseError('Upload a valid PDF shipping label.')
    uploaded_file.seek(0)

    try:
        reader = PdfReader(uploaded_file, strict=True)
        if reader.is_encrypted:
            raise LabelParseError('Password-protected PDFs are not supported.')
        if not reader.pages or len(reader.pages) > MAX_LABEL_PDF_PAGES:
            raise LabelParseError(
                f'PDF must contain between 1 and {MAX_LABEL_PDF_PAGES} pages.'
            )
        return [page.extract_text() or '' for page in reader.pages]
    except PdfReadError as error:
        raise LabelParseError('The uploaded file could not be read as a PDF.') from error


def extract_labels(uploaded_file):
    pages = _read_pdf_pages(uploaded_file)
    labels = {}
    skipped_pages = set()
    amazon_pages = {}

    for page_number, text in enumerate(pages, start=1):
        if not text.strip():
            skipped_pages.add(page_number)
            continue
        try:
            parsed = parse_meesho_label_text(text)
            parsed['platform'] = 'Meesho'
            labels[(parsed['platform'], parsed['order_id'])] = parsed
            continue
        except LabelParseError:
            pass

        order_match = AMAZON_ORDER_ID.search(text)
        if order_match:
            amazon_pages.setdefault(order_match.group(0), []).append((page_number, text))
        else:
            skipped_pages.add(page_number)

    for order_id, order_pages in amazon_pages.items():
        combined_text = '\n'.join(text for _, text in order_pages)
        try:
            parsed = parse_amazon_label_text(combined_text)
        except LabelParseError:
            skipped_pages.update(page_number for page_number, _ in order_pages)
            continue
        labels[(parsed['platform'], parsed['order_id'])] = parsed

    if not labels:
        raise LabelParseError('No supported Meesho or Amazon orders were found in this PDF.')
    return list(labels.values()), sorted(skipped_pages)


def extract_meesho_label(uploaded_file):
    labels, skipped_pages = extract_labels(uploaded_file)
    if skipped_pages or len(labels) != 1 or labels[0]['platform'] != 'Meesho':
        raise LabelParseError('Upload a PDF containing exactly one readable Meesho order.')
    return labels[0]
