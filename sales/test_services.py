"""
Tests for the shared OrderService discount/total logic (Phase 2.2).

issue_cart_items is exercised end-to-end by the POS flow; here we lock down the pure
discount/total arithmetic that both create and edit now share, including the edge cases
that previously differed between the two code paths.
"""
from decimal import Decimal
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from sales.services import compute_discount_and_total
from products.models import Product
from financial.payroll_models import DealDiscount


class ComputeTotalTests(TestCase):
    def test_fixed_discount(self):
        r = compute_discount_and_total(Decimal('100'), Decimal('0'),
                                       discount=Decimal('15'), discount_type='fixed',
                                       applied_deal=None, delivery_cost=Decimal('10'))
        self.assertEqual(r['applied_discount'], Decimal('15'))
        self.assertEqual(r['total'], Decimal('95'))  # 100 - 15 + 10
        self.assertEqual(r['discount_type'], 'fixed')

    def test_percent_discount(self):
        r = compute_discount_and_total(Decimal('200'), Decimal('0'),
                                       discount=Decimal('10'), discount_type='percent',
                                       applied_deal=None, delivery_cost=Decimal('0'))
        self.assertEqual(r['applied_discount'], Decimal('20'))
        self.assertEqual(r['total'], Decimal('180'))

    def test_discount_never_makes_total_negative(self):
        r = compute_discount_and_total(Decimal('50'), Decimal('0'),
                                       discount=Decimal('999'), discount_type='fixed',
                                       applied_deal=None, delivery_cost=Decimal('0'))
        self.assertEqual(r['total'], Decimal('0'))

    def test_tailoring_cost_added(self):
        r = compute_discount_and_total(Decimal('100'), Decimal('0'),
                                       discount=Decimal('0'), discount_type='fixed',
                                       applied_deal=None, delivery_cost=Decimal('0'),
                                       tailoring_cost=Decimal('40'))
        self.assertEqual(r['total'], Decimal('140'))

    def _deal(self, dtype='PERCENTAGE', value='10', minimum='0'):
        now = timezone.now()
        return DealDiscount.objects.create(
            name='X', promo_type='discount', discount_type=dtype, value=Decimal(value),
            minimum_order_value=Decimal(minimum), apply_to_all=True,
            start_date=now - timedelta(days=1), end_date=now + timedelta(days=1),
        )

    def test_deal_percentage_uses_qualified_subtotal(self):
        deal = self._deal('PERCENTAGE', '10')
        r = compute_discount_and_total(Decimal('300'), Decimal('200'),
                                       discount=Decimal('0'), discount_type='fixed',
                                       applied_deal=deal, delivery_cost=Decimal('0'))
        self.assertEqual(r['applied_discount'], Decimal('20'))  # 10% of qualified 200
        self.assertEqual(r['total'], Decimal('280'))
        self.assertEqual(r['discount_type'], 'fixed')
        self.assertEqual(r['applied_deal'], deal)

    def test_deal_minimum_not_met_raises(self):
        deal = self._deal('FIXED', '50', minimum='500')
        with self.assertRaises(ValueError):
            compute_discount_and_total(Decimal('100'), Decimal('100'),
                                       discount=Decimal('0'), discount_type='fixed',
                                       applied_deal=deal, delivery_cost=Decimal('0'))
