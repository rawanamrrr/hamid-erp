"""The one contract every device adapter implements — this is the seam that keeps the
ERP device-agnostic. Nothing outside this file (and each concrete adapter) is allowed to
know how a specific vendor's protocol/SDK/export format works; the sync engine
(attendance_devices/sync.py) only ever talks to an AttendanceDeviceAdapter and only ever
receives StandardPunchRecord objects back.

Adding support for a new device brand = writing one new adapter class + registering it
in adapters/registry.py. Nothing here, in sync.py, or in the ERP's attendance/payroll
logic (financial app) needs to change.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class StandardPunchRecord:
    """The ERP's standard internal attendance-punch format. Every adapter must translate
    whatever the device natively returns into this shape — this is the only format the
    rest of the system (DevicePunch model, sync engine) understands.
    """
    device_user_id: str            # the device's own user/employee id, e.g. "1025"
    punch_timestamp: datetime
    device_id: str                 # the AttendanceDevice.device_serial or adapter-local id
    device_name: str
    punch_type: str = 'unknown'    # 'in' | 'out' | 'unknown' — not every device reports direction
    verification_method: str = 'unknown'  # 'fingerprint' | 'face' | 'card' | 'pin' | 'unknown'
    raw_reference: str = ''        # the device's own record id/checksum, if it has one
    sync_timestamp: datetime | None = None  # set by the adapter/sync engine when fetched


@dataclass(frozen=True)
class DeviceUser:
    """A user/employee as enrolled on the device — used by the mapping UI so an admin
    can pick "device user 1025 (اسم الموظف على الجهاز)" → an ERP employee, instead of
    typing raw device IDs blind.
    """
    device_user_id: str
    name: str = ''


@dataclass(frozen=True)
class DeviceInfo:
    """Whatever identifying info the device is willing to report about itself — every
    field is optional since not every protocol exposes all of this."""
    serial_number: str = ''
    firmware_version: str = ''
    user_count: int | None = None
    record_count: int | None = None
    raw: dict | None = None


class AttendanceDeviceError(Exception):
    """Raised by an adapter for any device-communication failure (unreachable,
    auth rejected, protocol error, timeout, ...). The sync engine catches this at the
    device level (one device's failure never aborts syncing other devices) and records
    it on DeviceSyncLog.error_message / AttendanceDevice.last_error.
    """


class AttendanceDeviceAdapter(ABC):
    """Base contract for a device adapter. `device` is the attendance_devices.models.
    AttendanceDevice row driving this adapter instance; `credential` is its decrypted
    secret (str, may be empty for no-auth devices) — passed in rather than the adapter
    reading AttendanceDeviceCredential itself, so adapters never need direct DB/ORM
    access and stay easily unit-testable with a plain device+secret pair.

    Connection robustness (timeout/retry) is configured PER DEVICE (AttendanceDevice.
    timeout_seconds/max_retries/retry_delay_seconds), not hardcoded per-vendor:
    - `self.timeout` is available in `connect()` for any socket/HTTP call the concrete
      adapter makes (e.g. `socket.create_connection(addr, timeout=self.timeout)`,
      `requests.get(..., timeout=self.timeout)`).
    - Retrying a failed connect/fetch is handled ONCE, centrally, by the sync engine
      (see sync.py's `_with_retries`) — adapters should NOT implement their own retry
      loop; they should simply raise AttendanceDeviceError on failure and let the engine
      decide whether/how many times to retry, so retry behavior is configured
      consistently across every vendor from one place (the device's own settings).

    Timezone: `self.device_tz` (AttendanceDevice.get_timezone()) is the timezone the
    device's own clock is set to. Every StandardPunchRecord an adapter returns must
    carry either an aware `punch_timestamp` (already localized correctly), or a naive
    one that is genuinely in `self.device_tz` wall-clock time — the sync engine
    localizes naive timestamps using exactly this timezone (see sync.py), never the ERP
    server's own timezone, so a device's original recorded time is always preserved
    correctly regardless of where the server happens to run.
    """

    def __init__(self, device, credential: str = ''):
        self.device = device
        self.credential = credential
        self.timeout = getattr(device, 'timeout_seconds', 10) or 10
        self.device_tz = device.get_timezone() if hasattr(device, 'get_timezone') else None

    @abstractmethod
    def connect(self) -> None:
        """Open whatever connection/session the protocol needs, honoring `self.timeout`
        for any network call. Raise AttendanceDeviceError on failure — including on a
        timeout, which the underlying client library will raise as its own exception
        type; catch and re-raise as AttendanceDeviceError so the sync engine never needs
        to know about vendor-specific exception classes. No-op for stateless adapters
        (e.g. file import)."""

    def test_connection(self) -> bool:
        """Default implementation: try connect() then disconnect(). Protocols with a
        cheaper "ping" should override this instead of doing a full connect."""
        try:
            self.connect()
            return True
        except AttendanceDeviceError:
            return False
        finally:
            self.disconnect()

    def authenticate(self) -> None:
        """No-op by default — override for protocols that need a separate auth step
        after connect() (e.g. an HTTP adapter logging in for a session token)."""
        return None

    @abstractmethod
    def get_device_info(self) -> DeviceInfo:
        """Return whatever identifying info the device reports. Raise
        AttendanceDeviceError if unavailable (must still return a DeviceInfo() with
        blank fields, not crash the caller, for protocols that just don't support this)."""

    @abstractmethod
    def get_users(self) -> list[DeviceUser]:
        """Return the device's enrolled users, for the mapping-assist UI. Return an
        empty list for adapters where this genuinely isn't retrievable (e.g. some CSV
        exports only carry punches, no user roster) — never fabricate data."""

    @abstractmethod
    def get_punches(self, since: datetime | None = None) -> list[StandardPunchRecord]:
        """Return punch records, translated into StandardPunchRecord. `since` — when
        given — lets the adapter request only new records if the protocol supports it
        (incremental sync); if the protocol can't filter server-side, fetch everything
        and let the sync engine's dedup_key filtering discard already-seen punches."""

    @abstractmethod
    def disconnect(self) -> None:
        """Release the connection. Must be safe to call even if connect() was never
        called or already failed."""

    def clear_records(self) -> None:
        """Delete the fetched records from the device's own memory. Only ever called by
        the sync engine when AttendanceDevice.clear_after_sync=True — irreversible, so
        adapters that can't support this safely should raise AttendanceDeviceError
        rather than silently no-op (so it's visible in the sync log, not swallowed)."""
        raise AttendanceDeviceError(
            f"{type(self).__name__} does not support clearing device records.")
