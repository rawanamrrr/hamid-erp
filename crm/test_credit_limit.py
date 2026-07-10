"""Credit-limit enforcement tests (Phase 6.9)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from crm.models import Customer, CustomerPayment


class CreditLimitTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('cl', password='x')

    def test_unlimited_when_zero(self):
        c = Customer.objects.create(first_name='A', last_name='B', phone='CL1', credit_limit=Decimal('0'))
        self.assertIsNone(c.credit_available())
        self.assertFalse(c.would_exceed_credit(Decimal('99999')))

    def test_available_and_exceed(self):
        c = Customer.objects.create(first_name='A', last_name='B', phone='CL2', credit_limit=Decimal('1000'))
        # owes 600 already
        CustomerPayment.objects.create(customer=c, user=self.u, amount=Decimal('600'), transaction_type='debt')
        self.assertEqual(c.credit_available(), Decimal('400'))
        self.assertFalse(c.would_exceed_credit(Decimal('400')))   # exactly at limit OK
        self.assertTrue(c.would_exceed_credit(Decimal('401')))    # over the limit
