"""Stocktake adjustment tests (Phase 5.1)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User
from django.db.models import Sum

from products.models import Product, Warehouse, WarehouseStock, StockBatch
from products.inventory_services import adjust_to


def _product():
    return Product.objects.create(name='ST', sku='ST1', cost_price=Decimal('5'),
                                  price_retail=Decimal('10'), price_semi_wholesale=Decimal('9'),
                                  price_wholesale=Decimal('8'))


class StocktakeTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(name='W', is_active=True)
        self.p = _product()
        StockBatch.objects.create(product=self.p, warehouse=self.wh, purchase_price=Decimal('5'),
                                  initial_quantity=Decimal('20'), current_quantity=Decimal('20'))
        WarehouseStock.objects.create(product=self.p, warehouse=self.wh, quantity=Decimal('20'))

    def _invariant_ok(self):
        bsum = StockBatch.objects.filter(product=self.p, warehouse=self.wh).aggregate(s=Sum('current_quantity'))['s'] or Decimal('0')
        ws = WarehouseStock.objects.get(product=self.p, warehouse=self.wh)
        return Decimal(str(bsum)) == ws.quantity

    def test_adjust_down(self):
        delta = adjust_to(self.p, self.wh, Decimal('15'))  # counted 15 vs system 20
        self.assertEqual(delta, Decimal('-5'))
        self.assertEqual(WarehouseStock.objects.get(product=self.p, warehouse=self.wh).quantity, Decimal('15'))
        self.assertTrue(self._invariant_ok())

    def test_adjust_up(self):
        delta = adjust_to(self.p, self.wh, Decimal('26'))  # counted 26 vs 20
        self.assertEqual(delta, Decimal('6'))
        self.assertEqual(WarehouseStock.objects.get(product=self.p, warehouse=self.wh).quantity, Decimal('26'))
        self.assertTrue(self._invariant_ok())

    def test_no_change_when_equal(self):
        delta = adjust_to(self.p, self.wh, Decimal('20'))
        self.assertEqual(delta, Decimal('0.00'))
        self.assertTrue(self._invariant_ok())
