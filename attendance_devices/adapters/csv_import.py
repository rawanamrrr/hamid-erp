"""Reference adapter: devices that only offer a CSV/Excel attendance export (no live
network/SDK access) — the "import adapter" case called out explicitly in the
requirements. Also doubles as the framework's own working example/reference
implementation of the AttendanceDeviceAdapter contract, exercisable without any physical
hardware.

Expected CSV columns (case-insensitive, order-independent):
    device_user_id (required) — also accepts: user_id, id, employee_id
    timestamp (required)      — also accepts: datetime, punch_time, time — parsed with
                                 datetime.fromisoformat after normalizing a trailing 'Z'
                                 and a space/'T' separator
    punch_type (optional)     — 'in'/'out' (any case); anything else → 'unknown'
    verification_method (optional) — fingerprint/face/card/pin; anything else → 'unknown'
    user_name (optional)      — also accepts: name, device_user_name
    reference (optional)      — also accepts: record_id, ref — falls back to the row's
                                 own (user_id, timestamp) pair if absent
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

from django.utils import timezone as dj_timezone

from .base import (AttendanceDeviceAdapter, AttendanceDeviceError, DeviceInfo,
                    DeviceUser, StandardPunchRecord)

_COLUMN_ALIASES = {
    'device_user_id': {'device_user_id', 'user_id', 'id', 'employee_id'},
    'timestamp': {'timestamp', 'datetime', 'punch_time', 'time'},
    'punch_type': {'punch_type', 'type', 'direction'},
    'verification_method': {'verification_method', 'verification', 'method'},
    'user_name': {'user_name', 'name', 'device_user_name'},
    'reference': {'reference', 'record_id', 'ref'},
}


def _resolve_columns(fieldnames: list[str]) -> dict[str, str]:
    lower_map = {(fn or '').strip().lower(): fn for fn in fieldnames}
    resolved = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lower_map:
                resolved[canonical] = lower_map[alias]
                break
    return resolved


def _parse_timestamp(raw: str) -> datetime:
    raw = (raw or '').strip().replace('Z', '').replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%d/%m/%Y %H:%M:%S', '%d/%m/%Y %H:%M'):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise AttendanceDeviceError(f"تعذّر قراءة التاريخ/الوقت: '{raw}'")


class CsvImportAdapter(AttendanceDeviceAdapter):
    """No live connection — `connect`/`disconnect`/`test_connection` are no-ops that
    always succeed; the actual data comes from a file handed to `set_import_file()`
    by the caller (the sync view) before `get_punches()` is called.
    """

    def __init__(self, device, credential: str = ''):
        super().__init__(device, credential)
        self._file_content: str | None = None

    def set_import_file(self, file_obj) -> None:
        raw = file_obj.read()
        self._file_content = raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw

    def connect(self) -> None:
        return None

    def test_connection(self) -> bool:
        return True

    def disconnect(self) -> None:
        return None

    def get_device_info(self) -> DeviceInfo:
        return DeviceInfo(raw={'note': 'CSV import adapter — no live device info available'})

    def get_users(self) -> list[DeviceUser]:
        if not self._file_content:
            return []
        reader = csv.DictReader(io.StringIO(self._file_content))
        cols = _resolve_columns(reader.fieldnames or [])
        if 'device_user_id' not in cols:
            return []
        seen: dict[str, DeviceUser] = {}
        for row in reader:
            uid = (row.get(cols['device_user_id']) or '').strip()
            if not uid or uid in seen:
                continue
            name = (row.get(cols.get('user_name', '')) or '').strip() if cols.get('user_name') else ''
            seen[uid] = DeviceUser(device_user_id=uid, name=name)
        return list(seen.values())

    def get_punches(self, since: datetime | None = None) -> list[StandardPunchRecord]:
        if not self._file_content:
            raise AttendanceDeviceError("لم يتم رفع ملف بيانات للاستيراد.")
        reader = csv.DictReader(io.StringIO(self._file_content))
        cols = _resolve_columns(reader.fieldnames or [])
        missing = [c for c in ('device_user_id', 'timestamp') if c not in cols]
        if missing:
            raise AttendanceDeviceError(
                f"الملف لا يحتوي على الأعمدة المطلوبة: {', '.join(missing)}")

        # CSV timestamps have no timezone of their own (naive — assumed to already be in
        # the DEVICE's own local wall-clock time, per the adapter contract) — `since`
        # (AttendanceDevice.last_sync_at) is Django-aware in UTC, so convert it into the
        # device's own timezone (self.device_tz, NOT the ERP server's) before comparing,
        # rather than raising on a naive/aware mismatch or assuming the server's zone.
        since_naive = None
        if since:
            tz = self.device_tz or dj_timezone.get_current_timezone()
            since_naive = since.astimezone(tz).replace(tzinfo=None) if dj_timezone.is_aware(since) else since

        now = datetime.now()
        records = []
        for row in reader:
            uid = (row.get(cols['device_user_id']) or '').strip()
            if not uid:
                continue
            ts = _parse_timestamp(row.get(cols['timestamp']))
            if since_naive and ts < since_naive:
                continue
            punch_type_raw = (row.get(cols.get('punch_type', '')) or '').strip().lower() if cols.get('punch_type') else ''
            punch_type = punch_type_raw if punch_type_raw in ('in', 'out') else 'unknown'
            verif_raw = (row.get(cols.get('verification_method', '')) or '').strip().lower() if cols.get('verification_method') else ''
            verification = verif_raw if verif_raw in ('fingerprint', 'face', 'card', 'pin') else 'unknown'
            reference = (row.get(cols.get('reference', '')) or '').strip() if cols.get('reference') else ''

            records.append(StandardPunchRecord(
                device_user_id=uid,
                punch_timestamp=ts,
                device_id=self.device.device_serial or str(self.device.pk),
                device_name=self.device.name,
                punch_type=punch_type,
                verification_method=verification,
                raw_reference=reference,
                sync_timestamp=now,
            ))
        return records
