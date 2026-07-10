"""Daily movement summary tests (Phase 9.1)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from crm.models import Customer
from sales.models import Order
from financial.reports import daily_summary


class DailySummaryTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('d', password='x')
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='DS')

    def test_sales_and_net_cash(self):
        today = timezone.localdate()
        Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('100'),
                             subtotal_amount=Decimal('100'), received_amount=Decimal('100'),
                             cash_paid=Decimal('100'))
        Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('80'),
                             subtotal_amount=Decimal('80'), received_amount=Decimal('30'),
                             cash_paid=Decimal('30'))  # 50 credit
        d = daily_summary(today)
        self.assertEqual(d['sales']['count'], 2)
        self.assertEqual(d['sales']['total'], Decimal('180.00'))
        self.assertEqual(d['credit_sales'], Decimal('50.00'))  # 180 - 130 received
        self.assertEqual(d['cash_in'], Decimal('130.00'))      # 100 + 30 cash
        self.assertEqual(d['net_cash'], Decimal('130.00'))

    def test_void_excluded(self):
        today = timezone.localdate()
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('100'),
                                 subtotal_amount=Decimal('100'), received_amount=Decimal('100'),
                                 cash_paid=Decimal('100'))
        o.status = Order.STATUS_VOID
        o.save(update_fields=['status'])
        d = daily_summary(today)
        self.assertEqual(d['sales']['count'], 0)
        self.assertEqual(d['cash_in'], Decimal('0.00'))
