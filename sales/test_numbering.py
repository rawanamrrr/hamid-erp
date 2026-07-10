"""Document numbering tests (Phase 6.1)."""
from django.test import TestCase

from sales.models import DocumentSequence


class DocumentSequenceTests(TestCase):
    def test_sequential_gap_free(self):
        a = DocumentSequence.next_number('INV', year=2026)
        b = DocumentSequence.next_number('INV', year=2026)
        c = DocumentSequence.next_number('INV', year=2026)
        self.assertEqual(a, 'INV-2026-00001')
        self.assertEqual(b, 'INV-2026-00002')
        self.assertEqual(c, 'INV-2026-00003')

    def test_per_type_and_year_independent(self):
        self.assertEqual(DocumentSequence.next_number('INV', year=2026), 'INV-2026-00001')
        self.assertEqual(DocumentSequence.next_number('RET', year=2026), 'RET-2026-00001')
        self.assertEqual(DocumentSequence.next_number('INV', year=2027), 'INV-2027-00001')
        # INV-2026 continues independently
        self.assertEqual(DocumentSequence.next_number('INV', year=2026), 'INV-2026-00002')
