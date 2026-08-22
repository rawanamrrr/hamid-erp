"""ZKTeco standalone-SDK protocol adapter — TCP/UDP port 4370, the "pull" direction
(server connects to the device and issues commands), as opposed to ADMS/PUSH (the device
calls a server instead) which this adapter does NOT implement — a device only running in
push mode needs a different, HTTP-server-side adapter, not this one.

Confirmed applicable to devices in the same hardware family as this repo's actual unit
(Convoy-branded, CGCA serial prefix, keypad + 2.4" screen + optical sensor, System Info /
Comm menu layout) — Convoy is a reseller of ZKTeco-platform hardware, not a distinct
protocol vendor; port 4370 open + this menu structure is what confirmed it, not a guess.

Uses `pyzk` (the actively-maintained, fully reverse-engineered pure-Python
implementation every open-source ZKTeco integration is built on — pyzk/zklib/go-zkteco/
the PHP libraries all implement the same wire protocol) rather than re-implementing the
undocumented binary protocol by hand.
"""
from __future__ import annotations

from datetime import datetime

from .base import (AttendanceDeviceAdapter, AttendanceDeviceError, DeviceInfo,
                    DeviceUser, StandardPunchRecord)


class ZKTecoTcpAdapter(AttendanceDeviceAdapter):
    """`self.credential` (AttendanceDeviceCredential.secret, decrypted just-in-time by
    the sync engine — see sync.build_adapter) is the device's Comm Key (a numeric PIN
    set in Comm → Ethernet on the device, 0 if the device was never given one — most
    units ship with 0/no key). `self.device.protocol` selects UDP instead of TCP only
    when explicitly set to 'UDP' (case-insensitive); TCP (port 4370) is the default and
    what this repo's own unit was confirmed to answer on.
    """

    def __init__(self, device, credential: str = ''):
        super().__init__(device, credential)
        self._conn = None  # the connected zk.ZK "device" handle; None when not connected

    def _password(self) -> int:
        try:
            return int(self.credential) if self.credential else 0
        except ValueError:
            return 0

    def _wrap(self, exc: Exception) -> AttendanceDeviceError:
        return AttendanceDeviceError(f"جهاز {self.device.name}: {exc}")

    def connect(self) -> None:
        if self._conn is not None:
            return
        try:
            from zk import ZK
            from zk.exception import ZKError
        except ImportError:
            raise AttendanceDeviceError("مكتبة pyzk غير مثبتة على السيرفر.")
        if not self.device.ip_address:
            raise AttendanceDeviceError("لم يتم إدخال عنوان IP للجهاز.")
        try:
            zk = ZK(
                self.device.ip_address,
                port=self.device.port or 4370,
                timeout=self.timeout,
                password=self._password(),
                force_udp=(self.device.protocol or '').strip().upper() == 'UDP',
            )
            self._conn = zk.connect()
        except ZKError as e:
            raise self._wrap(e)
        except Exception as e:  # noqa: BLE001 — socket/OS errors aren't ZKError subclasses
            raise self._wrap(e)

    def disconnect(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.disconnect()
        except Exception:
            pass
        finally:
            self._conn = None

    def get_device_info(self) -> DeviceInfo:
        if self._conn is None:
            raise AttendanceDeviceError("الجهاز غير متصل.")
        try:
            users = self._conn.get_users() or []
            return DeviceInfo(
                serial_number=self._conn.get_serialnumber() or '',
                firmware_version=self._conn.get_firmware_version() or '',
                user_count=len(users),
            )
        except Exception as e:
            raise self._wrap(e)

    def get_users(self) -> list[DeviceUser]:
        if self._conn is None:
            raise AttendanceDeviceError("الجهاز غير متصل.")
        try:
            return [
                DeviceUser(device_user_id=str(u.user_id), name=u.name or '')
                for u in (self._conn.get_users() or [])
            ]
        except Exception as e:
            raise self._wrap(e)

    def get_punches(self, since: datetime | None = None) -> list[StandardPunchRecord]:
        if self._conn is None:
            raise AttendanceDeviceError("الجهاز غير متصل.")
        try:
            records = self._conn.get_attendance() or []
        except Exception as e:
            raise self._wrap(e)

        # pyzk's get_attendance() has no server-side date filter — it always returns
        # the device's full log. Filter client-side against `since`, converted into the
        # DEVICE's own timezone (not the server's) since pyzk's timestamps are naive
        # device-local wall-clock time — same reasoning as the CSV adapter's own since
        # handling (see csv_import.py), for the same underlying reason: preserving the
        # device's original recorded moment, not assuming server/device share a zone.
        since_naive = None
        if since is not None:
            from django.utils import timezone as dj_timezone
            since_naive = (since.astimezone(self.device_tz).replace(tzinfo=None)
                          if dj_timezone.is_aware(since) else since)

        now = datetime.now()
        out = []
        for r in records:
            if since_naive is not None and r.timestamp < since_naive:
                continue
            # pyzk's `punch` byte convention (community-documented, not officially
            # published): 0=check-in, 1=check-out, 2=break-out, 3=break-in,
            # 4=overtime-in, 5=overtime-out. Only the unambiguous first two are mapped;
            # anything else is reported honestly as 'unknown' rather than guessed.
            punch_type = 'in' if r.punch == 0 else 'out' if r.punch == 1 else 'unknown'
            out.append(StandardPunchRecord(
                device_user_id=str(r.user_id),
                punch_timestamp=r.timestamp,
                device_id=self.device.device_serial or str(self.device.pk),
                device_name=self.device.name,
                punch_type=punch_type,
                # pyzk's get_attendance() doesn't parse out a verification-method code
                # (fingerprint/face/card/pin) from the raw record — reported as
                # 'unknown' rather than assumed, per the adapter contract.
                verification_method='unknown',
                raw_reference=f"{r.uid}:{r.timestamp.isoformat()}",
                sync_timestamp=now,
            ))
        return out

    def clear_records(self) -> None:
        if self._conn is None:
            raise AttendanceDeviceError("الجهاز غير متصل.")
        try:
            self._conn.clear_attendance()
        except Exception as e:
            raise self._wrap(e)
