# sku_manager/models.py
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from datetime import timedelta
import re

class Platform(models.Model):
    name = models.CharField(max_length=50, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class StorageBox(models.Model):
    name = models.CharField(max_length=50, unique=True)
    description = models.CharField(max_length=100, blank=True, default='')
    color_tag = models.CharField(max_length=20, default='#3B82F6')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

class JewelrySKU(models.Model):
    name = models.CharField(max_length=100)
    category = models.CharField(max_length=50, default='General')
    style = models.CharField(max_length=50, blank=True, default='Standard')
    design_model = models.CharField(max_length=50, blank=True, default='')
    size = models.CharField(max_length=30, blank=True, default='Free Size')
    color = models.CharField(max_length=30, blank=True, default='Black')
    number = models.CharField(max_length=10, default='001')
    sku = models.CharField(max_length=100, unique=True, editable=False)
    image = models.ImageField(upload_to='products/', null=True, blank=True)
    is_listed = models.BooleanField(default=True)

    # Aggregated Live Stock
    batch_no = models.CharField(max_length=50, blank=True, default='')
    stock = models.PositiveIntegerField(default=0)
    storage_box = models.ForeignKey(StorageBox, on_delete=models.SET_NULL, null=True, blank=True, related_name='skus')
    section_name = models.CharField(max_length=50, blank=True, default='Main Slot')

    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    wholesale_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def sync_stock_from_batches(self):
        """30-day retention lifecycle + sum of active units"""
        now = timezone.now()

        # 1. Update status based on quantity
        for b in self.batches.all():
            if b.quantity == 0 and b.status == 'active':
                b.status = 'disabled'
                if not b.finished_at:
                    b.finished_at = now
                b.save(update_fields=['status', 'finished_at', 'updated_at'])
            elif b.quantity > 0 and b.status == 'disabled':
                b.status = 'active'
                b.finished_at = None
                b.save(update_fields=['status', 'finished_at', 'updated_at'])

        # 2. Delete batches finished more than 30 days ago
        cutoff_date = now - timedelta(days=30)
        self.batches.filter(status='disabled', finished_at__lte=cutoff_date).delete()

        # 3. Sum active units
        total_active = (self.batches
                .filter(status='active', sales_enabled=True)
                .aggregate(models.Sum('quantity'))['quantity__sum'] or 0)
        self.stock = max(0, total_active)

        # 4. Cache latest active batch details
        latest = self.batches.filter(status='active').order_by('-created_at').first() or self.batches.order_by('-created_at').first()
        if latest:
            self.purchase_price = latest.purchase_price
            self.wholesale_price = latest.wholesale_price
            self.selling_price = latest.selling_price
            self.batch_no = latest.batch_no
            if latest.storage_box:
                self.storage_box = latest.storage_box
            if latest.section_name:
                self.section_name = latest.section_name

        self.save(update_fields=[
            'stock',
            'purchase_price',
            'wholesale_price',
            'selling_price',
            'batch_no',
            'storage_box',
            'section_name',
            'updated_at',
        ])

    def generate_sku(self):
        clean_cat = re.sub(r'[^A-Z0-9]', '', self.category.upper()) or 'GEN'
        clean_sty = re.sub(r'[^A-Z0-9]', '', self.style.upper()) or 'STD'
        clean_name = re.sub(r'[^A-Z0-9]', '', self.name.upper()) or 'ITEM'
        clean_col = re.sub(r'[^A-Z0-9]', '', self.color.upper()) or 'BLK'
        clean_size = re.sub(r'[^A-Z0-9]', '', self.size.upper()) or 'FREE'
        clean_num = str(self.number or '001').strip()
        if clean_num.isdigit() and len(clean_num) < 3:
            clean_num = clean_num.zfill(3)

        return f"{clean_cat}-{clean_sty}-{clean_name}-{clean_col}-{clean_size}-{clean_num}"

    def save(self, *args, **kwargs):
        self.sku = self.generate_sku()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.sku} ({self.name})"

