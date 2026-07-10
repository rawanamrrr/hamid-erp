"""Voucher numbering tests (Phase 4.4)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from crm.models import Customer, CustomerPayment
from sales.models import DocumentSequence


class VoucherTests(TestCase):
    def test_receipt_voucher_sequence(self):
        a = DocumentSequence.next_number('RV', year=2026)
        b = DocumentSequence.next_number('RV', year=2026)
        self.assertEqual(a, 'RV-2026-00001')
        self.assertEqual(b, 'RV-2026-00002')

    def test_display_voucher_fallback(self):
        u = User.objects.create_user('v', password='x')
        c = Customer.objects.create(first_name='A', last_name='B', phone='VPH')
        p = CustomerPayment.objects.create(customer=c, user=u, amount=Decimal('10'),
                                           transaction_type='payment')
        self.assertEqual(p.display_voucher, f"#{p.id}")  # no voucher assigned -> id fallback
        p.voucher_number = 'RV-2026-00009'
        p.save(update_fields=['voucher_number'])
        self.assertEqual(p.display_voucher, 'RV-2026-00009')
