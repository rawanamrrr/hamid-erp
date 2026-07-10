"""Quantity-break price resolution tests (Phase 6.6)."""
from decimal import Decimal

from django.test import TestCase

from products.models import Product, ProductPriceBreak
from products.pricing import resolve_price, tier_price


def _product():
    return Product.objects.create(name='PB', sku='PB1', cost_price=Decimal('5'),
                                  price_retail=Decimal('20'), price_semi_wholesale=Decimal('18'),
                                  price_wholesale=Decimal('15'))


class PricingTests(TestCase):
    def setUp(self):
        self.p = _product()

    def test_tier_price(self):
        self.assertEqual(tier_price(self.p, 'retail'), Decimal('20'))
        self.assertEqual(tier_price(self.p, 'wholesale'), Decimal('15'))
        self.assertEqual(tier_price(self.p, ''), Decimal('20'))  # default retail

    def test_no_break_uses_tier(self):
        self.assertEqual(resolve_price(self.p, 'retail', 5), Decimal('20'))

    def test_quantity_break_applies(self):
        ProductPriceBreak.objects.create(product=self.p, min_quantity=Decimal('10'), unit_price=Decimal('17'))
        self.assertEqual(resolve_price(self.p, 'retail', 9), Decimal('20'))   # below threshold
        self.assertEqual(resolve_price(self.p, 'retail', 10), Decimal('17'))  # at threshold
        self.assertEqual(resolve_price(self.p, 'retail', 50), Decimal('17'))

    def test_best_of_multiple_breaks(self):
        ProductPriceBreak.objects.create(product=self.p, min_quantity=Decimal('10'), unit_price=Decimal('17'))
        ProductPriceBreak.objects.create(product=self.p, min_quantity=Decimal('100'), unit_price=Decimal('14'))
        self.assertEqual(resolve_price(self.p, 'retail', 100), Decimal('14'))
        self.assertEqual(resolve_price(self.p, 'retail', 50), Decimal('17'))

    def test_tier_scoped_break(self):
        ProductPriceBreak.objects.create(product=self.p, min_quantity=Decimal('10'),
                                         unit_price=Decimal('12'), customer_type='wholesale')
        # retail buyer doesn't get the wholesale-scoped break
        self.assertEqual(resolve_price(self.p, 'retail', 20), Decimal('20'))
        self.assertEqual(resolve_price(self.p, 'wholesale', 20), Decimal('12'))
