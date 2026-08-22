"""Shift punch-pairing + duplicate-punch debounce tests (attendance_devices.sync).

Covers three related rules:
  1. Sequential shift pairing: an open shift's checkout must give way to the next
     shift's arrival once that shift has actually started (_bucket_punches_by_shift).
  2. Debounce: repeated fingerprint taps within payroll.punch_debounce_minutes of the
     last ACCEPTED punch are fully ignored, unless they cross into the next shift.
  3. Overnight shifts (configured end <= start, e.g. 22:00 -> 06:00): a checkout punch
     after midnight must still close the shift that opened the previous evening, dated
     under its own start date, without breaking normal same-day shifts or the rules
     above (see OvernightShiftTests).
"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from attendance_devices.models import AttendanceDevice, DeviceEmployeeMapping, DevicePunch
from attendance_devices.sync import process_punches_into_attendance
from financial.payroll_models import AttendanceRecord
from settings.models import SystemPolicy


class ShiftPunchPairingTests(TestCase):
    """Shift 1: 09:00-17:00, Shift 2: 18:00-22:00, both with a 15-minute grace period —
    matches the scenarios in the feature request exactly."""

    def setUp(self):
        self.emp = User.objects.create_user('shiftemp', password='x')
        self.device = AttendanceDevice.objects.create(
            name='TestDevice', adapter_type='csv_import', enabled=True)
        DeviceEmployeeMapping.objects.create(
            device=self.device, device_user_id='UID1', employee=self.emp)

        policy, _ = SystemPolicy.objects.get_or_create(pk=1)
        policy.values.update({
            'payroll.shift_count': '2',
            'payroll.shift_1_start': '09:00',
            'payroll.shift_1_end': '17:00',
            'payroll.shift_1_grace_minutes': 15,
            'payroll.shift_2_start': '18:00',
            'payroll.shift_2_end': '22:00',
            'payroll.shift_2_grace_minutes': 15,
            'payroll.punch_debounce_minutes': 60,
        })
        policy.save()
        self.today = timezone.localdate()

    def _punch(self, hh, mm, dedup, day=None):
        day = day or self.today
        ts = timezone.make_aware(datetime.datetime.combine(day, datetime.time(hh, mm)))
        return DevicePunch.objects.create(
            device=self.device, device_user_id='UID1', employee=self.emp,
            punch_timestamp=ts, dedup_key=dedup)

    def _records(self):
        return list(AttendanceRecord.objects.filter(employee=self.emp, date=self.today)
                    .order_by('shift_index'))

    def test_normal_two_shift_day(self):
        self._punch(9, 10, 'a1')
        self._punch(17, 5, 'a2')
        self._punch(18, 20, 'a3')
        self._punch(22, 5, 'a4')
        process_punches_into_attendance(employee_id=self.emp.id)

        recs = self._records()
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0].shift_index, 1)
        self.assertEqual(recs[0].arrival_time, datetime.time(9, 10))
        self.assertEqual(recs[0].departure_time, datetime.time(17, 5))
        self.assertEqual(recs[1].shift_index, 2)
        self.assertEqual(recs[1].arrival_time, datetime.time(18, 20))
        self.assertEqual(recs[1].departure_time, datetime.time(22, 5))

    def test_checkout_at_scheduled_end(self):
        self._punch(9, 0, 'b1')
        self._punch(17, 0, 'b2')
        process_punches_into_attendance(employee_id=self.emp.id)

        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].shift_index, 1)
        self.assertEqual(recs[0].arrival_time, datetime.time(9, 0))
        self.assertEqual(recs[0].departure_time, datetime.time(17, 0))

    def test_checkout_late_still_belongs_to_shift_one(self):
        # 17:30 and 17:50 are both after shift 1's scheduled end but before shift 2
        # starts (18:00) — must still close shift 1, not open a bogus new bucket.
        for minute in (30, 50):
            with self.subTest(minute=minute):
                DevicePunch.objects.filter(employee=self.emp).delete()
                AttendanceRecord.objects.filter(employee=self.emp, date=self.today).delete()

                self._punch(9, 10, f'c1-{minute}')
                self._punch(17, minute, f'c2-{minute}')
                process_punches_into_attendance(employee_id=self.emp.id)

                recs = self._records()
                self.assertEqual(len(recs), 1)
                self.assertEqual(recs[0].shift_index, 1)
                self.assertEqual(recs[0].arrival_time, datetime.time(9, 10))
                self.assertEqual(recs[0].departure_time, datetime.time(17, minute))

    def test_missing_checkout_then_shift_two_arrival(self):
        self._punch(9, 10, 'd1')
        self._punch(18, 20, 'd2')
        self._punch(22, 0, 'd3')
        process_punches_into_attendance(employee_id=self.emp.id)

        recs = self._records()
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0].shift_index, 1)
        self.assertEqual(recs[0].arrival_time, datetime.time(9, 10))
        self.assertIsNone(recs[0].departure_time)
        self.assertEqual(recs[1].shift_index, 2)
        self.assertEqual(recs[1].arrival_time, datetime.time(18, 20))
        self.assertEqual(recs[1].departure_time, datetime.time(22, 0))

    def test_punches_around_the_shift_boundary(self):
        # 17:59 is still before shift 2's start (18:00) -> closes shift 1.
        # 18:00 itself is at-or-after the boundary -> opens shift 2, not shift 1's checkout.
        self._punch(9, 10, 'e1')
        self._punch(17, 59, 'e2')
        self._punch(18, 0, 'e3')
        self._punch(22, 0, 'e4')
        process_punches_into_attendance(employee_id=self.emp.id)

        recs = self._records()
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0].shift_index, 1)
        self.assertEqual(recs[0].arrival_time, datetime.time(9, 10))
        self.assertEqual(recs[0].departure_time, datetime.time(17, 59))
        self.assertEqual(recs[1].shift_index, 2)
        self.assertEqual(recs[1].arrival_time, datetime.time(18, 0))
        self.assertEqual(recs[1].departure_time, datetime.time(22, 0))

    def test_cross_day_punch_never_closes_previous_day(self):
        # A missed checkout today must never be filled in by tomorrow's fresh arrival —
        # punches are grouped by calendar date before any pairing/debounce logic runs.
        tomorrow = self.today + datetime.timedelta(days=1)
        self._punch(9, 15, 'f1')
        self._punch(9, 5, 'f2', day=tomorrow)
        process_punches_into_attendance(employee_id=self.emp.id)

        today_rec = AttendanceRecord.objects.get(employee=self.emp, date=self.today, shift_index=1)
        tomorrow_rec = AttendanceRecord.objects.get(employee=self.emp, date=tomorrow, shift_index=1)
        self.assertEqual(today_rec.arrival_time, datetime.time(9, 15))
        self.assertIsNone(today_rec.departure_time)
        self.assertEqual(tomorrow_rec.arrival_time, datetime.time(9, 5))
        self.assertIsNone(tomorrow_rec.departure_time)


class PunchDebounceTests(TestCase):
    """Shift 1: 09:00-17:00, Shift 2: 18:00-22:00, debounce window: 60 minutes."""

    def setUp(self):
        self.emp = User.objects.create_user('debounceemp', password='x')
        self.device = AttendanceDevice.objects.create(
            name='DebounceDevice', adapter_type='csv_import', enabled=True)
        DeviceEmployeeMapping.objects.create(
            device=self.device, device_user_id='DUID', employee=self.emp)

        policy, _ = SystemPolicy.objects.get_or_create(pk=1)
        policy.values.update({
            'payroll.shift_count': '2',
            'payroll.shift_1_start': '09:00',
            'payroll.shift_1_end': '17:00',
            'payroll.shift_1_grace_minutes': 15,
            'payroll.shift_2_start': '18:00',
            'payroll.shift_2_end': '22:00',
            'payroll.shift_2_grace_minutes': 15,
            'payroll.punch_debounce_minutes': 60,
        })
        policy.save()
        self.today = timezone.localdate()

    def _punch(self, hh, mm, dedup):
        ts = timezone.make_aware(datetime.datetime.combine(self.today, datetime.time(hh, mm)))
        return DevicePunch.objects.create(
            device=self.device, device_user_id='DUID', employee=self.emp,
            punch_timestamp=ts, dedup_key=dedup)

    def _records(self):
        return list(AttendanceRecord.objects.filter(employee=self.emp, date=self.today)
                    .order_by('shift_index'))

    def test_repeated_punches_within_one_hour_are_ignored(self):
        self._punch(9, 5, 'g1')
        self._punch(9, 20, 'g2')
        self._punch(9, 45, 'g3')
        self._punch(10, 0, 'g4')
        result = process_punches_into_attendance(employee_id=self.emp.id)

        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].arrival_time, datetime.time(9, 5))
        self.assertIsNone(recs[0].departure_time)
        # Every punch (1 accepted + 3 debounced) must be marked processed so a later sync
        # run never re-evaluates the ignored ones with no debounce baseline.
        self.assertEqual(DevicePunch.objects.filter(employee=self.emp, processed=False).count(), 0)

    def test_punch_exactly_at_one_hour_is_not_ignored(self):
        self._punch(9, 5, 'h1')
        self._punch(10, 5, 'h2')  # exactly 60 minutes later
        process_punches_into_attendance(employee_id=self.emp.id)

        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].arrival_time, datetime.time(9, 5))
        self.assertEqual(recs[0].departure_time, datetime.time(10, 5))

    def test_punch_after_more_than_one_hour_is_accepted(self):
        self._punch(9, 5, 'i1')
        self._punch(10, 10, 'i2')  # 65 minutes later
        process_punches_into_attendance(employee_id=self.emp.id)

        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].arrival_time, datetime.time(9, 5))
        self.assertEqual(recs[0].departure_time, datetime.time(10, 10))

    def test_debounce_window_compares_against_last_accepted_not_last_seen(self):
        # 09:05 accepted. 09:20/09:45/10:00 are each < 60min from 09:05 (the last
        # ACCEPTED punch) so all three are ignored — not just the first one, and not
        # re-armed by comparing against the previous (also-ignored) punch.
        self._punch(9, 5, 'j1')
        self._punch(9, 20, 'j2')
        self._punch(9, 45, 'j3')
        self._punch(10, 0, 'j4')
        self._punch(10, 10, 'j5')  # 65 minutes after 09:05 -> accepted as checkout
        process_punches_into_attendance(employee_id=self.emp.id)

        recs = self._records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0].arrival_time, datetime.time(9, 5))
        self.assertEqual(recs[0].departure_time, datetime.time(10, 10))

    def test_shift_boundary_crossing_overrides_debounce(self):
        # Shift 1 checkout at 17:50 (accepted). A punch at 18:10 is only 20 minutes
        # later (within the 60-minute debounce window) but it is AFTER shift 2's start
        # (18:00), so it must NOT be discarded — it becomes shift 2's arrival.
        self._punch(9, 10, 'k1')
        self._punch(17, 50, 'k2')
        self._punch(18, 10, 'k3')
        process_punches_into_attendance(employee_id=self.emp.id)

        recs = self._records()
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0].shift_index, 1)
        self.assertEqual(recs[0].arrival_time, datetime.time(9, 10))
        self.assertEqual(recs[0].departure_time, datetime.time(17, 50))
        self.assertEqual(recs[1].shift_index, 2)
        self.assertEqual(recs[1].arrival_time, datetime.time(18, 10))
        self.assertIsNone(recs[1].departure_time)

    def test_debounced_punches_never_reappear_on_a_later_sync_run(self):
        self._punch(9, 5, 'l1')
        self._punch(9, 20, 'l2')
        process_punches_into_attendance(employee_id=self.emp.id)
        first = self._records()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].arrival_time, datetime.time(9, 5))

        # A second sync run with nothing new unprocessed must be a pure no-op.
        result = process_punches_into_attendance(employee_id=self.emp.id)
        self.assertEqual(result['groups_processed'], 0)
        second = self._records()
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0].arrival_time, datetime.time(9, 5))


class OvernightShiftTests(TestCase):
    """Shift 1 (daytime): 09:00-17:00. Shift 2 (overnight): 22:00-06:00. Both with a
    15-minute grace period and the standard 60-minute debounce window."""

    def setUp(self):
        self.emp = User.objects.create_user('overnightemp', password='x')
        self.device = AttendanceDevice.objects.create(
            name='OvernightDevice', adapter_type='csv_import', enabled=True)
        DeviceEmployeeMapping.objects.create(
            device=self.device, device_user_id='OUID', employee=self.emp)

        policy, _ = SystemPolicy.objects.get_or_create(pk=1)
        policy.values.update({
            'payroll.shift_count': '2',
            'payroll.shift_1_start': '09:00',
            'payroll.shift_1_end': '17:00',
            'payroll.shift_1_grace_minutes': 15,
            'payroll.shift_2_start': '22:00',
            'payroll.shift_2_end': '06:00',
            'payroll.shift_2_grace_minutes': 15,
            'payroll.punch_debounce_minutes': 60,
        })
        policy.save()
        self.day1 = timezone.localdate()
        self.day2 = self.day1 + datetime.timedelta(days=1)
        self.day3 = self.day1 + datetime.timedelta(days=2)

    def _punch(self, day, hh, mm, dedup):
        ts = timezone.make_aware(datetime.datetime.combine(day, datetime.time(hh, mm)))
        return DevicePunch.objects.create(
            device=self.device, device_user_id='OUID', employee=self.emp,
            punch_timestamp=ts, dedup_key=dedup)

    def _record(self, day, shift_index):
        return AttendanceRecord.objects.get(employee=self.emp, date=day, shift_index=shift_index)

    def test_normal_overnight_shift(self):
        self._punch(self.day1, 22, 0, 'o1')
        self._punch(self.day2, 6, 0, 'o2')
        process_punches_into_attendance(employee_id=self.emp.id)

        rec = self._record(self.day1, 2)
        self.assertEqual(rec.arrival_time, datetime.time(22, 0))
        self.assertEqual(rec.departure_time, datetime.time(6, 0))
        self.assertEqual(rec.late_minutes, 0)
        self.assertEqual(rec.early_departure_minutes, 0)
        # No stray record was created on day2 for this same overnight shift.
        self.assertFalse(AttendanceRecord.objects.filter(employee=self.emp, date=self.day2).exists())

    def test_arrival_before_midnight_checkout_after_midnight(self):
        # Late arrival (22:20) and an early checkout (05:55, 5 min before the 06:00 end).
        self._punch(self.day1, 22, 20, 'p1')
        self._punch(self.day2, 5, 55, 'p2')
        process_punches_into_attendance(employee_id=self.emp.id)

        rec = self._record(self.day1, 2)
        self.assertEqual(rec.arrival_time, datetime.time(22, 20))
        self.assertEqual(rec.departure_time, datetime.time(5, 55))
        self.assertEqual(rec.late_minutes, 5)          # 20 min late - 15 grace
        self.assertEqual(rec.early_departure_minutes, 5)  # left 5 min before 06:00

    def test_missing_overnight_checkout(self):
        # Only an arrival punch — no checkout at all for this shift. It must stay open
        # (departure=None), not get filled in by some unrelated later punch.
        self._punch(self.day1, 22, 5, 'q1')
        process_punches_into_attendance(employee_id=self.emp.id)

        rec = self._record(self.day1, 2)
        self.assertEqual(rec.arrival_time, datetime.time(22, 5))
        self.assertIsNone(rec.departure_time)

    def test_multiple_punches_after_midnight(self):
        # Arrival, then several close-together early-morning taps around checkout time
        # (e.g. the sensor didn't read cleanly the first couple of tries) — only the
        # FIRST one should be accepted as the checkout; the rest, all within the
        # debounce window of it, must be ignored rather than reopening/closing anything.
        self._punch(self.day1, 22, 5, 'r1')
        self._punch(self.day2, 5, 50, 'r2')   # >1hr after 22:05 -> accepted checkout
        self._punch(self.day2, 5, 53, 'r3')   # within 1hr of 05:50 -> debounced
        self._punch(self.day2, 5, 58, 'r4')   # within 1hr of 05:50 -> debounced
        process_punches_into_attendance(employee_id=self.emp.id)

        rec = self._record(self.day1, 2)
        self.assertEqual(rec.arrival_time, datetime.time(22, 5))
        self.assertEqual(rec.departure_time, datetime.time(5, 50))
        # Exactly one AttendanceRecord — the debounced taps never opened a second one.
        self.assertEqual(AttendanceRecord.objects.filter(employee=self.emp).count(), 1)
        self.assertEqual(DevicePunch.objects.filter(employee=self.emp, processed=False).count(), 0)

    def test_two_consecutive_overnight_shifts_on_different_dates(self):
        self._punch(self.day1, 22, 5, 's1')
        self._punch(self.day2, 5, 55, 's2')
        self._punch(self.day2, 22, 10, 's3')
        self._punch(self.day3, 6, 0, 's4')
        process_punches_into_attendance(employee_id=self.emp.id)

        rec1 = self._record(self.day1, 2)
        self.assertEqual(rec1.arrival_time, datetime.time(22, 5))
        self.assertEqual(rec1.departure_time, datetime.time(5, 55))

        rec2 = self._record(self.day2, 2)
        self.assertEqual(rec2.arrival_time, datetime.time(22, 10))
        self.assertEqual(rec2.departure_time, datetime.time(6, 0))

        # Exactly two overnight records — no stray/duplicate rows anywhere.
        self.assertEqual(AttendanceRecord.objects.filter(employee=self.emp).count(), 2)

    def test_normal_daytime_shift_still_works_with_overnight_shift_configured(self):
        # Shift 1 (daytime) must behave exactly as before even though shift 2 is
        # overnight — including "forgot to check out, comes back the next morning"
        # never bleeding into a fresh new day.
        self._punch(self.day1, 9, 10, 't1')
        self._punch(self.day1, 17, 5, 't2')
        self._punch(self.day2, 9, 0, 't3')
        process_punches_into_attendance(employee_id=self.emp.id)

        rec1 = self._record(self.day1, 1)
        self.assertEqual(rec1.arrival_time, datetime.time(9, 10))
        self.assertEqual(rec1.departure_time, datetime.time(17, 5))

        rec2 = self._record(self.day2, 1)
        self.assertEqual(rec2.arrival_time, datetime.time(9, 0))
        self.assertIsNone(rec2.departure_time)

    def test_forgotten_daytime_checkout_never_bleeds_past_midnight(self):
        # A missed daytime checkout must never be picked up by an early-morning punch
        # the next day, even with an overnight shift configured elsewhere.
        self._punch(self.day1, 9, 10, 'u1')
        self._punch(self.day2, 0, 30, 'u2')  # just after midnight, still "next day"
        process_punches_into_attendance(employee_id=self.emp.id)

        rec1 = self._record(self.day1, 1)
        self.assertEqual(rec1.arrival_time, datetime.time(9, 10))
        self.assertIsNone(rec1.departure_time)

        # 00:30 must not have closed shift 1 — it opens its own new record instead,
        # matched to whichever shift is nearest (the overnight one, still open from
        # the day before by construction of this test's data, or shift 1 by proximity).
        self.assertTrue(
            AttendanceRecord.objects.filter(employee=self.emp, date=self.day2).exists())
