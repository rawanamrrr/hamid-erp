"""
Advance ↔ payslip balance tests.

Verifies that recording an advance immediately withdraws the correct amount from
the source account, and that when a payslip is later paid the *net* salary (after
advance-installment deduction) is charged — so the account is debited exactly once
for the advance and once for the reduced salary, not twice for the full salary.
"""
from decimal import Decimal

from django.test import TestCase, RequestFactory
from django.contrib.auth.models import User

from financial.models import Account, Transaction
from financial.payroll_models import EmployeeAdvance, Payslip


def _make_account(name='Cashier', balance='5000'):
    return Account.objects.create(name=name, balance=Decimal(balance), is_active=True)


class AdvanceWithdrawalTest(TestCase):
    """Creating an advance with source_account debits that account immediately."""

    def setUp(self):
        self.emp = User.objects.create_user('emp_adv', password='x')
        self.cashier = _make_account('Cashier', '5000')
        self.admin = User.objects.create_superuser('admin_adv', password='x')

    def _create_advance(self, amount, installment=0):
        """Simulate what advance_create view does (atomically)."""
        from django.db import transaction as db_tx
        with db_tx.atomic():
            adv = EmployeeAdvance.objects.create(
                employee=self.emp,
                amount=Decimal(str(amount)),
                per_period_deduction=Decimal(str(installment)),
                source_account=self.cashier,
            )
            Transaction.objects.create(
                account=self.cashier,
                transaction_type='WITHDRAWAL',
                amount=Decimal(str(amount)),
                description=f'سلفة موظف: {self.emp.username}',
                created_by=self.admin,
            )
        return adv

    def test_account_debited_on_advance_create(self):
        """Account balance drops by exactly the advance amount when advance is created."""
        self.cashier.refresh_from_db()
        balance_before = self.cashier.balance
        self._create_advance(1000)
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.balance, balance_before - Decimal('1000'))

    def test_withdrawal_transaction_posted(self):
        """A WITHDRAWAL Transaction is recorded against the source account."""
        self._create_advance(500)
        tx = Transaction.objects.filter(account=self.cashier, transaction_type='WITHDRAWAL').last()
        self.assertIsNotNone(tx)
        self.assertEqual(tx.amount, Decimal('500'))

    def test_advance_remaining_set_on_create(self):
        """remaining is automatically set to amount on first save."""
        adv = self._create_advance(800, installment=200)
        self.assertEqual(adv.remaining, Decimal('800'))

    def test_source_account_stored(self):
        """source_account FK is saved on the advance record."""
        adv = self._create_advance(300)
        adv.refresh_from_db()
        self.assertEqual(adv.source_account_id, self.cashier.pk)


class PayslipNoDoubleCountTest(TestCase):
    """
    Full money-flow: give advance → pay salary net of installment.
    The account should be debited:
      - advance amount  (WITHDRAWAL at advance creation time)
      - net salary      (EXPENSE at payslip payment time = gross - installment)
    Total debit = advance + (gross - installment), NOT advance + gross.
    """

    def setUp(self):
        self.emp = User.objects.create_user('emp_ps', password='x')
        self.cashier = _make_account('Cashier', '10000')
        self.admin = User.objects.create_superuser('admin_ps', password='x')

    def _give_advance(self, amount, installment):
        from django.db import transaction as db_tx
        with db_tx.atomic():
            adv = EmployeeAdvance.objects.create(
                employee=self.emp,
                amount=Decimal(str(amount)),
                per_period_deduction=Decimal(str(installment)),
                source_account=self.cashier,
            )
            Transaction.objects.create(
                account=self.cashier,
                transaction_type='WITHDRAWAL',
                amount=Decimal(str(amount)),
                description='advance',
                created_by=self.admin,
            )
        return adv

    def _pay_salary(self, gross, period='2026-08'):
        """Simplified _pay_payslip logic: apply advance installment, pay net."""
        from django.db import transaction as db_tx

        ps = Payslip.objects.create(
            employee=self.emp,
            period_month=period,
            basic_salary=Decimal(str(gross)),
        )
        applied = Decimal('0')
        plan = []
        for a in self.emp.advances.filter(is_settled=False):
            due = a.due_amount()
            if due > 0:
                plan.append((a, due))
                applied += due
        ps.advance_deducted = applied
        net = ps.net  # gross - advance_deducted (clamped to 0)
        with db_tx.atomic():
            for a, due in plan:
                a.apply(due)
            Transaction.objects.create(
                account=self.cashier,
                transaction_type='EXPENSE',
                amount=net,
                description='salary',
                created_by=self.admin,
            )
            ps.status = 'paid'
            ps.net_paid = net
            ps.save()
        return ps, net

    def test_no_double_count_single_installment(self):
        """
        Advance 1000 (installment 300), salary gross 3000.
        Expected debits: 1000 (advance) + 2700 (salary net) = 3700 total.
        Account should end at 10000 - 3700 = 6300.
        """
        self.cashier.refresh_from_db()
        self._give_advance(1000, installment=300)
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.balance, Decimal('9000'))  # after advance

        ps, net = self._pay_salary(3000)
        self.assertEqual(net, Decimal('2700'))   # 3000 - 300 installment
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.balance, Decimal('6300'))  # 9000 - 2700

    def test_advance_settled_when_single_lump(self):
        """
        Advance 500 with no installment (per_period_deduction=0) → full amount deducted
        from first payslip, advance marked settled.
        """
        adv = self._give_advance(500, installment=0)
        ps, net = self._pay_salary(3000, period='2026-09')
        self.assertEqual(net, Decimal('2500'))   # 3000 - 500
        adv.refresh_from_db()
        self.assertTrue(adv.is_settled)
        self.assertEqual(adv.remaining, Decimal('0.00'))
