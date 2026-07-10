"""
Invariant tests for the central inventory service (Phase 0.4 / 2.1).

Core invariant under test:
    Σ StockBatch.current_quantity  ==  WarehouseStock.quantity  ==  Product.stock_quantity
for every (product, warehouse), across issue / restore / FEFO / expiry paths.
"""
from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from products.models import Product, Warehouse, WarehouseStock, StockBatch, StockTransaction
from products.inventory_services import issue_stock, restore_stock, available_quantity


def _make_product(sku='T1', cost='10'):
    return Product.objects.create(
        name=f'Product {sku}', sku=sku, cost_price=Decimal(cost),
        price_retail=Decimal('20'), price_semi_wholesale=Decimal('18'),
        price_wholesale=Decimal('15'),
    )


class InventoryInvariantTests(TestCase):
    def setUp(self):
        self.wh = Warehouse.objects.create(name='Main', is_active=True, is_sales_point=True)
        self.p = _make_product()

    def _batch(self, qty, price='10', expiry=None):
        return StockBatch.objects.create(
            product=self.p, warehouse=self.wh, purchase_price=Decimal(price),
            initial_quantity=Decimal(qty), current_quantity=Decimal(qty), expiry_date=expiry,
        )

    def assert_invariant(self):
        batch_sum = sum(
            (b.current_quantity for b in StockBatch.objects.filter(product=self.p, warehouse=self.wh)),
            Decimal('0'),
        )
        ws = WarehouseStock.objects.get(product=self.p, warehouse=self.wh)
        self.p.refresh_from_db()
        self.assertEqual(batch_sum, ws.quantity, "batch sum != warehouse stock")
        self.assertEqual(ws.quantity, self.p.stock_quantity, "warehouse stock != product cache")

    def test_issue_keeps_invariant_and_weighted_cost(self):
        self._batch('10', '10')
        self._batch('10', '20')  # newer, dearer
        cost = issue_stock(self.p, self.wh, Decimal('15'), reference='S1', note='sale')
        # 10 @10 + 5 @20 = 200 over 15 units => 13.33 weighted
        self.assertAlmostEqual(float(cost), 200 / 15, places=2)
        self.assert_invariant()
        self.assertEqual(WarehouseStock.objects.get(product=self.p, warehouse=self.wh).quantity, Decimal('5.00'))

    def test_issue_then_restore_round_trips(self):
        self._batch('8', '10')
        issue_stock(self.p, self.wh, Decimal('5'), reference='S2')
        restore_stock(self.p, self.wh, Decimal('5'), reference='S2', transaction_type='RET_IN')
        self.assert_invariant()
        self.assertEqual(WarehouseStock.objects.get(product=self.p, warehouse=self.wh).quantity, Decimal('8.00'))

    def test_issue_insufficient_raises_and_no_partial_mutation(self):
        self._batch('3', '10')
        with self.assertRaises(ValueError):
            issue_stock(self.p, self.wh, Decimal('5'), reference='S3')
        # batch untouched (atomic rollback)
        self.assertEqual(StockBatch.objects.get(product=self.p, warehouse=self.wh).current_quantity, Decimal('3.00'))

    def test_expired_batch_not_auto_sold(self):
        yesterday = timezone.now().date() - timedelta(days=1)
        self._batch('10', '10', expiry=yesterday)   # expired
        self._batch('4', '12')                        # good
        # only 4 sellable -> asking 6 must fail even though 14 exist physically
        with self.assertRaises(ValueError):
            issue_stock(self.p, self.wh, Decimal('6'), reference='S4', block_expired=True)
        # selling 4 takes the GOOD batch, expired one stays put
        issue_stock(self.p, self.wh, Decimal('4'), reference='S4', block_expired=True)
        good = StockBatch.objects.get(product=self.p, warehouse=self.wh, expiry_date__isnull=True)
        self.assertEqual(good.current_quantity, Decimal('0.00'))
        self.assertEqual(available_quantity(self.p, self.wh), Decimal('0.00'))  # excludes expired

    def test_fefo_orders_by_expiry(self):
        soon = timezone.now().date() + timedelta(days=5)
        later = timezone.now().date() + timedelta(days=50)
        b_later = self._batch('10', '10', expiry=later)
        b_soon = self._batch('10', '10', expiry=soon)
        issue_stock(self.p, self.wh, Decimal('6'), reference='S5')
        b_soon.refresh_from_db(); b_later.refresh_from_db()
        self.assertEqual(b_soon.current_quantity, Decimal('4.00'))   # nearest expiry drained first
        self.assertEqual(b_later.current_quantity, Decimal('10.00'))
