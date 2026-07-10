"""AR aging tests (Phase 8.1)."""
from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from crm.models import Customer, CustomerPayment
from crm.aging import customer_aging
from sales.models import Order, OrderItem


class AgingTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('ag', password='x')
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='AG')

    def _order(self, total, received, days_ago):
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal(total),
                                 subtotal_amount=Decimal(total), received_amount=Decimal(received),
                                 cash_paid=Decimal(received))
        OrderItem.objects.create(order=o, product=None, quantity=Decimal('1'), price=Decimal(total))
        # backdate created_at (auto_now_add) directly
        Order.objects.filter(pk=o.pk).update(created_at=timezone.now() - timedelta(days=days_ago))
        return o

    def test_buckets_by_age_and_reconcile(self):
        self._order('100', '0', days_ago=10)    # current
        self._order('200', '0', days_ago=45)    # 31-60
        self._order('50', '0', days_ago=120)    # 90+
        ag = customer_aging(self.c)
        self.assertEqual(ag['current'], Decimal('100.00'))
        self.assertEqual(ag['d30'], Decimal('200.00'))
        self.assertEqual(ag['d90'], Decimal('50.00'))
        self.assertEqual(ag['total'], self.c.get_balance())

    def test_fifo_payment_clears_oldest_first(self):
        self._order('100', '0', days_ago=120)   # old
        self._order('100', '0', days_ago=5)     # new
        # a 100 payment today should clear the OLD invoice first
        CustomerPayment.objects.create(customer=self.c, user=self.u, amount=Decimal('100'),
                                       transaction_type='payment')
        ag = customer_aging(self.c)
        self.assertEqual(ag['d90'], Decimal('0.00'))      # old one cleared
        self.assertEqual(ag['current'], Decimal('100.00'))  # new one remains
        self.assertEqual(ag['total'], self.c.get_balance())
