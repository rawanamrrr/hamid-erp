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


def process_punches_into_attendance(*, employee_id: int | None = None) -> dict:
    """Fold unprocessed, mapped DevicePunch rows into financial.AttendanceRecord —
    grouped by (employee, date): earliest punch of the day → arrival_time, latest →
    departure_time (the two-punches-a-day model this ERP uses). Writes into the SAME
    fields the manual attendance_daily form already writes, so every downstream
    calculation (_compute_late_minutes, Payslip deductions, ...) runs completely
    unmodified — this function is the ENTIRE integration surface with financial/.

    Safe to re-run: only ever touches DevicePunch rows with processed=False, and marks
    them processed inside the same transaction as the AttendanceRecord write.
    """
    from financial.payroll_models import AttendanceRecord
    from financial.views import _compute_late_minutes, _compute_early_departure_minutes
    from settings.policies import get_policy

    qs = DevicePunch.objects.filter(processed=False, employee_id__isnull=False)
    if employee_id:
        qs = qs.filter(employee_id=employee_id)

    groups: dict[tuple[int, object], list[DevicePunch]] = {}
    for punch in qs.select_related('employee'):
        local_ts = timezone.localtime(punch.punch_timestamp)
        key = (punch.employee_id, local_ts.date())
        groups.setdefault(key, []).append(punch)

    grace = get_policy('payroll.grace_period_minutes') or 15
    work_start = get_policy('payroll.work_start_time') or '09:00'
    work_end = get_policy('payroll.work_end_time') or '17:00'

    records_touched = 0
    records_skipped_locked = 0
    with transaction.atomic():
        for (emp_id, date), punches in groups.items():
            existing = AttendanceRecord.objects.filter(employee_id=emp_id, date=date).first()
            if existing and existing.locked_by_manual_edit:
                # A manager already hand-corrected this exact day — never silently
                # overwrite that (see AttendanceRecord.locked_by_manual_edit). Leave
                # these punches unprocessed (not marked processed=True) so they're
                # preserved and would be picked up automatically if the lock is ever
                # cleared, rather than being lost.
                records_skipped_locked += 1
                continue

            punches.sort(key=lambda p: p.punch_timestamp)
            arrival = timezone.localtime(punches[0].punch_timestamp).time()
            departure = timezone.localtime(punches[-1].punch_timestamp).time() if len(punches) > 1 else None

            record, _created = AttendanceRecord.objects.get_or_create(
                employee_id=emp_id, date=date,
                defaults={'status': AttendanceRecord.STATUS_PRESENT},
            )
            record.status = AttendanceRecord.STATUS_PRESENT
            record.arrival_time = arrival
            record.departure_time = departure
            record.late_minutes = _compute_late_minutes(arrival, grace, work_start)
            record.early_departure_minutes = (
                _compute_early_departure_minutes(departure, work_end) if departure else 0)
            record.note = (record.note or '')
            record.save(update_fields=['status', 'arrival_time', 'departure_time',
                                       'late_minutes', 'early_departure_minutes'])
            records_touched += 1

            DevicePunch.objects.filter(id__in=[p.id for p in punches]).update(processed=True)

    return {'attendance_records_updated': records_touched, 'groups_processed': len(groups),
            'records_skipped_locked': records_skipped_locked}
