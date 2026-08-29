from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from accounts.permissions import require_permission
from django.contrib import messages
from django.conf import settings
from django.db import connections
from django.http import FileResponse, HttpResponse
import logging
import os
import base64
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime

logger = logging.getLogger(__name__)
from .models import SystemSetting
from .forms import SystemSettingForm
from sales.printer_utils import get_available_printers 



@login_required
def user_guide_view(request):
    return render(request, 'settings/user_guide.html', {
        'title': 'دليل المستخدم الشامل',
    })


@login_required
@require_permission('settings', 'view')
def settings_view(request):
    # Get settings object
    setting_obj, created = SystemSetting.objects.get_or_create(pk=1)
    
    # 1. Fetch Printers
    raw_printers = get_available_printers()
    
    # Ensure standard format list of tuples: [('Name', 'Name'), ...]
    # This prevents any weird structure issues
    available_printers = []
    if raw_printers:
        for p in raw_printers:
            # If get_available_printers returns strings, convert to tuple
            if isinstance(p, str):
                available_printers.append((p, p))
            # If it returns tuples like ('Name', 'Name'), use as is
            elif isinstance(p, (list, tuple)) and len(p) >= 2:
                available_printers.append((p[0], p[1]))
            elif isinstance(p, (list, tuple)) and len(p) == 1:
                available_printers.append((p[0], p[0]))

    if request.method == 'POST':
        # Saving (VAT rate, shop info, printer, etc.) is a store-wide config change —
        # require the same master/superuser gate policies_view uses, not just "view".
        prof = getattr(request.user, 'profile', None)
        if not (request.user.is_superuser or getattr(prof, 'is_master', False)):
            messages.error(request, "تعديل الإعدادات متاح لحساب المالك (Master) فقط.")
            return redirect('settings_view')

        # 2. PASS 'printer_choices' HERE
        form = SystemSettingForm(
            request.POST,
            request.FILES,
            instance=setting_obj,
            printer_choices=available_printers
        )

        if form.is_valid():
            instance = form.save(commit=False)
            
            # Handle Logo
            uploaded_file = request.FILES.get('logo_upload')
            if uploaded_file:
                image_data = uploaded_file.read()
                base64_encoded = base64.b64encode(image_data).decode('utf-8')
                mime_type = "image/png"
                if uploaded_file.name.endswith('.jpg') or uploaded_file.name.endswith('.jpeg'):
                    mime_type = "image/jpeg"
                instance.logo_base64 = f"data:{mime_type};base64,{base64_encoded}"

            recipients = []
            for i in range(1, 6):
                value = form.cleaned_data.get(f'recipient_{i}', '')
                if value:
                    recipients.append(value.strip())
            instance.email_recipients = ','.join(recipients)
            
            instance.save()
            messages.success(request, "تم حفظ الإعدادات بنجاح!")
            return redirect('settings_view')
        else:
            # Debug: Print errors to console
            print("Form Errors:", form.errors)
    else:
        # 3. PASS 'printer_choices' HERE
        form = SystemSettingForm(instance=setting_obj, printer_choices=available_printers)

    from .network import network_access

    return render(request, 'settings/edit.html', {
        'form': form,
        'setting': setting_obj,
        'title': 'إعدادات النظام',
        'available_printers': [p[0] for p in available_printers], # Pass just names for template loop if needed, or use form field
        # The address waiters type into their phones — see settings/network.py.
        **network_access(request),
    })

