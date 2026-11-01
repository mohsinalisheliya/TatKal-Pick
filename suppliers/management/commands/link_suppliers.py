from django.core.management.base import BaseCommand

from sku_manager.models import StockBatch
from suppliers.models import Supplier


class Command(BaseCommand):
    help = ('Turn the free-text "supplier_name" already stored on your batches into real Suppliers and link '
            'each batch to its supplier. Safe to run more than once.')

    def add_arguments(self, p):
        p.add_argument('--include-default', action='store_true',
                       help='Also create a supplier called "Primary Supplier" (the old automatic value).')

    def handle(self, *a, **o):
        skip = set() if o['include_default'] else {'primary supplier'}
        made = linked = 0
        for name in StockBatch.objects.filter(supplier__isnull=True).exclude(supplier_name='') \
                .values_list('supplier_name', flat=True).distinct():
            clean = ' '.join(name.split())
            if not clean or clean.lower() in skip:
                continue
            sup = Supplier.objects.filter(name__iexact=clean).first()
            if sup is None:
                sup = Supplier.objects.create(name=clean[:120])
                made += 1
            linked += StockBatch.objects.filter(supplier__isnull=True, supplier_name=name).update(supplier=sup)
        self.stdout.write(self.style.SUCCESS(f'{made} supplier(s) created, {linked} batch(es) linked.'))