class PackagingMaterial(models.Model):
    TYPE_CHOICES = [
        ('box',   'Packaging Box'),
        ('bag',   'Poly / Courier Bag'),
        ('card',  'Thank You / Insert Card'),
        ('other', 'Other Material / Tape / Sticker'),
    ]

    name            = models.CharField(max_length=100)
    material_type   = models.CharField(max_length=20, choices=TYPE_CHOICES, default='box')
    cost_per_piece  = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    stock_quantity  = models.PositiveIntegerField(default=0)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} (₹{self.cost_per_piece}/pc)"


class StockBatch(models.Model):
    STATUS_CHOICES = [
        ('active', 'ACTIVATED'),
        ('disabled', 'DISACTIVATED'),
    ]

    sku = models.ForeignKey(JewelrySKU, on_delete=models.CASCADE, related_name='batches')
    batch_no = models.CharField(max_length=120)
    quantity = models.PositiveIntegerField(default=0)
    purchase_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    wholesale_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    storage_box = models.ForeignKey(StorageBox, on_delete=models.SET_NULL, null=True, blank=True)
    section_name = models.CharField(max_length=50, blank=True, default='Main Section')
    supplier_name = models.CharField(max_length=100, blank=True, default='Primary Supplier')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    sales_enabled = models.BooleanField(default=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    # Packaging cost — cached sum of all BatchPackagingItems for this batch.
    # Updated by save_packaging_items() in the view after each save.
    packaging_cost = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text='Cached total packaging cost per product unit (sum of all items × qty).',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def true_cost(self):
        """Total landed cost per unit = product purchase price + packaging cost."""
        from decimal import Decimal
        return (self.purchase_price or Decimal('0')) + (self.packaging_cost or Decimal('0'))

    @property
    def days_until_deletion(self):
        if self.finished_at:
            elapsed = (timezone.now() - self.finished_at).days
            return max(0, 30 - elapsed)
        return 30

    def recalculate_packaging_cost(self):
        """Recalculate and cache the total packaging cost from all BatchPackagingItems."""
        from decimal import Decimal
        total = sum(
            (item.quantity_per_unit * item.cost_snapshot for item in self.packaging_items.all()),
            Decimal('0'),
        )
        self.packaging_cost = total
        self.save(update_fields=['packaging_cost', 'updated_at'])

    def __str__(self):
        return f"{self.sku.sku} - {self.batch_no} ({self.quantity} units)"


class BatchPackagingItem(models.Model):
    """Tracks which packaging materials (and how many per unit) are used in a batch."""
    batch = models.ForeignKey(
        StockBatch, on_delete=models.CASCADE, related_name='packaging_items'
    )
    material = models.ForeignKey(
        'PackagingMaterial', on_delete=models.PROTECT, related_name='batch_items'
    )
    quantity_per_unit = models.PositiveIntegerField(
        default=1,
        help_text='How many of this material are used per product unit (e.g. 1 box, 2 stickers).'
    )
    cost_snapshot = models.DecimalField(
        max_digits=10, decimal_places=2, default=0.00,
        help_text='Material cost per piece at the time this batch was created/updated.'
    )

    class Meta:
        unique_together = ('batch', 'material')

    @property
    def line_total(self):
        """Cost contribution per product unit for this packaging line."""
        return self.quantity_per_unit * self.cost_snapshot

    def __str__(self):
        return f"{self.material.name} ×{self.quantity_per_unit} → {self.batch}"


class ManualStockReturn(models.Model):
    sku = models.ForeignKey(JewelrySKU, on_delete=models.CASCADE, related_name='manual_stock_returns')
    order_id = models.CharField(max_length=100, blank=True, default='')
    quantity = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['order_id'],
                condition=~models.Q(order_id=''),
                name='uniq_manual_stock_return_order_id',
            ),
        ]


