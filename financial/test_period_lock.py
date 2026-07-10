"""Period-lock tests (Phase 4.5)."""
from datetime import date, timedelta

from django.test import TestCase
from django.utils import timezone

from financial.models import PeriodLock


class PeriodLockTests(TestCase):
    def test_no_lock_allows_all(self):
        self.assertFalse(PeriodLock.is_locked(timezone.now()))

    def test_dates_on_or_before_lock_are_locked(self):
        lock = PeriodLock.get_solo()
        lock.locked_up_to = date(2026, 5, 31)
        lock.save()
        self.assertTrue(PeriodLock.is_locked(date(2026, 5, 31)))   # boundary inclusive
        self.assertTrue(PeriodLock.is_locked(date(2026, 5, 1)))
        self.assertFalse(PeriodLock.is_locked(date(2026, 6, 1)))   # after lock = open

    def test_singleton_get_solo(self):
        a = PeriodLock.get_solo()
        b = PeriodLock.get_solo()
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(PeriodLock.objects.count(), 1)
