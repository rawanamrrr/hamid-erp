"""Inventory insights tests (Phase 10.5)."""
from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from crm.models import Customer
from sales.models import Order, OrderItem
from products.models import Product
from products.insights import inventory_insights


def _p(sku, stock):
    return Product.objects.create(name=f'P{sku}', sku=sku, cost_price=Decimal('5'),
                                  price_retail=Decimal('10'), price_semi_wholesale=Decimal('9'),
                                  price_wholesale=Decimal('8'),
                                  stock_quantity=Decimal(stock), is_active=True)


class InsightsTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('in', password='x')
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='IN')

    def _sell(self, product, qty, days_ago=1):
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('10'),
                                 subtotal_amount=Decimal('10'), received_amount=Decimal('10'))
        OrderItem.objects.create(order=o, product=product, quantity=Decimal(qty), price=Decimal('10'),
                                 cost_price=Decimal('5'))
        Order.objects.filter(pk=o.pk).update(created_at=timezone.now() - timedelta(days=days_ago))

    def test_dead_stock_flagged(self):
        dead = _p('DEAD', '40')   # has stock, never sold
        d = inventory_insights(window_days=90)
        names = [r['product'].id for r in d['dead']]
        self.assertIn(dead.id, names)
        self.assertEqual(d['dead_count'], 1)

    def test_reorder_when_low_cover(self):
        fast = _p('FAST', '5')   # 5 in stock
        # sell 90 over the window -> ~1/day velocity -> 5 days cover < 14 lead -> reorder
        self._sell(fast, '90', days_ago=1)
        d = inventory_insights(window_days=90)
        row = next(r for r in d['rows'] if r['product'].id == fast.id)
        self.assertEqual(row['status'], 'reorder')
        self.assertTrue(d['reorder_count'] >= 1)

    def test_healthy_not_flagged(self):
        ok = _p('OK', '1000')
        self._sell(ok, '10', days_ago=1)  # tiny velocity, huge stock -> healthy
        d = inventory_insights(window_days=90)
        row = next(r for r in d['rows'] if r['product'].id == ok.id)
        self.assertEqual(row['status'], 'healthy')
