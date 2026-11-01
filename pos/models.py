# pos/models.py
"""Counter (cashier) sales. """
from django.conf import settings
from django.db import models


class PosSale(models.Model):
    PAYMENT_CHOICES = [('cash', 'Cash'), ('upi', 'UPI'), ('card', 'Card')]
    STATUS_PAID = 'paid'
    STATUS_VOID = 'void'
    STATUS_CHOICES = [(STATUS_PAID, 'Paid'), (STATUS_VOID, 'Void')]

    bill_no = models.CharField(max_length=30, unique=True)
    # one-time key sent by the screen so a double-click / retry never bills twice
    token = models.CharField(max_length=64, unique=True, null=True, blank=True)
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='pos_sales')
    cashier_name = models.CharField(max_length=150, blank=True, default='')
    customer_name = models.CharField(max_length=120, blank=True, default='')
    customer_phone = models.CharField(max_length=20, blank=True, default='')
    price_list = models.CharField(max_length=10, default='reselling')       # reselling | wholesale | buying
    payment_method = models.CharField(max_length=10, choices=PAYMENT_CHOICES, default='cash')
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    amount_received = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    change_given = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PAID)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by_name = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at', '-pk']

    @property
    def items_count(self):
        return sum(item.quantity for item in self.items.all())

    def __str__(self):
        return f'{self.bill_no} ({self.total})'


class PosSaleItem(models.Model):
    sale = models.ForeignKey(PosSale, on_delete=models.CASCADE, related_name='items')
    sku = models.ForeignKey('sku_manager.JewelrySKU', on_delete=models.SET_NULL, null=True, blank=True,
                            related_name='pos_items')
    # snapshots, so old bills still read correctly if a product is renamed or deleted
    sku_code = models.CharField(max_length=100)
    name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    # [[batch_id, qty], ...] - which batches the units came out of (used to put stock back on void)
    allocations = models.JSONField(default=list, blank=True)
    dispatch_log = models.OneToOneField('sku_manager.DispatchLog', on_delete=models.SET_NULL, null=True, blank=True,
                                        related_name='pos_item')

    def __str__(self):
        return f'{self.name} x{self.quantity}'

