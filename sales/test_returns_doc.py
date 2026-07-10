"""Sales-return document tests (Phase 6.4)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User

from sales.models import ReturnInvoice, DocumentSequence


class ReturnDocumentTests(TestCase):
    def test_return_number_sequence(self):
        a = DocumentSequence.next_number('RET', year=2026)
        b = DocumentSequence.next_number('RET', year=2026)
        self.assertEqual(a, 'RET-2026-00001')
        self.assertEqual(b, 'RET-2026-00002')

    def test_display_number_fallback(self):
        u = User.objects.create_user('r', password='x')
        ri = ReturnInvoice.objects.create(user=u, total_refund_amount=Decimal('10'))
        self.assertEqual(ri.display_number, f"RET#{ri.id}")
        ri.return_number = 'RET-2026-00005'
        ri.save(update_fields=['return_number'])
        self.assertEqual(ri.display_number, 'RET-2026-00005')

    def test_reason_category_choice(self):
        u = User.objects.create_user('r2', password='x')
        ri = ReturnInvoice.objects.create(user=u, total_refund_amount=Decimal('10'),
                                          reason_category='defect')
        self.assertEqual(ri.get_reason_category_display(), 'عيب صناعة / تالف')
