"""Operational-limit tests (Phase 3.2)."""
from decimal import Decimal

from django.test import TestCase
from django.contrib.auth.models import User


class OpLimitTests(TestCase):
    def _profile(self, user, **kw):
        p = user.profile
        for k, v in kw.items():
            setattr(p, k, v)
        p.save()
        return p

    def test_cashier_discount_cap(self):
        u = User.objects.create_user('cash', password='x')
        self._profile(u, max_discount_percent=Decimal('10'), is_master=False)
        self.assertEqual(u.profile.discount_cap(), Decimal('10'))
        self.assertFalse(u.profile.allows_below_cost())

    def test_master_uncapped(self):
        u = User.objects.create_user('owner', password='x')
        self._profile(u, max_discount_percent=Decimal('5'), is_master=True)
        self.assertEqual(u.profile.discount_cap(), Decimal('100.00'))
        self.assertTrue(u.profile.allows_below_cost())
        self.assertTrue(u.profile.allows_price_edit())

    def test_superuser_uncapped(self):
        u = User.objects.create_superuser('su', 's@x.com', 'x')
        self._profile(u, max_discount_percent=Decimal('5'), can_sell_below_cost=False)
        self.assertEqual(u.profile.discount_cap(), Decimal('100.00'))
        self.assertTrue(u.profile.allows_below_cost())

    def test_below_cost_flag(self):
        u = User.objects.create_user('cash2', password='x')
        self._profile(u, can_sell_below_cost=True, is_master=False)
        self.assertTrue(u.profile.allows_below_cost())
