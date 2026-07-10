"""Low-stock / reorder report tests (Phase 5.7)."""
from decimal import Decimal

from django.test import TestCase, Client
from django.contrib.auth.models import User

from products.models import Product, Supplier


def _product(sku, stock, threshold, supplier=None, cost='10'):
    return Product.objects.create(
        name=f'P{sku}', sku=sku, cost_price=Decimal(cost),
        price_retail=Decimal('20'), price_semi_wholesale=Decimal('18'),
        price_wholesale=Decimal('15'),
        stock_quantity=Decimal(stock), low_stock_threshold=Decimal(threshold),
        supplier=supplier, is_active=True,
    )


class LowStockReportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('boss', 'b@x.com', 'x')
        self.client = Client()
        self.client.force_login(self.admin)
        self.sup = Supplier.objects.create(name='ACME')

    def test_only_low_and_out_listed(self):
        _product('OK', stock='100', threshold='5', supplier=self.sup)     # healthy -> excluded
        _product('LOW', stock='3', threshold='5', supplier=self.sup)      # low -> included
        _product('OUT', stock='0', threshold='5', supplier=self.sup)      # out -> included
        _product('NOTHRESH', stock='0', threshold='0', supplier=self.sup) # no threshold -> excluded from low view
        resp = self.client.get('/products/products/low-stock/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_items'], 2)

    def test_suggested_quantity_restocks_above_threshold(self):
        _product('LOW', stock='3', threshold='5', supplier=self.sup)  # target 2x=10, suggested=7
        resp = self.client.get('/products/products/low-stock/')
        row = resp.context['groups'][0]['rows'][0]
        self.assertEqual(row['suggested'], Decimal('7'))
        self.assertEqual(row['line_cost'], Decimal('70'))  # 7 * cost 10

    def test_out_only_filter(self):
        _product('LOW', stock='3', threshold='5', supplier=self.sup)
        _product('OUT', stock='0', threshold='5', supplier=self.sup)
        resp = self.client.get('/products/products/low-stock/?out=1')
        self.assertEqual(resp.context['total_items'], 1)  # only the out-of-stock one
