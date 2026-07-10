"""X / Z drawer report tests (Phase 4.10)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from financial.models import DailyShift, Account, Transaction
from sales.models import Order
from crm.models import Customer


class ExpectedBalanceTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('z', password='x')
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='ZS')
        self.drawer = Account.objects.create(name='Drawer', account_type='CASH_DRAWER')
        self.shift = DailyShift.objects.create(employee=self.u, start_balance=Decimal('100'))

    def test_cash_refund_reduces_expected(self):
        # cash sale of 200 into the drawer
        Order.objects.create(user=self.u, customer=self.c, total_amount=Decimal('200'),
                             subtotal_amount=Decimal('200'), received_amount=Decimal('200'),
                             cash_paid=Decimal('200'))
        # a 50 cash refund leaves the drawer
        Transaction.objects.create(shift=self.shift, account=self.drawer,
                                   transaction_type='REFUND', amount=Decimal('50'),
                                   description='refund', created_by=self.u)
        expected, cash_sales = self.shift.calculate_expected_balance()
        # 100 start + 200 sales - 50 refund = 250
        self.assertEqual(cash_sales, Decimal('200.00'))
        self.assertEqual(expected, Decimal('250.00'))

    def test_expense_and_withdrawal_reduce_expected(self):
        Transaction.objects.create(shift=self.shift, account=self.drawer,
                                   transaction_type='EXPENSE', amount=Decimal('30'),
                                   description='exp', created_by=self.u)
        Transaction.objects.create(shift=self.shift, account=self.drawer,
                                   transaction_type='WITHDRAWAL', amount=Decimal('20'),
                                   description='wd', created_by=self.u)
        expected, _ = self.shift.calculate_expected_balance()
        self.assertEqual(expected, Decimal('50.00'))  # 100 - 30 - 20
