"""Automatic/background sync entry point — the same sync_device()/
process_punches_into_attendance() used by the manual "Sync Now" button, just callable
from outside a request. Run this from Windows Task Scheduler, cron, or a Celery beat
schedule (whichever is available in the deployment) on whatever interval makes sense —
nothing about the sync/dedup logic differs from a manual run, so it's exactly as safe to
run this every 5 minutes as it is to click "Sync Now" repeatedly.

Live-protocol devices (network SDK/API adapters) can be synced this way out of the box.
A csv_import device has no file to read here (there's no "background" file upload) — it
stays manual-only via the device_sync_now view, as intended for that adapter type.

Usage:
    python manage.py sync_attendance_devices                 # all enabled devices
    python manage.py sync_attendance_devices --device-id 3    # one device only
"""
from django.core.management.base import BaseCommand

from attendance_devices.models import AttendanceDevice
from attendance_devices.sync import process_punches_into_attendance, sync_device


class Command(BaseCommand):
    help = "Sync attendance punches from all enabled, non-file-based attendance devices."

    def add_arguments(self, parser):
        parser.add_argument('--device-id', type=int, default=None,
                            help="Sync only this device (by AttendanceDevice.id).")

    def handle(self, *args, **options):
        devices = AttendanceDevice.objects.filter(enabled=True).exclude(adapter_type='csv_import')
        if options['device_id']:
            devices = devices.filter(pk=options['device_id'])

        if not devices.exists():
            self.stdout.write(self.style.WARNING("لا توجد أجهزة مفعّلة تدعم المزامنة التلقائية."))
            return

        for device in devices:
            log = sync_device(device)
            if log.status == 'failed':
                self.stdout.write(self.style.ERROR(f"{device.name}: فشلت المزامنة — {log.error_message}"))
            else:
                self.stdout.write(self.style.SUCCESS(
                    f"{device.name}: {log.records_created} سجل جديد "
                    f"({log.records_skipped_duplicate} مكرر، {log.records_unmapped} غير مربوط)"))

        result = process_punches_into_attendance()
        self.stdout.write(self.style.SUCCESS(
            f"تم تحديث {result['attendance_records_updated']} سجل حضور."))
