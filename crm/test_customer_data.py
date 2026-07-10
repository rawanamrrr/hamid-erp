"""Customer data fields tests (Phase 8.6)."""
from decimal import Decimal

from django.test import TestCase

from crm.models import Customer
from crm.forms import CustomerForm


class CustomerDataTests(TestCase):
    def test_new_fields_default(self):
        c = Customer.objects.create(first_name='A', last_name='B', phone='CD1')
        self.assertEqual(c.phone2, '')
        self.assertEqual(c.tax_number, '')
        self.assertFalse(c.is_blacklisted)

    def test_form_accepts_new_fields(self):
        form = CustomerForm(data={
            'first_name': 'X', 'last_name': 'Y', 'phone': 'CD2',
            'phone2': '0100', 'tax_number': '123-456', 'is_blacklisted': True,
            'customer_type': 'retail', 'opening_balance': '0', 'credit_limit': '0',
        })
        self.assertTrue(form.is_valid(), form.errors)
        c = form.save()
        self.assertTrue(c.is_blacklisted)
        self.assertEqual(c.tax_number, '123-456')
