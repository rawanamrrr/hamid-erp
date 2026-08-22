"""Vendor-agnostic sync engine — the only code (besides the ERP's own existing
financial.payroll_models logic) allowed to touch DevicePunch / DeviceEmployeeMapping /
AttendanceRecord. Adapters never see any of these models; they only return
StandardPunchRecord lists (see adapters/base.py).

Two separate, independently-safe-to-rerun steps:
  1. sync_device()                       — device → DevicePunch (idempotent on dedup_key)
  2. process_punches_into_attendance()   — DevicePunch → financial.AttendanceRecord
                                            (idempotent on DevicePunch.processed)
Kept separate so a device sync can run frequently/automatically while attendance
processing (which touches payroll-adjacent data) can be reviewed/run on its own cadence
if a manager wants that — though by default the "Sync Now" view runs both in sequence.
"""
from __future__ import annotations

import hashlib
import logging
import time as time_module

from django.db import transaction
from django.utils import timezone

from .adapters.base import AttendanceDeviceError, StandardPunchRecord
from .adapters.registry import get_adapter_class
from .models import (AttendanceDevice, AttendanceDeviceCredential, DeviceEmployeeMapping,
                     DevicePunch, DeviceSyncLog)

logger = logging.getLogger(__name__)


def _fetch_with_retries(adapter, device: AttendanceDevice):
    """Connect + authenticate + fetch punches, retrying up to device.max_retries times
    on a connection failure — centralized here (not per-adapter) so every vendor gets
    identical, consistently-configured retry behavior straight from the device's own
    settings, instead of each adapter reinventing its own retry loop differently.
    Re-raises the last AttendanceDeviceError if every attempt fails.
    """
    attempts = max(1, (device.max_retries or 0) + 1)
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            adapter.connect()
            try:
                adapter.authenticate()
                return adapter.get_punches(since=device.last_sync_at)
            finally:
                adapter.disconnect()
        except AttendanceDeviceError as exc:
            last_error = exc
            logger.warning("Attendance device %s: attempt %d/%d failed: %s",
                           device.name, attempt, attempts, exc)
            if attempt < attempts:
                time_module.sleep(device.retry_delay_seconds or 0)
    raise last_error


def _dedup_key(device_id: int, record: StandardPunchRecord) -> str:
    """Built from every piece of identifying info available — including raw_reference
    when the device provides one — so the SAME physical punch always hashes to the same
    key no matter how many times (or by which overlapping sync run) it's fetched, and a
    genuinely different punch (even same employee, same device, different second) never
    collides with one already stored.
    """
    raw = f"{device_id}|{record.device_user_id}|{record.punch_timestamp.isoformat()}|{record.raw_reference}"
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def build_adapter(device: AttendanceDevice):
    """Instantiate the registered adapter for this device, with its credential
    decrypted just-in-time (never held longer than this call needs it)."""
    adapter_cls = get_adapter_class(device.adapter_type)
    secret = ''
    try:
        secret = device.credential.secret
    except AttendanceDeviceCredential.DoesNotExist:
        pass
    return adapter_cls(device, secret)


