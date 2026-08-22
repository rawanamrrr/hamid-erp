from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import require_permission

from .adapters.csv_import import CsvImportAdapter
from .adapters.registry import adapter_choices
from .models import (AttendanceDevice, AttendanceDeviceCredential,
                     DeviceEmployeeMapping, DevicePunch, DeviceSyncLog)
from .sync import build_adapter, process_punches_into_attendance, sync_device

def _int_or(raw, default):
    raw = (raw or '').strip()
    return int(raw) if raw.isdigit() else default


# Same permission bar as the existing attendance/payroll pages (financial:manage) —
# device management is a payroll-adjacent admin function, not a new RBAC module, so it
# reuses the gate already in place rather than inventing a parallel permission scheme.
_PERM = ('financial', 'manage')


@login_required
@require_permission(*_PERM)
def device_list(request):
    devices = AttendanceDevice.objects.all().order_by('name')
    recent_logs = DeviceSyncLog.objects.select_related('device').order_by('-started_at')[:20]
    unmapped_count = DevicePunch.objects.filter(employee__isnull=True).values(
        'device_id', 'device_user_id').distinct().count()
    return render(request, 'attendance_devices/device_list.html', {
        'devices': devices, 'recent_logs': recent_logs, 'unmapped_count': unmapped_count,
    })


@login_required
@require_permission(*_PERM)
def device_form(request, device_id=None):
    device = get_object_or_404(AttendanceDevice, pk=device_id) if device_id else None

    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        adapter_type = (request.POST.get('adapter_type') or '').strip()
        if not name or not adapter_type:
            messages.error(request, 'اسم الجهاز ونوع الاتصال مطلوبان.')
            return redirect('attendance_devices:device_form', device_id=device_id) if device_id \
                else redirect('attendance_devices:device_add')

        if device is None:
            device = AttendanceDevice()
        device.name = name
        device.adapter_type = adapter_type
        device.manufacturer = (request.POST.get('manufacturer') or '').strip()
        device.model = (request.POST.get('model') or '').strip()
        device.ip_address = (request.POST.get('ip_address') or '').strip()
        port_raw = (request.POST.get('port') or '').strip()
        device.port = int(port_raw) if port_raw.isdigit() else None
        device.protocol = (request.POST.get('protocol') or '').strip()
        device.device_serial = (request.POST.get('device_serial') or '').strip()
        device.username = (request.POST.get('username') or '').strip()
        device.timezone_name = (request.POST.get('timezone_name') or '').strip() or settings.TIME_ZONE
        device.timeout_seconds = _int_or(request.POST.get('timeout_seconds'), 10)
        device.max_retries = _int_or(request.POST.get('max_retries'), 2)
        device.retry_delay_seconds = _int_or(request.POST.get('retry_delay_seconds'), 5)
        device.enabled = bool(request.POST.get('enabled'))
        device.clear_after_sync = bool(request.POST.get('clear_after_sync'))
        device.save()

        secret = request.POST.get('secret')
        if secret:  # blank means "leave unchanged" — never overwrite with an empty secret
            cred, _ = AttendanceDeviceCredential.objects.get_or_create(device=device)
            cred.secret = secret
            cred.save()

        messages.success(request, 'تم حفظ الجهاز بنجاح.')
        return redirect('attendance_devices:device_list')

    return render(request, 'attendance_devices/device_form.html', {
        'device': device, 'adapter_choices': adapter_choices(),
    })


@login_required
@require_permission(*_PERM)
@require_POST
def device_delete(request, device_id):
    device = get_object_or_404(AttendanceDevice, pk=device_id)
    device.delete()
    messages.success(request, 'تم حذف الجهاز.')
    return redirect('attendance_devices:device_list')


@login_required
@require_permission(*_PERM)
@require_POST
def device_test_connection(request, device_id):
    device = get_object_or_404(AttendanceDevice, pk=device_id)
    try:
        adapter = build_adapter(device)
    except ValueError as exc:
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=400)

    ok = adapter.test_connection()
    device.connection_status = 'online' if ok else 'offline'
    device.save(update_fields=['connection_status'])
    return JsonResponse({'status': 'ok' if ok else 'error',
                         'message': 'الاتصال ناجح' if ok else 'تعذر الاتصال بالجهاز'})


