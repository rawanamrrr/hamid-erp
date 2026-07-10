"""Fashion variant checkout: variant stock is deducted and recorded on the order line."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User
from django.db import transaction

from products.models import Product, ProductVariant, Size, Warehouse
from sales.models import Order
from sales.services import issue_cart_items


class VariantCheckoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('seller', password='x')
        self.wh = Warehouse.objects.create(name='Main')
        self.product = Product.objects.create(
            name='Shirt', sku='SH-V', cost_price=Decimal('50'),
            price_retail=Decimal('120'), price_semi_wholesale=Decimal('110'),
            price_wholesale=Decimal('100'), has_variants=True)
        self.size, _ = Size.objects.get_or_create(name='L', defaults={'size_type': 'alpha'})
        self.variant = ProductVariant.objects.create(
            product=self.product, size=self.size, color='أحمر', stock_quantity=Decimal('5'))

    def test_variant_line_deducts_variant_stock_and_records_variant(self):
        order = Order.objects.create(user=self.user, warehouse=self.wh)
        cart = [{'id': self.product.id, 'variant_id': self.variant.id,
                 'quantity': 2, 'price': 120, 'sell_unit': 'box'}]
        with transaction.atomic():
            subtotal, _ = issue_cart_items(order, cart, self.wh, self.user)
        self.variant.refresh_from_db()
        self.assertEqual(self.variant.stock_quantity, Decimal('3'))   # 5 - 2
        self.assertEqual(subtotal, Decimal('240'))                    # 2 × 120
        line = order.items.first()
        self.assertEqual(line.variant_id, self.variant.id)
        self.assertEqual(line.cost_price, Decimal('50'))              # COGS from parent product
