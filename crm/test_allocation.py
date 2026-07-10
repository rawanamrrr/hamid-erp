"""Payment allocation tests (Phase 8.2)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from crm.models import Customer, CustomerPayment, PaymentAllocation
from crm.allocation import allocate_payment_fifo, order_outstanding
from sales.models import Order, OrderItem


class AllocationTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('al', password='x')
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='AL')

    def _credit_order(self, total):
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal(total),
                                 subtotal_amount=Decimal(total), received_amount=Decimal('0'))
        OrderItem.objects.create(order=o, product=None, quantity=Decimal('1'), price=Decimal(total))
        return o

    def test_fifo_allocation_fills_oldest_first(self):
        o1 = self._credit_order('100')
        o2 = self._credit_order('100')
        pay = CustomerPayment.objects.create(customer=self.c, user=self.u, amount=Decimal('120'),
                                             transaction_type='payment')
        remainder = allocate_payment_fifo(pay)
        self.assertEqual(remainder, Decimal('0.00'))
        self.assertEqual(order_outstanding(o1), Decimal('0.00'))   # fully paid
        self.assertEqual(order_outstanding(o2), Decimal('80.00'))  # 20 of 100 paid

    def test_overpayment_returns_remainder(self):
        o1 = self._credit_order('50')
        pay = CustomerPayment.objects.create(customer=self.c, user=self.u, amount=Decimal('80'),
                                             transaction_type='payment')
        remainder = allocate_payment_fifo(pay)
        self.assertEqual(order_outstanding(o1), Decimal('0.00'))
        self.assertEqual(remainder, Decimal('30.00'))  # 30 left as customer credit

    def test_return_ledger_entries_not_allocated(self):
        self._credit_order('100')
        pay = CustomerPayment.objects.create(customer=self.c, user=self.u, amount=Decimal('40'),
                                             transaction_type='payment', payment_method='return_credit')
        allocate_payment_fifo(pay)
        self.assertEqual(PaymentAllocation.objects.filter(payment=pay).count(), 0)