def sync_device(device: AttendanceDevice, *, triggered_by=None, adapter=None) -> DeviceSyncLog:
    """Fetch punches from one device and land them as DevicePunch rows. Never raises for
    a device-communication failure — that's recorded on the returned DeviceSyncLog
    (status='failed') so one dead device never breaks a multi-device sync run; the
    caller decides whether to surface it as an error to the user.

    `adapter` lets a caller inject an already-prepared adapter instance (e.g. the CSV
    import view, which must call set_import_file() before this runs) instead of having
    one built fresh from the registry.
    """
    log = DeviceSyncLog.objects.create(device=device, triggered_by=triggered_by, status='running')
    adapter = adapter or build_adapter(device)

    try:
        # Retrying belongs to the ENGINE, not individual adapters (see
        # _fetch_with_retries docstring) — a caller-injected adapter (e.g. the CSV
        # import view, whose "connection" is just an in-memory uploaded file) still
        # goes through the same path; retrying a no-op connect()/a already-parsed file
        # is harmless, and a genuinely flaky live adapter now retries consistently.
        records = _fetch_with_retries(adapter, device)
    except AttendanceDeviceError as exc:
        log.status = 'failed'
        log.error_message = str(exc)
        log.finished_at = timezone.now()
        log.save(update_fields=['status', 'error_message', 'finished_at'])
        device.connection_status = 'error'
        device.last_error = str(exc)
        device.save(update_fields=['connection_status', 'last_error'])
        return log
    except Exception as exc:  # noqa: BLE001 — any unexpected adapter bug must not crash the sync run
        log.status = 'failed'
        log.error_message = f"خطأ غير متوقع: {exc}"
        log.finished_at = timezone.now()
        log.save(update_fields=['status', 'error_message', 'finished_at'])
        device.connection_status = 'error'
        device.last_error = str(exc)
        device.save(update_fields=['connection_status', 'last_error'])
        return log

    mapping_by_uid = {
        m.device_user_id: m.employee_id
        for m in DeviceEmployeeMapping.objects.filter(device=device)
    }

    created = 0
    skipped_duplicate = 0
    unmapped = 0

    with transaction.atomic():
        for record in records:
            key = _dedup_key(device.pk, record)
            employee_id = mapping_by_uid.get(record.device_user_id)
            if employee_id is None:
                unmapped += 1  # still stored below (employee=None) — never silently dropped

            # A naive punch_timestamp is device-local wall-clock time (see adapters/
            # base.py's contract) — localize it using the DEVICE's own configured
            # timezone (AttendanceDevice.timezone_name), never the ERP server's, so the
            # original moment the device recorded is preserved exactly regardless of
            # where the server happens to run or what timezone it's set to.
            ts = record.punch_timestamp
            if timezone.is_naive(ts):
                ts = timezone.make_aware(ts, device.get_timezone())

            _, was_created = DevicePunch.objects.get_or_create(
                dedup_key=key,
                defaults={
                    'device': device,
                    'device_user_id': record.device_user_id,
                    'employee_id': employee_id,
                    'punch_timestamp': ts,
                    'punch_type': record.punch_type or 'unknown',
                    'verification_method': record.verification_method or 'unknown',
                    'raw_reference': record.raw_reference or '',
                    'sync_log': log,
                },
            )
            if was_created:
                created += 1
            else:
                skipped_duplicate += 1

        if device.clear_after_sync and records:
            adapter.connect()
            try:
                adapter.clear_records()
            finally:
                adapter.disconnect()

    log.status = 'success' if unmapped == 0 else 'partial'
    log.records_fetched = len(records)
    log.records_created = created
    log.records_skipped_duplicate = skipped_duplicate
    log.records_unmapped = unmapped
    log.finished_at = timezone.now()
    log.save(update_fields=['status', 'records_fetched', 'records_created',
                            'records_skipped_duplicate', 'records_unmapped', 'finished_at'])

    device.last_sync_at = timezone.now()
    device.connection_status = 'online'
    device.last_error = ''
    device.save(update_fields=['last_sync_at', 'connection_status', 'last_error'])
    return log


