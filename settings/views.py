from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from accounts.permissions import require_permission
from django.contrib import messages
from django.conf import settings
from django.http import FileResponse, HttpResponse
import os
import base64
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

    return render(request, 'settings/edit.html', {
        'form': form, 
        'setting': setting_obj, 
        'title': 'إعدادات النظام',
        'available_printers': [p[0] for p in available_printers] # Pass just names for template loop if needed, or use form field
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
        new_values = {}
        for key, meta in POLICY_REGISTRY.items():
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


@login_required
@require_permission('settings', 'view')
def download_database(request):
    if not _require_master(request):
        messages.error(request, "تنزيل قاعدة البيانات متاح لحساب المالك (Master) فقط.")
        return redirect('settings_view')
    db_path = settings.DATABASES['default']['NAME']
    if os.path.exists(db_path):
        return FileResponse(open(db_path, 'rb'), as_attachment=True, filename='backup.db')
    messages.error(request, "لم يتم العثور على ملف قاعدة البيانات.")
    return redirect('settings_view')

@login_required
@require_permission('settings', 'view')
def import_database(request):
    if not _require_master(request):
        messages.error(request, "استيراد قاعدة البيانات متاح لحساب المالك (Master) فقط.")
        return redirect('settings_view')
    if request.method == 'POST' and request.FILES.get('db_file'):
        db_file = request.FILES['db_file']
        if not db_file.name.endswith('.db') and not db_file.name.endswith('.sqlite3'):
            messages.error(request, "صيغة الملف غير صحيحة. يجب أن يكون .db أو .sqlite3")
            return redirect('settings_view')
            
        db_path = settings.DATABASES['default']['NAME']
        
        try:
            with open(db_path, 'wb+') as destination:
                for chunk in db_file.chunks():
                    destination.write(chunk)
            messages.success(request, "تم استيراد قاعدة البيانات بنجاح!")
        except Exception as e:
            messages.error(request, f"حدث خطأ أثناء استيراد قاعدة البيانات: {e}")
            
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