@login_required
@require_permission(*_PERM)
@require_POST
def device_sync_now(request, device_id):
    """Manual "Sync Now". A csv_import device requires a file upload on this same
    request (the adapter has no live connection to pull from); a live-protocol device
    just syncs directly against the network.
    """
    device = get_object_or_404(AttendanceDevice, pk=device_id)
    if not device.enabled:
        return JsonResponse({'status': 'error', 'message': 'الجهاز غير مفعّل'}, status=400)

    adapter = None
    if device.adapter_type == 'csv_import':
        upload = request.FILES.get('import_file')
        if not upload:
            return JsonResponse({'status': 'error', 'message': 'يجب رفع ملف للاستيراد'}, status=400)
        adapter = CsvImportAdapter(device, '')
        adapter.set_import_file(upload)

    try:
        log = sync_device(device, triggered_by=request.user, adapter=adapter)
    except ValueError as exc:  # unknown adapter_type
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=400)

    result = process_punches_into_attendance() if log.status in ('success', 'partial') else {}

    return JsonResponse({
        'status': 'ok' if log.status in ('success', 'partial') else 'error',
        'message': log.error_message or 'تمت المزامنة',
        'records_fetched': log.records_fetched,
        'records_created': log.records_created,
        'records_skipped_duplicate': log.records_skipped_duplicate,
        'records_unmapped': log.records_unmapped,
        'attendance_records_updated': result.get('attendance_records_updated', 0),
    })


@login_required
@require_permission(*_PERM)
def mapping_view(request, device_id):
    """List every device_user_id seen so far for this device (from stored punches, plus
    get_users() when the adapter can enumerate them) next to its current mapping (if
    any), with a picker to assign/reassign an ERP employee.
    """
    device = get_object_or_404(AttendanceDevice, pk=device_id)

    if request.method == 'POST':
        device_user_id = (request.POST.get('device_user_id') or '').strip()
        employee_id = request.POST.get('employee_id') or None
        device_user_name = (request.POST.get('device_user_name') or '').strip()
        if device_user_id and employee_id:
            DeviceEmployeeMapping.objects.update_or_create(
                device=device, device_user_id=device_user_id,
                defaults={'employee_id': employee_id, 'device_user_name': device_user_name},
            )
            # A user that was unmapped when first synced sits in DevicePunch with
            # employee=None forever unless backfilled here — attach it now so
            # process_punches_into_attendance() can pick these up on its next run.
            DevicePunch.objects.filter(device=device, device_user_id=device_user_id,
                                       employee__isnull=True).update(employee_id=employee_id)
            messages.success(request, 'تم حفظ الربط.')
        elif device_user_id and not employee_id:
            DeviceEmployeeMapping.objects.filter(device=device, device_user_id=device_user_id).delete()
            messages.success(request, 'تم إلغاء الربط.')
        return redirect('attendance_devices:device_mapping', device_id=device.id)

    known_uids = set(
        DevicePunch.objects.filter(device=device).values_list('device_user_id', flat=True).distinct())
    mapping_by_uid = {m.device_user_id: m for m in device.employee_mappings.select_related('employee')}
    known_uids |= set(mapping_by_uid.keys())

    # Pull the device's OWN enrolled-user list live, not just uids that happen to have
    # already generated a punch — otherwise a freshly-enrolled employee is invisible
    # here until they've clocked in at least once, which defeats the point of mapping
    # them in advance. Best-effort: a device that's offline/unreachable right now just
    # falls back to whatever's already known from punches/existing mappings instead of
    # breaking the whole page.
    live_names_by_uid = {}
    live_fetch_error = None
    from .sync import build_adapter
    from .adapters.base import AttendanceDeviceError
    try:
        adapter = build_adapter(device)
        adapter.connect()
        try:
            for u in adapter.get_users():
                known_uids.add(u.device_user_id)
                if u.name:
                    live_names_by_uid[u.device_user_id] = u.name
        finally:
            adapter.disconnect()
    except AttendanceDeviceError as e:
        live_fetch_error = str(e)
    except Exception as e:  # noqa: BLE001 — an adapter bug here must not break this page
        live_fetch_error = str(e)

    rows = []
    for uid in sorted(known_uids):
        mapping = mapping_by_uid.get(uid)
        rows.append({
            'device_user_id': uid,
            'employee': mapping.employee if mapping else None,
            'device_user_name': (mapping.device_user_name if mapping and mapping.device_user_name
                                 else live_names_by_uid.get(uid, '')),
        })

    employees = User.objects.filter(is_active=True).order_by('first_name', 'username')
    return render(request, 'attendance_devices/mapping.html', {
        'device': device, 'rows': rows, 'employees': employees,
        'live_fetch_error': live_fetch_error,
    })
