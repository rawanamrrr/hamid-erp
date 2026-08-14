from django.contrib import admin

from .models import (AttendanceDevice, AttendanceDeviceCredential,
                     DeviceEmployeeMapping, DevicePunch, DeviceSyncLog)


@admin.register(AttendanceDevice)
class AttendanceDeviceAdmin(admin.ModelAdmin):
    list_display = ('name', 'adapter_type', 'manufacturer', 'model', 'enabled',
                    'connection_status', 'last_sync_at')
    list_filter = ('adapter_type', 'enabled', 'connection_status')
    search_fields = ('name', 'manufacturer', 'model', 'ip_address', 'device_serial')


@admin.register(DeviceEmployeeMapping)
class DeviceEmployeeMappingAdmin(admin.ModelAdmin):
    list_display = ('device', 'device_user_id', 'device_user_name', 'employee')
    list_filter = ('device',)
    search_fields = ('device_user_id', 'device_user_name')


@admin.register(DevicePunch)
class DevicePunchAdmin(admin.ModelAdmin):
    list_display = ('device', 'device_user_id', 'employee', 'punch_timestamp',
                    'punch_type', 'verification_method', 'processed')
    list_filter = ('device', 'punch_type', 'processed')
    date_hierarchy = 'punch_timestamp'
    search_fields = ('device_user_id',)


@admin.register(DeviceSyncLog)
class DeviceSyncLogAdmin(admin.ModelAdmin):
    list_display = ('device', 'started_at', 'status', 'records_fetched', 'records_created',
                    'records_skipped_duplicate', 'records_unmapped')
    list_filter = ('device', 'status')


admin.site.register(AttendanceDeviceCredential)
