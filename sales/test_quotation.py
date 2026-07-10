"""Quotation tests (Phase 6.2)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from crm.models import Customer
from sales.models import Quotation, QuotationItem, DocumentSequence


class QuotationTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user('q', password='x')
        self.c = Customer.objects.create(first_name='A', last_name='B', phone='Q1')

    def test_number_sequence(self):
        a = DocumentSequence.next_number('QUO', year=2026)
        b = DocumentSequence.next_number('QUO', year=2026)
        self.assertEqual(a, 'QUO-2026-00001')
        self.assertEqual(b, 'QUO-2026-00002')

    def test_total_and_display(self):
        q = Quotation.objects.create(customer=self.c, number='QUO-2026-00001', created_by=self.u)
        QuotationItem.objects.create(quotation=q, product=None, description='X', quantity=Decimal('3'), unit_price=Decimal('10'))
        QuotationItem.objects.create(quotation=q, product=None, description='Y', quantity=Decimal('2'), unit_price=Decimal('25'))
        self.assertEqual(q.total, Decimal('80.00'))  # 30 + 50
        self.assertEqual(q.display_number, 'QUO-2026-00001')
        self.assertEqual(q.display_customer, 'A B')

    def test_display_number_fallback(self):
        q = Quotation.objects.create(customer_name='عميل نقدي', created_by=self.u)
        self.assertEqual(q.display_number, f'QUO#{q.id}')
        self.assertEqual(q.display_customer, 'عميل نقدي')