@login_required
@require_permission('settings', 'view')
def policies_view(request):
    """Layer-2 policy engine UI ("ثوابت النظام"). Master-gated. GET renders grouped toggles
    from the registry; POST persists overridden values into the SystemPolicy singleton."""
    from .models import SystemPolicy
    from .policies import POLICY_REGISTRY, grouped_registry, resolved_policies

    # Master / superuser only — these constants change store-wide behavior.
    prof = getattr(request.user, 'profile', None)
    if not (request.user.is_superuser or getattr(prof, 'is_master', False)):
        messages.error(request, "هذه الصفحة متاحة لحساب المالك (Master) فقط.")
        return redirect('settings_view')

    policy_obj, _ = SystemPolicy.objects.get_or_create(pk=1)

    if request.method == 'POST':
        # Start from whatever's already stored (e.g. 'payroll.*' keys saved from the
        # separate إعدادات الحضور والخصومات page) and only overwrite the keys this page
        # actually renders — POLICY_GROUPS no longer lists 'payroll', so grouped_registry()
        # already excludes it; iterating the full POLICY_REGISTRY here instead would blank
        # every payroll setting back to its default on every save of this unrelated page.
        new_values = dict(policy_obj.values or {})
        rendered_keys = {key for _, _, items in grouped_registry() for key, _ in items}
        for key, meta in POLICY_REGISTRY.items():
            if key not in rendered_keys:
                continue
            field = key.replace('.', '__')  # dots aren't valid in form field names
            t = meta.get('type', 'bool')
            if t == 'bool':
                new_values[key] = (request.POST.get(field) == 'on')
            elif t == 'int':
                try:
                    new_values[key] = int(request.POST.get(field) or meta.get('default') or 0)
                except (TypeError, ValueError):
                    new_values[key] = meta.get('default')
            elif t == 'decimal':
                new_values[key] = str(request.POST.get(field) or meta.get('default') or '0')
            elif t == 'choice':
                val = request.POST.get(field)
                valid = {c[0] for c in meta.get('choices', [])}
                new_values[key] = val if val in valid else meta.get('default')
            elif t == 'time':
                # Anything that isn't a real HH:MM would silently disable whatever runs on
                # this schedule, so fall back to the default rather than store it.
                val = (request.POST.get(field) or '').strip()
                try:
                    hh, mm = val.split(':')[:2]
                    valid = 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
                except (ValueError, AttributeError):
                    valid = False
                new_values[key] = '%02d:%02d' % (int(hh), int(mm)) if valid else meta.get('default')
            elif t == 'path':
                new_values[key] = (request.POST.get(field) or '').strip().strip('"')
            else:
                new_values[key] = request.POST.get(field, meta.get('default'))
        policy_obj.values = new_values
        policy_obj.save()
        messages.success(request, "تم حفظ ثوابت النظام بنجاح!")
        return redirect('policies_view')

    resolved = resolved_policies()
    groups = []
    for gkey, glabel, items in grouped_registry():
        rows = []
        for key, meta in items:
            value = resolved.get(key)
            if meta.get('type') == 'decimal':
                # Render as a plain "3.33" string — {{ value }} on a raw Decimal gets
                # locale-formatted (comma decimal separator under ar locale), which the
                # admin would then type back verbatim and break Decimal() parsing on save.
                value = str(value)
            rows.append({
                'key': key,
                'field': key.replace('.', '__'),
                'meta': meta,
                'value': value,
                'is_bool': meta.get('type') == 'bool',
                'is_choice': meta.get('type') == 'choice',
                'is_price_list': meta.get('type') == 'price_list',
                # A clock picker beats typing "02:00" into a free-text box, and a folder
                # path needs far more room than the narrow default input gives it.
                'is_time': meta.get('type') == 'time',
                'is_path': meta.get('type') == 'path',
            })
        groups.append({'key': gkey, 'label': glabel, 'rows': rows})

    return render(request, 'settings/policies.html', {
        'title': 'ثوابت النظام',
        'groups': groups,
    })


def _require_master(request):
    """Shared master/superuser gate for whole-database backup/restore — these operations
    expose or overwrite every customer/financial record in the system, so 'settings:view'
    (meant for reading store config) must never be enough on its own."""
    prof = getattr(request.user, 'profile', None)
    return request.user.is_superuser or getattr(prof, 'is_master', False)