class PlatformPrice(models.Model):
    sku = models.ForeignKey(JewelrySKU, on_delete=models.CASCADE, related_name='platform_prices')
    platform = models.ForeignKey(Platform, on_delete=models.CASCADE)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    class Meta:
        unique_together = ('sku', 'platform')

class MarketplaceListingSKU(models.Model):
    SOURCE_DATABASE = 'database'
    SOURCE_MANUAL = 'manual'
    SOURCE_CHOICES = [
        (SOURCE_DATABASE, 'From database'),
        (SOURCE_MANUAL, 'Enter manually'),
    ]

    sku = models.ForeignKey(JewelrySKU, on_delete=models.CASCADE, related_name='marketplace_listing_skus')
    platform = models.ForeignKey(Platform, on_delete=models.CASCADE, related_name='listing_skus')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_MANUAL)
    listing_sku = models.CharField(max_length=100, blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('sku', 'platform'), name='uniq_market_sku_product'),
            models.UniqueConstraint(
                Lower('listing_sku'),
                'platform',
                condition=~models.Q(listing_sku=''),
                name='uniq_market_sku_value_ci',
            ),
        ]

    @property
    def effective_sku(self):
        if self.source == self.SOURCE_DATABASE:
            return self.sku.sku
        return self.listing_sku

    def __str__(self):
        return f"{self.platform.name}: {self.effective_sku} → {self.sku.sku}"

class MarketplaceOrder(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_DISPATCHED = 'dispatched'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending review'),
        (STATUS_DISPATCHED, 'Dispatched'),
        (STATUS_CANCELLED, 'Cancelled / RTO'),
    ]

    platform = models.ForeignKey(Platform, on_delete=models.PROTECT, related_name='orders')
    platform_name = models.CharField(max_length=50)
    order_id = models.CharField(max_length=100)
    seller_sku = models.CharField(max_length=100)
    sku = models.ForeignKey(
        JewelrySKU,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketplace_orders',
    )
    quantity = models.PositiveIntegerField()
    customer_name = models.CharField(max_length=150, blank=True, default='')
    shipping_address = models.TextField(blank=True, default='')
    customer_phone = models.CharField(max_length=40, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    dispatch_log = models.OneToOneField(
        'DispatchLog',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marketplace_order',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    stock_restored = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('platform', 'order_id'), name='uniq_marketplace_order_id'),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.platform_name} order {self.order_id}"


class MarketplaceLabelScan(models.Model):
    pdf_file = models.FileField(upload_to='marketplace_labels/%Y/%m/%d/')
    orders = models.ManyToManyField(MarketplaceOrder, related_name='label_scans')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Marketplace label scan {self.pk}"


class DispatchLog(models.Model):
    sku = models.ForeignKey(JewelrySKU, on_delete=models.CASCADE, related_name='dispatch_logs')
    platform = models.ForeignKey(Platform, on_delete=models.SET_NULL, null=True, blank=True)
    platform_name = models.CharField(max_length=50)
    quantity = models.PositiveIntegerField(default=1)
    sold_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    stock_after = models.PositiveIntegerField(default=0)
    dispatched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-dispatched_at']

class AppSettings(models.Model):
    brand_name = models.CharField(max_length=100, default='TATKAL PICK')
    label_logo = models.ImageField(upload_to='branding/', blank=True, null=True)
    tagline = models.CharField(max_length=150, blank=True, default='Universal Inventory & Dispatch')
    currency_symbol = models.CharField(max_length=10, default='₹')
    low_stock_threshold = models.PositiveIntegerField(default=5)
    support_contact = models.CharField(max_length=100, blank=True, default='')
    meesho_store_url = models.URLField(max_length=500, blank=True, default='')
    amazon_store_url = models.URLField(max_length=500, blank=True, default='')
    flipkart_store_url = models.URLField(max_length=500, blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get_settings(cls):
        obj, _ = cls.objects.get_or_create(id=1)
        return obj