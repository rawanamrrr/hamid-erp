"""Supplier price comparison tests (Phase 7.5)."""
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth.models import User

from products.models import Product, Supplier, SupplierProduct


def _p(sku):
    return Product.objects.create(name=f'P{sku}', sku=sku, cost_price=Decimal('0'),
                                  price_retail=Decimal('10'), price_semi_wholesale=Decimal('9'),
                                  price_wholesale=Decimal('8'))


class PriceComparisonTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('pc', 'p@x.com', 'x')
        self.client = Client(); self.client.force_login(self.admin)
        self.s1 = Supplier.objects.create(name='S1')
        self.s2 = Supplier.objects.create(name='S2')

    def test_cheapest_flagged_and_spread(self):
        p = _p('CMP')
        SupplierProduct.objects.create(supplier=self.s1, product=p, last_purchase_price=Decimal('30'))
        SupplierProduct.objects.create(supplier=self.s2, product=p, last_purchase_price=Decimal('25'))
        resp = self.client.get('/products/suppliers/price-comparison/?multi=1')
        self.assertEqual(resp.status_code, 200)
        rows = resp.context['rows']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['cheapest'], Decimal('25'))
        self.assertEqual(rows[0]['spread'], Decimal('5'))
        cheapest = [o for o in rows[0]['offers'] if o.is_cheapest]
        self.assertEqual(len(cheapest), 1)
        self.assertEqual(cheapest[0].supplier, self.s2)

    def test_multi_filter_hides_single_supplier(self):
        p = _p('ONE')
        SupplierProduct.objects.create(supplier=self.s1, product=p, last_purchase_price=Decimal('10'))
        resp = self.client.get('/products/suppliers/price-comparison/?multi=1')
        self.assertEqual(len(resp.context['rows']), 0)