SQLITE_MAGIC = b'SQLite format 3\x00'


def _is_postgres():
    return 'postgresql' in settings.DATABASES['default']['ENGINE']


def _dump_postgres(target_path):
    """Write a pg_dump custom-format archive of the current database to target_path."""
    from textile_pos.pg_tools import find_pg_tool, pg_conn_args, pg_env
    db = settings.DATABASES['default']
    cmd = [find_pg_tool('pg_dump'), '-Fc', *pg_conn_args(db), '-d', db['NAME'], '-f', str(target_path)]
    result = subprocess.run(cmd, env=pg_env(db), capture_output=True, text=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode != 0:
        raise RuntimeError((result.stderr or 'pg_dump failed').strip()[:500])


def _dump_sqlite(target_path):
    """Copy the SQLite database using its Online Backup API.

    A plain file copy of a database the app is actively writing to can capture a torn
    page and produce a backup that will not open — the backup API takes a consistent
    snapshot instead.
    """
    src = sqlite3.connect(settings.DATABASES['default']['NAME'])
    try:
        dest = sqlite3.connect(str(target_path))
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()


@login_required
@require_permission('settings', 'view')
def download_database(request):
    """Download a restorable backup of the whole database.

    This used to hand back settings.DATABASES['default']['NAME'] as a file path, which is
    only ever a real path on SQLite. On PostgreSQL that value is a database NAME
    ('wholesale_pos'), so os.path.exists() was always False and the button reported "the
    database file was not found" on every PostgreSQL install.
    """
    if not _require_master(request):
        messages.error(request, "تنزيل قاعدة البيانات متاح لحساب المالك (Master) فقط.")
        return redirect('settings_view')

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    postgres = _is_postgres()
    suffix = '.dump' if postgres else '.db'
    work_dir = tempfile.mkdtemp(prefix='digiflow-backup-')
    target = os.path.join(work_dir, f'backup_{stamp}{suffix}')

    try:
        if postgres:
            _dump_postgres(target)
        else:
            _dump_sqlite(target)
    except Exception as exc:
        shutil.rmtree(work_dir, ignore_errors=True)
        logger.exception('Database backup failed')
        messages.error(request, f"تعذر إنشاء النسخة الاحتياطية: {exc}")
        return redirect('settings_view')

    # FileResponse streams and closes the handle; the temp folder goes with it.
    handle = open(target, 'rb')
    response = FileResponse(handle, as_attachment=True, filename=os.path.basename(target))
    response._resource_closers.append(lambda: shutil.rmtree(work_dir, ignore_errors=True))
    return response


def _restore_postgres(upload_path):
    from textile_pos.pg_tools import find_pg_tool, pg_conn_args, pg_env
    db = settings.DATABASES['default']
    connections.close_all()   # drop our own pooled connections so objects aren't held
    cmd = [
        find_pg_tool('pg_restore'),
        '--clean', '--if-exists', '--no-owner', '--no-privileges',
        *pg_conn_args(db), '-d', db['NAME'], str(upload_path),
    ]
    result = subprocess.run(cmd, env=pg_env(db), capture_output=True, text=True,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    # pg_restore exits non-zero for harmless "does not exist, skipping" notices under
    # --clean on a partially-populated database, so only a hard failure counts.
    if result.returncode != 0 and 'errors ignored on restore' not in (result.stderr or ''):
        stderr = (result.stderr or '').strip()
        if 'ERROR' in stderr:
            raise RuntimeError(stderr[-500:])


def _restore_sqlite(upload_path):
    db_path = settings.DATABASES['default']['NAME']
    # Keep the database we are about to overwrite. Restoring the wrong file is the kind
    # of mistake that ends a business, and it is one click away from here.
    safety = f'{db_path}.replaced-{datetime.now().strftime("%Y%m%d_%H%M%S")}'
    connections.close_all()
    if os.path.exists(db_path):
        shutil.copy2(db_path, safety)
    shutil.copy2(upload_path, db_path)


@login_required
@require_permission('settings', 'view')
def import_database(request):
    """Restore the database from a backup produced by the download button above.

    The old version wrote the uploaded bytes straight over
    settings.DATABASES['default']['NAME'] with no checks at all. On PostgreSQL that name
    is not a path, so it tried to create a file called 'wholesale_pos' inside the program
    folder and died with "Permission denied". On SQLite it "worked" — including when the
    uploaded file was not a database at all, silently destroying the live one.
    """
    if not _require_master(request):
        messages.error(request, "استيراد قاعدة البيانات متاح لحساب المالك (Master) فقط.")
        return redirect('settings_view')

    if request.method != 'POST' or not request.FILES.get('db_file'):
        return redirect('settings_view')

    db_file = request.FILES['db_file']
    postgres = _is_postgres()
    allowed = ('.dump', '.backup') if postgres else ('.db', '.sqlite3')
    if not db_file.name.lower().endswith(allowed):
        messages.error(
            request,
            "صيغة الملف غير صحيحة. النظام يعمل حالياً على "
            + ("PostgreSQL، والنسخة الاحتياطية يجب أن تكون بامتداد .dump"
               if postgres else "SQLite، والنسخة الاحتياطية يجب أن تكون بامتداد .db أو .sqlite3")
        )
        return redirect('settings_view')

    work_dir = tempfile.mkdtemp(prefix='digiflow-restore-')
    upload_path = os.path.join(work_dir, 'upload.bin')
    try:
        with open(upload_path, 'wb') as destination:
            for chunk in db_file.chunks():
                destination.write(chunk)

        # Check the file really is what it claims BEFORE touching the live database.
        with open(upload_path, 'rb') as fh:
            head = fh.read(16)
        if postgres:
            if not head.startswith(b'PGDMP'):
                raise ValueError('الملف ليس نسخة احتياطية صالحة من PostgreSQL.')
        elif head != SQLITE_MAGIC:
            raise ValueError('الملف ليس قاعدة بيانات SQLite صالحة.')

        if postgres:
            _restore_postgres(upload_path)
        else:
            _restore_sqlite(upload_path)

        messages.success(
            request,
            "تم استيراد قاعدة البيانات بنجاح! أعد تشغيل البرنامج للتأكد من تحميل البيانات الجديدة."
        )
    except Exception as exc:
        logger.exception('Database restore failed')
        messages.error(request, f"حدث خطأ أثناء استيراد قاعدة البيانات: {exc}")
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    return redirect('settings_view')


def favicon_view(request):
    """
    Serve the system logo as a favicon.
    Decodes the base64 logo from SystemSetting and returns it as an image.
    No login required so browsers can fetch it on login pages too.
    """
    try:
        settings_obj = SystemSetting.objects.first()
        if settings_obj and settings_obj.logo_base64:
            raw = settings_obj.logo_base64
            if ',' in raw:
                header_part, b64_part = raw.split(',', 1)
                # Detect MIME type
                if 'jpeg' in header_part or 'jpg' in header_part:
                    content_type = 'image/jpeg'
                elif 'svg' in header_part:
                    content_type = 'image/svg+xml'
                elif 'ico' in header_part:
                    content_type = 'image/x-icon'
                else:
                    content_type = 'image/png'

                image_data = base64.b64decode(b64_part)
                response = HttpResponse(image_data, content_type=content_type)
                response['Cache-Control'] = 'public, max-age=86400'  # Cache 24h
                return response
    except Exception:
        pass

    # Fallback: return an empty 1x1 transparent PNG
    # Prevents 404 errors when no logo is set
    transparent_png = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
    )
    response = HttpResponse(transparent_png, content_type='image/png')
    response['Cache-Control'] = 'public, max-age=3600'
    return response