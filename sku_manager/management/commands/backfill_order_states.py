from django.core.management.base import BaseCommand

from sku_manager.geo import detect_state, extract_pincode
from sku_manager.models import MarketplaceOrder


class Command(BaseCommand):
    help = 'Fill state / pincode on existing marketplace orders from their shipping address.'

    def handle(self, *args, **options):
        changed = missing = 0
        for order in MarketplaceOrder.objects.exclude(shipping_address=''):
            state = detect_state(order.shipping_address)
            pin = extract_pincode(order.shipping_address)
            if not state:
                missing += 1
            if (state, pin) != (order.state, order.pincode):
                MarketplaceOrder.objects.filter(pk=order.pk).update(state=state or order.state, pincode=pin or order.pincode)
                changed += 1
        self.stdout.write(self.style.SUCCESS(f'Updated {changed} orders; {missing} had no recognisable state.'))
