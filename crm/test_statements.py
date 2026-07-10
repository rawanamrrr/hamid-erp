"""
Customer account-statement tests (Phase 4.3).

The statement is the printable view of the subledger; its closing balance must always
equal Customer.get_balance() so the document and the system never disagree.
"""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from crm.models import Customer, CustomerPayment
from crm.statements import build_customer_statement
from sales.models import Order, OrderItem


class CustomerStatementTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('s', password='x')
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='SP',
                                         opening_balance=Decimal('50'))

    def _order(self, total, received):
        o = Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal(total),
                                 subtotal_amount=Decimal(total), received_amount=Decimal(received),
                                 cash_paid=Decimal(received))
        OrderItem.objects.create(order=o, product=None, quantity=Decimal('1'), price=Decimal(total))
        return o

    def test_closing_equals_get_balance(self):
        self._order('100', '40')          # +60 debt
        self._order('200', '200')         # paid in full
        CustomerPayment.objects.create(customer=self.c, user=self.u, amount=Decimal('30'),
                                       transaction_type='payment')
        st = build_customer_statement(self.c)
        self.assertEqual(st['closing'], self.c.get_balance())
        # opening (50) + 60 (debt from first order) - 30 (payment) = 80
        self.assertEqual(st['closing'], Decimal('80.00'))

    def test_void_order_excluded_from_statement(self):
        o = self._order('100', '0')
        st = build_customer_statement(self.c)
        self.assertEqual(st['closing'], Decimal('150.00'))  # opening 50 + 100
        o.status = Order.STATUS_VOID
        o.save(update_fields=['status'])
        st2 = build_customer_statement(self.c)
        self.assertEqual(st2['closing'], Decimal('50.00'))  # back to opening
        self.assertEqual(st2['closing'], self.c.get_balance())

    def test_running_balance_is_monotonic_consistent(self):
        self._order('100', '0')
        self._order('50', '0')
        st = build_customer_statement(self.c)
        # rows' final running balance equals closing
        self.assertEqual(st['rows'][-1]['balance'], st['closing'])
