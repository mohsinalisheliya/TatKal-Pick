import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError


MAX_LABEL_PDF_BYTES = 10 * 1024 * 1024
MAX_LABEL_PDF_PAGES = 5


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


def extract_meesho_label(uploaded_file):
    if uploaded_file.size > MAX_LABEL_PDF_BYTES:
        raise LabelParseError('PDF must be 10 MB or smaller.')

    uploaded_file.seek(0)
    if uploaded_file.read(5) != b'%PDF-':
        raise LabelParseError('Upload a valid PDF shipping label.')
    uploaded_file.seek(0)

    try:
        reader = PdfReader(uploaded_file, strict=True)
        if reader.is_encrypted:
            raise LabelParseError('Password-protected PDFs are not supported.')
        if not reader.pages or len(reader.pages) > MAX_LABEL_PDF_PAGES:
            raise LabelParseError(f'PDF must contain between 1 and {MAX_LABEL_PDF_PAGES} pages.')
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    except PdfReadError as error:
        raise LabelParseError('The uploaded file could not be read as a PDF.') from error

    if not text.strip():
        raise LabelParseError('This PDF has no selectable text. Image-only labels are not supported yet.')
    return parse_meesho_label_text(text)
