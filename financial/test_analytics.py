"""Sales analytics tests (Phase 9.6)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from crm.models import Customer
from sales.models import Order, OrderItem
from products.models import Product
from financial.reports import sales_analytics


def _p(sku, cost='4'):
    return Product.objects.create(name=f'P{sku}', sku=sku, cost_price=Decimal(cost),
                                  price_retail=Decimal('10'), price_semi_wholesale=Decimal('9'),
                                  price_wholesale=Decimal('8'))


class AnalyticsTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('an', password='x')
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='AN')

    def _order(self, total, cash):
        return Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal(total),
                                    subtotal_amount=Decimal(total), received_amount=Decimal(cash),
                                    cash_paid=Decimal(cash))

    def test_summary_and_best_sellers(self):
        pa, pb = _p('A'), _p('B')
        o1 = self._order('100', '100'); OrderItem.objects.create(order=o1, product=pa, quantity=Decimal('8'), price=Decimal('10'), cost_price=Decimal('4'))
        o2 = self._order('60', '60'); OrderItem.objects.create(order=o2, product=pb, quantity=Decimal('3'), price=Decimal('20'), cost_price=Decimal('4'))
        a = sales_analytics()
        self.assertEqual(a['summary']['invoices'], 2)
        self.assertEqual(a['summary']['total'], Decimal('160.00'))
        self.assertEqual(a['avg_basket'], Decimal('80.00'))
        self.assertEqual(a['best_qty'][0]['product__name'], pa.name)  # 8 > 3
        self.assertEqual(a['by_payment']['cash'], Decimal('160.00'))

    def test_empty_period_safe(self):
        a = sales_analytics()
        self.assertEqual(a['summary']['invoices'], 0)
        self.assertEqual(a['avg_basket'], Decimal('0.00'))