def _bucket_punches_by_shift(punches):
    """Pairs ONE EMPLOYEE'S punches (which may span many calendar days — this is no
    longer restricted to a single day's punches, see process_punches_into_attendance)
    into shift arrival/departure buckets SEQUENTIALLY, walking punches in time order and
    tracking one "currently open" shift at a time — rather than independently matching
    every punch to its nearest shift by time-of-day (that approach let a shift-2 arrival
    get merged into shift-1 as its checkout, or vice versa, whenever the two were close
    together).

    Core rule: once a shift is opened by an arrival punch, the NEXT punch closes it as a
    checkout only if that punch is still before the following configured shift's start —
    a late checkout (e.g. shift 1 ends 17:00 but the punch is 17:50) still closes shift 1
    as long as shift 2 hasn't started yet (18:00). The moment a punch lands at or after
    the next shift's start, it is no longer eligible to be shift 1's checkout: shift 1 is
    left open with no departure (a genuinely missing checkout is preserved, not silently
    overwritten), and the punch instead opens a new shift bucket of its own.

    OVERNIGHT SHIFTS (configured end <= start, e.g. 22:00 → 06:00): a shift's "shift
    date" is the calendar date of its own arrival punch (requirement: shift date = start
    date), and the boundary that bounds its checkout is computed as an absolute
    datetime, not just a time-of-day, so it can correctly extend past midnight into the
    following calendar day:
      - If another configured shift starts later the SAME day, the boundary is that
        shift's start on the SAME date (identical to the non-overnight case).
      - If this open shift is the last one in the daily sequence AND it is itself
        overnight, the boundary wraps to the FOLLOWING day's first shift start — so a
        checkout at 05:55 for a 22:00-06:00 shift that opened yesterday still closes it.
      - If this open shift is the last one in the daily sequence and is NOT overnight,
        the boundary is simply midnight — a normal same-day shift's checkout must never
        bleed into the next calendar day (this preserves "forgot to check out, comes back
        tomorrow" always starting a fresh day, never silently closing yesterday).
    Arrival-matching (which configured shift a fresh punch belongs to) uses a CIRCULAR
    time-of-day distance so an arrival shortly after midnight (e.g. 00:10) still matches
    an overnight shift starting at 22:00 (only ~130 minutes away going around midnight,
    not the ~1310 minutes a naive same-day subtraction would suggest).

    Before any of the above: a debounce pass drops accidental repeated taps — see the
    `payroll.punch_debounce_minutes` policy check below. Debounce state and the shift
    pairing boundary both now live on real datetimes (not just time-of-day), so both
    rules keep working correctly across a midnight crossing.

    Returns (buckets, ignored_punches):
      - buckets: {(shift_date, shift_index): [punches in that shift, chronological —
        first = arrival, additional = closing/other punches within that shift's window]}.
      - ignored_punches: punches dropped by the debounce check — the caller still needs to
        mark these DevicePunch rows processed (they must never affect an AttendanceRecord,
        but they also must not be reconsidered on the next sync run with no memory of the
        punch that debounced them — see process_punches_into_attendance).
    """
    import datetime as _dt

    from settings.policies import get_policy

    def _to_minutes(hhmm):
        try:
            h, m = (int(x) for x in str(hhmm).split(':')[:2])
            return h * 60 + m
        except Exception:
            return 0

    try:
        shift_count = max(1, min(5, int(get_policy('payroll.shift_count') or 1)))
    except (TypeError, ValueError):
        shift_count = 1

    try:
        debounce_minutes = int(get_policy('payroll.punch_debounce_minutes') or 60)
    except (TypeError, ValueError):
        debounce_minutes = 60
    debounce_window = _dt.timedelta(minutes=debounce_minutes)

    shifts = sorted(
        ({'index': i,
          'start': _to_minutes(get_policy(f'payroll.shift_{i}_start')),
          'end': _to_minutes(get_policy(f'payroll.shift_{i}_end'))}
         for i in range(1, shift_count + 1)),
        key=lambda s: s['start'],
    )
    shift_pos = {s['index']: pos for pos, s in enumerate(shifts)}
    shift_by_index = {s['index']: s for s in shifts}

    def is_overnight(shift):
        return shift['end'] <= shift['start']

    def nearest_shift_index(minutes):
        def circular_distance(start):
            raw = abs(start - minutes)
            return min(raw, 1440 - raw)
        return min(shifts, key=lambda s: circular_distance(s['start']))['index']

    def _time_of(total_minutes):
        total_minutes %= 1440
        return _dt.time(total_minutes // 60, total_minutes % 60)

    def boundary_for_open_shift(shift_idx, shift_date):
        """Absolute (naive, local) datetime after which this open shift's checkout is
        considered genuinely missing rather than late — see the docstring above."""
        pos = shift_pos[shift_idx]
        shift = shift_by_index[shift_idx]
        if pos + 1 < len(shifts):
            return _dt.datetime.combine(shift_date, _time_of(shifts[pos + 1]['start']))
        if is_overnight(shift):
            return _dt.datetime.combine(
                shift_date + _dt.timedelta(days=1), _time_of(shifts[0]['start']))
        return _dt.datetime.combine(shift_date + _dt.timedelta(days=1), _dt.time(0, 0))

    sorted_punches = sorted(punches, key=lambda p: p.punch_timestamp)

    buckets: dict[tuple, list] = {}
    open_key = None            # (shift_date, shift_idx) currently open, or None
    open_boundary_dt = None
    # Debounce state: the (naive, local) datetime of the last ACCEPTED (non-ignored)
    # punch, and the boundary that was relevant when it was accepted. A candidate punch
    # is only ever compared against the last ACCEPTED punch — ignored punches never
    # reset the window, so 09:05 / 09:20 / 09:45 / 10:00 all compare back to 09:05, not
    # to each other, and all three get dropped rather than only the first pair.
    last_accepted_dt = None
    last_boundary_dt = None
    ignored_punches = []

    for p in sorted_punches:
        local_dt = timezone.localtime(p.punch_timestamp).replace(tzinfo=None)
        t = local_dt.time()
        minutes = t.hour * 60 + t.minute

        if last_accepted_dt is not None:
            within_debounce_window = (local_dt - last_accepted_dt) < debounce_window
            crossed_next_shift = last_boundary_dt is not None and local_dt >= last_boundary_dt
            if within_debounce_window and not crossed_next_shift:
                # Accidental repeated tap — completely ignored: not a checkout, not a new
                # arrival, no AttendanceRecord effect, doesn't move the debounce window.
                ignored_punches.append(p)
                continue

        if open_key is not None:
            if local_dt < open_boundary_dt:
                # Still before the next shift starts — this closes the open shift as its
                # checkout, however late it is (or however far past midnight, for an
                # overnight shift).
                buckets[open_key].append(p)
                last_accepted_dt = local_dt
                last_boundary_dt = open_boundary_dt
                open_key = None
                open_boundary_dt = None
                continue
            # At/after the next shift's start: the open shift's checkout is genuinely
            # missing (left as-is, only its arrival punch is in its bucket) and this punch
            # opens a new shift below instead.
            open_key = None
            open_boundary_dt = None

        shift_idx = nearest_shift_index(minutes)
        shift_date = local_dt.date()
        key = (shift_date, shift_idx)
        buckets.setdefault(key, []).append(p)
        open_key = key
        open_boundary_dt = boundary_for_open_shift(shift_idx, shift_date)
        last_accepted_dt = local_dt
        last_boundary_dt = open_boundary_dt

    return buckets, ignored_punches


def process_punches_into_attendance(*, employee_id: int | None = None) -> dict:
    """Fold unprocessed, mapped DevicePunch rows into financial.AttendanceRecord —
    grouped by EMPLOYEE ONLY (not by calendar date — see _bucket_punches_by_shift for
    why: an overnight shift's checkout can fall on the day after its arrival, and pairing
    needs to see both punches together to attribute the checkout to the correct shift
    date), then by shift: within each shift bucket, earliest punch → arrival_time, latest
    → departure_time. One AttendanceRecord row is written per (employee, shift_date,
    shift_index) group, so a day with punches spanning two configured shifts — or an
    overnight shift spanning two calendar dates — produces the correct separate rows
    rather than merging them into a single mislabeled arrival/departure pair. Accidental
    repeated taps (see payroll.punch_debounce_minutes) are dropped before pairing and
    never reach an AttendanceRecord at all. Writes into the SAME fields the manual
    attendance_daily form already writes (shift_index=1 only, from that form's side), so
    every downstream calculation (Payslip deductions, ...) runs completely unmodified —
    this function is the ENTIRE integration surface with financial/.

    Safe to re-run: only ever touches DevicePunch rows with processed=False, and marks
    them processed inside the same transaction as the AttendanceRecord write.
    """
    from financial.payroll_models import AttendanceRecord
    from financial.views import _match_shift_and_compute

    qs = DevicePunch.objects.filter(processed=False, employee_id__isnull=False)
    if employee_id:
        qs = qs.filter(employee_id=employee_id)

    groups: dict[int, list[DevicePunch]] = {}
    for punch in qs.select_related('employee'):
        groups.setdefault(punch.employee_id, []).append(punch)

    records_touched = 0
    records_skipped_locked = 0
    with transaction.atomic():
        for emp_id, punches in groups.items():
            shift_buckets, ignored_punches = _bucket_punches_by_shift(punches)
            # Debounced duplicates never touch an AttendanceRecord, but still need to be
            # marked processed — otherwise the next sync run would re-evaluate them with
            # no memory of the punch that debounced them and wrongly treat them as fresh.
            processed_ids = [p.id for p in ignored_punches]

            for (shift_date, shift_idx), shift_punches in shift_buckets.items():
                existing = AttendanceRecord.objects.filter(
                    employee_id=emp_id, date=shift_date, shift_index=shift_idx).first()
                if existing and existing.locked_by_manual_edit:
                    # A manager already hand-corrected this exact (day, shift) — never
                    # silently overwrite that (see AttendanceRecord.locked_by_manual_edit).
                    # Leave these punches unprocessed so they're preserved and would be
                    # picked up automatically if the lock is ever cleared.
                    records_skipped_locked += 1
                    continue

                shift_punches.sort(key=lambda p: p.punch_timestamp)
                arrival = timezone.localtime(shift_punches[0].punch_timestamp).time()
                departure = (timezone.localtime(shift_punches[-1].punch_timestamp).time()
                             if len(shift_punches) > 1 else None)

                record, _created = AttendanceRecord.objects.get_or_create(
                    employee_id=emp_id, date=shift_date, shift_index=shift_idx,
                    defaults={'status': AttendanceRecord.STATUS_PRESENT},
                )
                record.status = AttendanceRecord.STATUS_PRESENT
                record.arrival_time = arrival
                record.departure_time = departure
                record.late_minutes, record.early_departure_minutes, _matched = (
                    _match_shift_and_compute(arrival, departure, force_shift_index=shift_idx))
                record.note = (record.note or '')
                record.save(update_fields=['status', 'arrival_time', 'departure_time',
                                           'late_minutes', 'early_departure_minutes'])
                records_touched += 1
                processed_ids.extend(p.id for p in shift_punches)

            if processed_ids:
                DevicePunch.objects.filter(id__in=processed_ids).update(processed=True)

    return {'attendance_records_updated': records_touched, 'groups_processed': len(groups),
            'records_skipped_locked': records_skipped_locked}
