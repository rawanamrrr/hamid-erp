"""Payroll computation tests: payslip net, absence, adjustments, advances."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from financial.payroll_models import Payslip, PayslipAdjustment, EmployeeAdvance


class PayrollTests(TestCase):
    def setUp(self):
        self.emp = User.objects.create_user('emp', password='x')

    def test_net_with_adjustments_and_absence(self):
        ps = Payslip.objects.create(
            employee=self.emp, period_month='2026-06',
            basic_salary=Decimal('3000'), allowances=Decimal('500'),
            days_absent=Decimal('3'),  # 3000/30*3 = 300 absence
        )
        PayslipAdjustment.objects.create(payslip=ps, kind='bonus', amount=Decimal('200'))
        PayslipAdjustment.objects.create(payslip=ps, kind='overtime', amount=Decimal('100'))
        PayslipAdjustment.objects.create(payslip=ps, kind='deduction', amount=Decimal('150'))
        PayslipAdjustment.objects.create(payslip=ps, kind='penalty', amount=Decimal('50'))
        # gross = 3000 + 500 + (200+100) = 3800
        self.assertEqual(ps.gross, Decimal('3800'))
        self.assertEqual(ps.total_additions, Decimal('300'))
        self.assertEqual(ps.total_deductions, Decimal('200'))
        self.assertEqual(ps.absence_deduction, Decimal('300.00'))
        # net = 3800 - 200 - 300 - 0 = 3300
        self.assertEqual(ps.net, Decimal('3300.00'))

    def test_net_never_negative(self):
        ps = Payslip.objects.create(employee=self.emp, period_month='2026-07',
                                    basic_salary=Decimal('100'))
        PayslipAdjustment.objects.create(payslip=ps, kind='deduction', amount=Decimal('500'))
        self.assertEqual(ps.net, Decimal('0.00'))

    def test_advance_installment_and_apply(self):
        adv = EmployeeAdvance.objects.create(
            employee=self.emp, amount=Decimal('1000'), per_period_deduction=Decimal('300'))
        self.assertEqual(adv.remaining, Decimal('1000'))   # set from amount on create
        self.assertEqual(adv.due_amount(), Decimal('300'))
        adv.apply(Decimal('300'))
        self.assertEqual(adv.remaining, Decimal('700'))
        self.assertFalse(adv.is_settled)
        # full remaining when no installment set
        adv2 = EmployeeAdvance.objects.create(employee=self.emp, amount=Decimal('200'))
        self.assertEqual(adv2.due_amount(), Decimal('200'))
        adv2.apply(Decimal('200'))
        self.assertTrue(adv2.is_settled)
        self.assertEqual(adv2.due_amount(), Decimal('0.00'))
