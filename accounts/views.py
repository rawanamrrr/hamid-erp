from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.core.exceptions import PermissionDenied
import json
from django.contrib.auth.views import LoginView
from django.contrib.auth import logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib import messages
from django.db.models import Count, Q, ProtectedError
from django.utils import timezone
import subprocess
from .forms import (
    LoginForm, CreateUserForm, EditUserForm,
    ProfileUserForm, ProfileDetailsForm, ProfilePasswordChangeForm
)
from .models import Role, UserProfile, UserActivityLog, UserIPHistory, SystemError
from .permissions import require_permission, has_permission, get_best_landing_url

# ----------------- INICIO AUTH -----------------
class CustomLoginView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        # "Remember me": persist the session 30 days; otherwise expire on browser close.
        if self.request.POST.get('remember_me'):
            self.request.session.set_expiry(60 * 60 * 24 * 30)
        else:
            self.request.session.set_expiry(0)
        return super().form_valid(form)

    def get_success_url(self):
        # We always check for the next parameter first
        next_url = self.request.GET.get('next')
        if next_url:
            return next_url

        return get_best_landing_url(self.request.user)

@login_required
def no_access(request):
    """Shown instead of a hard 403 when a user has no permissions granted at all."""
    return render(request, 'registration/no_access.html', {'title': 'لا توجد صلاحيات'})

def logout_view(request):
    logout(request)
    return redirect('/accounts/login/')

def home_redirect(request):
    if request.user.is_authenticated:
        return redirect(get_best_landing_url(request.user))
    return redirect('login')


def forgot_password_request(request):
    """Step 1: the user enters their email; we email a reset code to the linked account."""
    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip().lower()
        user = (User.objects.filter(email__iexact=email, is_active=True).first()
                if email else None)
        if user:
            from .models import PasswordResetCode
            from .mailer import send_reset_email
            rc = PasswordResetCode.issue(user)
            send_reset_email(user.email, rc.code)
            request.session['reset_user_id'] = user.id
            request.session['reset_email'] = email
            messages.success(request, 'تم إرسال رمز التحقق إلى بريدك الإلكتروني.')
            return redirect('forgot_password_verify')
        messages.error(request, 'لا يوجد حساب نشط مرتبط بهذا البريد الإلكتروني.')
    return render(request, 'registration/forgot_password.html')


def forgot_password_verify(request):
    """Step 2: the user enters the code + a new password."""
    uid = request.session.get('reset_user_id')
    user = User.objects.filter(id=uid).first() if uid else None
    if not user:
        return redirect('forgot_password')
    if request.method == 'POST':
        from .models import PasswordResetCode
        code = (request.POST.get('code') or '').strip()
        new_pwd = request.POST.get('new_password') or ''
        confirm = request.POST.get('confirm_password') or ''
        rc = PasswordResetCode.objects.filter(user=user, used=False).first()
        if not rc or not rc.is_valid():
            messages.error(request, 'انتهت صلاحية الرمز أو تجاوزت عدد المحاولات. اطلب رمزاً جديداً.')
            return redirect('forgot_password')
        rc.attempts += 1
        rc.save(update_fields=['attempts'])
        if code != rc.code:
            messages.error(request, 'الرمز غير صحيح.')
        elif len(new_pwd) < 6:
            messages.error(request, 'كلمة المرور يجب أن تكون 6 أحرف على الأقل.')
        elif new_pwd != confirm:
            messages.error(request, 'كلمتا المرور غير متطابقتين.')
        else:
            user.set_password(new_pwd)
            user.save()
            rc.used = True
            rc.save(update_fields=['used'])
            for k in ('reset_user_id', 'reset_email'):
                request.session.pop(k, None)
            messages.success(request, 'تم تغيير كلمة المرور بنجاح. سجّل الدخول الآن.')
            return redirect('login')
    return render(request, 'registration/forgot_password_verify.html',
                  {'email': request.session.get('reset_email', '')})


@login_required
def my_profile(request):
    user = request.user
    profile, _ = UserProfile.objects.get_or_create(user=user)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_profile':
            user_form = ProfileUserForm(request.POST, instance=user)
            profile_form = ProfileDetailsForm(request.POST, request.FILES, instance=profile)
            password_form = ProfilePasswordChangeForm(user=user)

            if user_form.is_valid() and profile_form.is_valid():
                user_form.save()
                profile_form.save()
                UserActivityLog.objects.create(
                    user=user,
                    action_type='UPDATE',
                    module='profile',
                    description='تعديل بياناته الشخصية'
                )
                messages.success(request, 'تم تحديث الملف الشخصي بنجاح.')
                return redirect('my_profile')
            messages.error(request, 'يرجى تصحيح أخطاء نموذج البيانات الشخصية.')

        elif action == 'change_password':
            user_form = ProfileUserForm(instance=user)
            profile_form = ProfileDetailsForm(instance=profile)
            password_form = ProfilePasswordChangeForm(user=user, data=request.POST)

            if password_form.is_valid():
                changed_user = password_form.save()
                update_session_auth_hash(request, changed_user)
                UserActivityLog.objects.create(
                    user=user,
                    action_type='UPDATE',
                    module='profile',
                    description='قام بتغيير كلمة المرور'
                )
                messages.success(request, 'تم تغيير كلمة المرور بنجاح.')
                return redirect('my_profile')
            messages.error(request, 'تعذر تغيير كلمة المرور. تأكد من البيانات المدخلة.')
        else:
            user_form = ProfileUserForm(instance=user)
            profile_form = ProfileDetailsForm(instance=profile)
            password_form = ProfilePasswordChangeForm(user=user)
    else:
        user_form = ProfileUserForm(instance=user)
        profile_form = ProfileDetailsForm(instance=profile)
        password_form = ProfilePasswordChangeForm(user=user)

    return render(request, 'accounts/my_profile.html', {
        'title': 'الملف الشخصي',
        'user_form': user_form,
        'profile_form': profile_form,
        'password_form': password_form,
        'recent_logs': UserActivityLog.objects.filter(user=user).order_by('-timestamp')[:20],
    })

# ----------------- USER MANAGEMENT -----------------
@login_required
@require_permission('users', 'view')
def user_list(request):
    users = User.objects.select_related('profile').prefetch_related('profile__roles').annotate(
        logs_count=Count('activity_logs')
    ).all().order_by('-date_joined')
    
    # Filter by Role
    role_filter = request.GET.get('role')
    if role_filter:
        users = users.filter(profile__roles__id=role_filter)
        
    roles = Role.objects.all()
    return render(request, 'accounts/user_list.html', {'users': users, 'roles': roles, 'title': 'إدارة المستخدمين'})

@login_required
@require_permission('users', 'view')
def user_detail(request, pk):
    user_obj = get_object_or_404(User.objects.select_related('profile'), pk=pk)
    
    # Check permissions for master accounts
    is_current_master = hasattr(request.user, 'profile') and request.user.profile.is_master
    is_viewing_master = hasattr(user_obj, 'profile') and user_obj.profile.is_master
    
    # If viewing a master account and you are not master, deny
    if is_viewing_master and not is_current_master:
        raise PermissionDenied('لا تمتلك صلاحيات كافية لفحص هذه الحساب.')
    
    recent_logs = UserActivityLog.objects.filter(user=user_obj).order_by('-timestamp')[:50]
    ip_history = UserIPHistory.objects.filter(user=user_obj).order_by('-last_seen')[:20]
    
    return render(request, 'accounts/user_detail.html', {
        'user_obj': user_obj,
        'recent_logs': recent_logs,
        'ip_history': ip_history,
        'title': f'تفاصيل المستخدم: {user_obj.username}'
    })

@login_required
@require_permission('users', 'create')
def user_create(request):
    if request.method == 'POST':
        form = CreateUserForm(request.POST, request_user=request.user)
        if form.is_valid():
            new_user = form.save()
            # The signal will create the profile. Persist the required phone + assign roles.
            if hasattr(new_user, 'profile'):
                new_user.profile.phone = form.cleaned_data['phone']
                new_user.profile.save(update_fields=['phone'])
            role_ids = request.POST.getlist('roles')
            if hasattr(new_user, 'profile'):
                new_user.profile.roles.set(role_ids)
                # Phase 3.2: operational limits — the "حدود التشغيل (للكاشير)" section on
                # this same form used to be silently discarded here; only user_edit saved
                # them, so a brand-new user always got the model defaults regardless of
                # what was checked when creating them.
                from decimal import Decimal, InvalidOperation
                prof = new_user.profile
                try:
                    prof.max_discount_percent = Decimal(str(request.POST.get('max_discount_percent') or '100'))
                except (InvalidOperation, TypeError):
                    prof.max_discount_percent = Decimal('100')
                try:
                    prof.max_discount_amount = Decimal(str(request.POST.get('max_discount_amount') or '0'))
                except (InvalidOperation, TypeError):
                    prof.max_discount_amount = Decimal('0')
                prof.can_sell_below_cost = request.POST.get('can_sell_below_cost') == 'on'
                prof.can_edit_price = request.POST.get('can_edit_price') == 'on'
                prof.can_sell_below_sale_price = request.POST.get('can_sell_below_sale_price') == 'on'
                prof.can_sell_below_zero_stock = request.POST.get('can_sell_below_zero_stock') == 'on'
                # can_change_unit / can_view_profit are no longer editable from this form
                # (not useful per-user — profit visibility is governed by the store-wide
                # 'sales.show_profit_on_invoice' policy instead) — left at their model defaults.
                # allowed_order_types is set per-ROLE only (إضافة/تعديل دور) — no per-user
                # override field on this form, so it's left untouched here.
                landing = request.POST.get('default_landing')
                if landing in dict(prof.LANDING_CHOICES):
                    prof.default_landing = landing
                prof.save(update_fields=[
                    'max_discount_percent', 'max_discount_amount', 'can_sell_below_cost',
                    'can_edit_price', 'can_sell_below_sale_price', 'can_sell_below_zero_stock',
                    'default_landing',
                ])
            UserActivityLog.objects.create(
                user=request.user, action_type='CREATE', module='users',
                description=f'إنشاء مستخدم جديد: {new_user.username}'
            )
            messages.success(request, 'تم إضافة المستخدم بنجاح.')
            return redirect('user_list')
    else:
        form = CreateUserForm(request_user=request.user)

    roles = Role.objects.all()
    return render(request, 'accounts/user_form.html', {'form': form, 'roles': roles, 'title': 'إضافة مستخدم جديد'})


@login_required
@require_permission('users', 'edit')
def user_reset_password(request, pk):
    """Owner/admin resets a user's password locally — the offline, free recovery path."""
    target = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        new_pwd = request.POST.get('new_password') or ''
        if len(new_pwd) < 6:
            messages.error(request, 'كلمة المرور يجب أن تكون 6 أحرف على الأقل.')
        else:
            target.set_password(new_pwd)
            target.save()
            UserActivityLog.objects.create(
                user=request.user, action_type='UPDATE', module='users',
                description=f'إعادة تعيين كلمة مرور المستخدم: {target.username}')
            messages.success(request, f'تم تغيير كلمة مرور {target.username} بنجاح.')
    return redirect('user_edit', pk=pk)

@login_required
@require_permission('users', 'edit')
def user_edit(request, pk):
    user_obj = get_object_or_404(User.objects.select_related('profile'), pk=pk)
    
    # Check permissions for master accounts
    is_current_master = hasattr(request.user, 'profile') and request.user.profile.is_master
    is_editing_master = hasattr(user_obj, 'profile') and user_obj.profile.is_master
    
    # If editing a master account and you are not master, deny
    if is_editing_master and not is_current_master:
        raise PermissionDenied('لا تمتلك صلاحيات كافية لتعديل هذه الحساب.')
    
    if request.method == 'POST':
        form = EditUserForm(request.POST, request.FILES, instance=user_obj, request_user=request.user)
        if form.is_valid():
            form.save()
            role_ids = request.POST.getlist('roles')
            if hasattr(user_obj, 'profile'):
                user_obj.profile.roles.set(role_ids)
                # Phase 3.2: operational limits
                from decimal import Decimal, InvalidOperation
                prof = user_obj.profile
                try:
                    prof.max_discount_percent = Decimal(str(request.POST.get('max_discount_percent') or '100'))
                except (InvalidOperation, TypeError):
                    prof.max_discount_percent = Decimal('100')
                try:
                    prof.max_discount_amount = Decimal(str(request.POST.get('max_discount_amount') or '0'))
                except (InvalidOperation, TypeError):
                    prof.max_discount_amount = Decimal('0')
                prof.can_sell_below_cost = request.POST.get('can_sell_below_cost') == 'on'
                prof.can_edit_price = request.POST.get('can_edit_price') == 'on'
                # Phase 3.2 (expanded) granular flags
                prof.can_sell_below_sale_price = request.POST.get('can_sell_below_sale_price') == 'on'
                prof.can_sell_below_zero_stock = request.POST.get('can_sell_below_zero_stock') == 'on'
                # can_change_unit / can_view_profit are no longer editable from this form
                # (not useful per-user — profit visibility is governed by the store-wide
                # 'sales.show_profit_on_invoice' policy instead) — left at their model defaults.
                prof.allowed_order_types = [
                    t for t, field in (
                        (prof.ORDER_TYPE_DINE_IN, 'allowed_order_type_dine_in'),
                        (prof.ORDER_TYPE_TAKEAWAY, 'allowed_order_type_takeaway'),
                        (prof.ORDER_TYPE_DELIVERY, 'allowed_order_type_delivery'),
                    ) if request.POST.get(field) == 'on'
                ]
                landing = request.POST.get('default_landing')
                if landing in dict(prof.LANDING_CHOICES):
                    prof.default_landing = landing
                prof.save(update_fields=[
                    'max_discount_percent', 'max_discount_amount', 'can_sell_below_cost',
                    'can_edit_price', 'can_sell_below_sale_price', 'can_sell_below_zero_stock',
                    'allowed_order_types', 'default_landing',
                ])

            UserActivityLog.objects.create(
                user=request.user, action_type='UPDATE', module='users',
                description=f'تعديل بيانات المستخدم: {user_obj.username}'
            )
            messages.success(request, 'تم حفظ التعديلات.')
            return redirect('user_detail', pk=user_obj.pk)
    else:
        form = EditUserForm(instance=user_obj, request_user=request.user)
        
    roles = Role.objects.all()
    current_roles = user_obj.profile.roles.all() if hasattr(user_obj, 'profile') else []
    
    return render(request, 'accounts/user_form.html', {
        'form': form, 'roles': roles, 'current_roles': current_roles, 
        'user_obj': user_obj, 'title': f'تعديل المستخدم: {user_obj.username}'
    })

@login_required
@require_permission('users', 'delete')
def user_delete(request, pk):
    if request.method != 'POST':
        return redirect('user_list')

    user_obj = get_object_or_404(User.objects.select_related('profile'), pk=pk)

    if user_obj.pk == request.user.pk:
        messages.error(request, 'لا يمكنك حذف حسابك الخاص.')
        return redirect('user_list')

    # Check permissions for master accounts
    is_current_master = hasattr(request.user, 'profile') and request.user.profile.is_master
    is_deleting_master = hasattr(user_obj, 'profile') and user_obj.profile.is_master

    # If deleting a master account and you are not master, deny
    if is_deleting_master and not is_current_master:
        raise PermissionDenied('لا تمتلك صلاحيات كافية لحذف هذه الحساب.')

    # Don't delete master or superuser unless you are master
    if (not is_deleting_master and not user_obj.is_superuser) or is_current_master:
        username = user_obj.username
        try:
            user_obj.delete()
        except ProtectedError:
            # This user created financial records (Transaction/JournalEntry) that are
            # PROTECTED on purpose — deleting them would corrupt the accounting audit
            # trail. Deactivating instead preserves their history but blocks login.
            messages.error(
                request,
                f'لا يمكن حذف المستخدم "{username}" لأنه مرتبط بحركات مالية/محاسبية '
                f'(مثل فواتير أو قيود محاسبية) — حذفه سيفسد السجل المحاسبي. '
                f'بدلاً من ذلك، عطّل الحساب من صفحة التعديل (إلغاء تفعيل) لمنعه من الدخول '
                f'مع الاحتفاظ بسجله.'
            )
            return redirect('user_list')
        UserActivityLog.objects.create(
            user=request.user, action_type='DELETE', module='users',
            description=f'حذف مستخدم: {username}'
        )
        messages.success(request, f'تم حذف المستخدم "{username}" بنجاح.')
    else:
        messages.error(request, 'لا تمتلك صلاحيات كافية لحذف هذا الحساب.')
    return redirect('user_list')


# ----------------- ROLE MANAGEMENT -----------------
@login_required
@require_permission('users', 'edit')
def role_list(request):
    roles = Role.objects.annotate(users_count=Count('users')).all()
    return render(request, 'accounts/role_list.html', {'roles': roles, 'title': 'إدارة الأدوار والصلاحيات'})

@login_required
@require_permission('users', 'edit')
def role_create(request):
    import json
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        
        # Parse custom permissions
        raw_perms = request.POST.get('permissions_json', '{}')
        try:
            permissions = json.loads(raw_perms)
        except:
            permissions = {}
            
        allowed_order_types = [
            t for t, field in (
                ('dine_in', 'role_allowed_order_type_dine_in'),
                ('takeaway', 'role_allowed_order_type_takeaway'),
                ('delivery', 'role_allowed_order_type_delivery'),
            ) if request.POST.get(field) == 'on'
        ]
        role = Role.objects.create(name=name, description=description, permissions=permissions,
                                    allowed_order_types=allowed_order_types)

        UserActivityLog.objects.create(
            user=request.user, action_type='CREATE', module='roles',
            description=f'إنشاء دور جديد: {role.name}'
        )
        messages.success(request, 'تم إنشاء الدور بنجاح.')
        return redirect('role_list')
        
    return render(request, 'accounts/role_form.html', {'title': 'إضافة دور جديد'})

@login_required
@require_permission('users', 'edit')
def role_edit(request, pk):
    import json
    role = get_object_or_404(Role, pk=pk)
    
    if request.method == 'POST':
        role.name = request.POST.get('name')
        role.description = request.POST.get('description', '')
        
        raw_perms = request.POST.get('permissions_json', '{}')
        try:
            role.permissions = json.loads(raw_perms)
        except:
            pass

        role.allowed_order_types = [
            t for t, field in (
                ('dine_in', 'role_allowed_order_type_dine_in'),
                ('takeaway', 'role_allowed_order_type_takeaway'),
                ('delivery', 'role_allowed_order_type_delivery'),
            ) if request.POST.get(field) == 'on'
        ]

        role.save()
        UserActivityLog.objects.create(
            user=request.user, action_type='UPDATE', module='roles',
            description=f'تعديل الدور: {role.name}'
        )
        messages.success(request, 'تم حفظ الدور.')
        return redirect('role_list')
        
    perms_json_str = json.dumps(role.permissions)
    return render(request, 'accounts/role_form.html', {'role': role, 'perms_json_str': perms_json_str, 'title': 'تعديل الدور'})

@login_required
@require_permission('users', 'delete')
@require_POST
def role_delete(request, pk):
    role = get_object_or_404(Role, pk=pk)
    name = role.name
    # Deleting a Role only clears the M2M row on UserProfile.roles — any user who had it
    # keeps their account and simply loses that role's permissions (falling back to
    # whatever their other roles/direct_permissions grant), exactly like removing any
    # other role from their profile. They are NOT deleted or deactivated.
    users_count = role.users.count()
    role.delete()

    UserActivityLog.objects.create(
        user=request.user, action_type='DELETE', module='roles',
        description=f'حذف الدور: {name} (كان مرتبطاً بـ {users_count} مستخدم)'
    )
    messages.success(request, f'تم حذف الدور "{name}" بنجاح. المستخدمون المرتبطون به لم يُحذفوا، لكنهم فقدوا صلاحياته حتى تتم إضافة دور آخر لهم.')
    return redirect('role_list')


# ----------------- ACTIVITY LOGS -----------------
@login_required
@require_permission('users', 'view')
def activity_logs(request):
    logs = UserActivityLog.objects.select_related('user').order_by('-timestamp')[:500]
    return render(request, 'accounts/logs_list.html', {'logs': logs, 'title': 'سجل نشاط النظام'})

@csrf_exempt
def log_js_error(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            SystemError.objects.create(
                user=request.user if request.user and request.user.is_authenticated else None,
                path=data.get('url', request.META.get('HTTP_REFERER', 'Unknown')),
                exception_type=f"JS {data.get('type', 'Error')}",
                message=data.get('message', ''),
                traceback=data.get('stack', ''),
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT'),
                source='FRONTEND'
            )
            return JsonResponse({'status': 'ok'})
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    return JsonResponse({'status': 'ignored'}, status=405)

@login_required
def system_error_history(request):
    # Only allow Master account
    if not hasattr(request.user, 'profile') or not request.user.profile.is_master:
        raise PermissionDenied('لا تمتلك صلاحيات كافية للوصول إلى هذه الصفحة.')
        
    errors = SystemError.objects.all().order_by('-timestamp')
    
    # Search
    search = request.GET.get('q')
    if search:
        errors = errors.filter(Q(message__icontains=search) | Q(exception_type__icontains=search) | Q(path__icontains=search))
        
    return render(request, 'accounts/error_history.html', {
        'errors': errors,
        'title': 'سجل أخطاء النظام (History)'
    })

@login_required
def resolve_error(request, pk):
    # Only allow Master account
    if not hasattr(request.user, 'profile') or not request.user.profile.is_master:
        raise PermissionDenied('لا تمتلك صلاحيات كافية للوصول إلى هذه الصفحة.')
        
    error = get_object_or_404(SystemError, pk=pk)
    error.is_resolved = True
    error.save()
    messages.success(request, 'تم تحديد الخطأ كمحلول.')
    return redirect('error_history')

@login_required
def restart_gunicorn(request):
    # Only allow Master account
    if not hasattr(request.user, 'profile') or not request.user.profile.is_master:
        raise PermissionDenied('لا تمتلك صلاحيات كافية للوصول إلى هذه الصفحة.')
        
    if request.method != 'POST':
        return redirect('error_history')

    try:
        pkill_result = subprocess.run(
            ['sudo', 'pkill', 'gunicorn'],
            capture_output=True,
            text=True,
            timeout=20
        )
        start_result = subprocess.run(
            ['sudo', 'systemctl', 'start', 'gunicorn'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if start_result.returncode == 0 and pkill_result.returncode in (0, 1):
            messages.success(request, 'تم تنفيذ إعادة تشغيل Gunicorn بنجاح.')
        else:
            error_text = (start_result.stderr or pkill_result.stderr or 'تعذر تنفيذ الأمر على السيرفر').strip()
            messages.error(request, f'فشل إعادة تشغيل Gunicorn: {error_text}')
    except Exception as exc:
        messages.error(request, f'حدث خطأ أثناء تشغيل الأوامر: {str(exc)}')

    return redirect('error_history')

# ----------------- SIDEBAR PERMISSIONS -----------------
@login_required
@require_permission('users', 'edit')
def user_sidebar_permissions(request, pk):
    user_obj = get_object_or_404(User.objects.select_related('profile'), pk=pk)
    profile = user_obj.profile
    
    # Check permissions for master accounts
    is_current_master = hasattr(request.user, 'profile') and request.user.profile.is_master
    is_editing_master = hasattr(user_obj, 'profile') and user_obj.profile.is_master
    
    # If editing a master account and you are not master, deny
    if is_editing_master and not is_current_master:
        raise PermissionDenied('لا تمتلك صلاحيات كافية لتعديل هذه الحساب.')
    
    modules = [
        {
            'id': 'dashboard', 'name': 'لوحة التحكم', 'icon': 'fas fa-th-large',
            'sub_links': [
                {'id': 'dashboard:items_sold', 'name': 'القطع المباعة', 'icon': 'fas fa-boxes'},
                {'id': 'dashboard:revenue', 'name': 'الإيرادات', 'icon': 'fas fa-sack-dollar'},
                {'id': 'dashboard:stock_value', 'name': 'قيمة المخزون', 'icon': 'fas fa-warehouse'},
                {'id': 'dashboard:cash_summary', 'name': 'ملخص الخزنة', 'icon': 'fas fa-vault'},
            ]
        },
        {
            'id': 'pos', 'name': 'نظام البيع (الكاشير)', 'icon': 'fas fa-calculator',
            'sub_links': [
                {'id': 'pos:desktop', 'name': 'كاشير الكمبيوتر', 'icon': 'fas fa-desktop'},
                {'id': 'pos:mobile', 'name': 'موبايل كاشير', 'icon': 'fas fa-mobile-alt'},
                {'id': 'pos:transfer', 'name': 'تحويل مخزون (من الكاشير)', 'icon': 'fas fa-exchange-alt'},
                {'id': 'pos:cashier_inbound', 'name': 'الطلبات الواردة (الكاشير)', 'icon': 'fas fa-cash-register'},
                {'id': 'pos:custody', 'name': 'الجرد والودائع (تقارير الويتر/الطيارين)', 'icon': 'fas fa-mug-saucer'},
            ]
        },
        # Waiter/kitchen/delivery are now their own permission modules (restaurant/views.py
        # checks 'waiter'/'kitchen'/'delivery' directly) instead of being lumped under 'pos'
        # — each needs its own sidebar toggle to match, same as it needs its own role
        # checkbox in role_form.html.
        {
            'id': 'waiter', 'name': 'شاشة الويتر', 'icon': 'fas fa-chair',
            'sub_links': []
        },
        {
            'id': 'kitchen', 'name': 'شاشة المطبخ', 'icon': 'fas fa-kitchen-set',
            'sub_links': []
        },
        {
            'id': 'delivery', 'name': 'شاشة الدليفري', 'icon': 'fas fa-motorcycle',
            'sub_links': []
        },
        {
            'id': 'financial', 'name': 'الخزنة والشيفتات', 'icon': 'fas fa-vault',
            'sub_links': [
                {'id': 'financial:open_shift', 'name': 'فتح وردية', 'icon': 'fas fa-door-open'},
                {'id': 'financial:close_shift', 'name': 'إغلاق وردية', 'icon': 'fas fa-lock'},
                {'id': 'financial:shift_history', 'name': 'سجل الورديات', 'icon': 'fas fa-clock-rotate-left'},
                {'id': 'financial:shift_report', 'name': 'تقرير الوردية (X)', 'icon': 'fas fa-receipt'},
                {'id': 'financial:withdraw', 'name': 'سحب نقدي', 'icon': 'fas fa-money-bill-wave'},
                {'id': 'financial:manage', 'name': 'إضافة عملية مالية (إيداع/تحويل/مصروف)', 'icon': 'fas fa-money-bill-transfer'},
                {'id': 'financial:accounts', 'name': 'دليل الحسابات', 'icon': 'fas fa-book'},
                {'id': 'financial:payroll', 'name': 'رواتب الموظفين والسلف', 'icon': 'fas fa-money-check-dollar'},
                {'id': 'financial:deals', 'name': 'العروض والخصومات', 'icon': 'fas fa-tags'},
                {'id': 'financial:reports', 'name': 'التقارير المالية (ملخص/ربحية/تحليلات/قوائم)', 'icon': 'fas fa-chart-pie'},
            ]
        },
        {
            'id': 'sales', 'name': 'المبيعات والتشغيل', 'icon': 'fas fa-chart-line',
            'sub_links': [
                {'id': 'sales:orders', 'name': 'سجل الفواتير', 'icon': 'fas fa-file-invoice-dollar'},
                {'id': 'sales:voided', 'name': 'الفواتير الملغاة', 'icon': 'fas fa-ban'},
                {'id': 'sales:quotations', 'name': 'عروض الأسعار', 'icon': 'fas fa-file-lines'},
                {'id': 'sales:reservations', 'name': 'الحجوزات', 'icon': 'fas fa-calendar-check'},
                {'id': 'sales:factory', 'name': 'التصنيع / الورشة', 'icon': 'fas fa-industry'},
                {'id': 'sales:factory_create', 'name': 'أمر شغل جديد', 'icon': 'fas fa-plus'},
                {'id': 'sales:sold_items', 'name': 'مبيعات وتقارير الأصناف', 'icon': 'fas fa-boxes-stacked'},
                {'id': 'sales:refunds', 'name': 'تسجيل مرتجع', 'icon': 'fas fa-reply-all'},
                {'id': 'sales:returns_register', 'name': 'سجل المرتجعات', 'icon': 'fas fa-clock-rotate-left'},
                {'id': 'sales:oversold', 'name': 'سجل مبيعات تجاوزت المخزون', 'icon': 'fas fa-triangle-exclamation'},
                {'id': 'sales:expenses', 'name': 'المصروفات', 'icon': 'fas fa-receipt'},
                {'id': 'financial:statement', 'name': 'كشف الحساب', 'icon': 'fas fa-wallet'},
            ]
        },
        {
            'id': 'shipping', 'name': 'الشحن والأونلاين', 'icon': 'fas fa-truck-fast',
            'sub_links': [
                {'id': 'shipping:dashboard', 'name': 'متابعة الشحن', 'icon': 'fas fa-box-open'},
                {'id': 'shipping:companies', 'name': 'شركات الشحن', 'icon': 'fas fa-shipping-fast'},
            ]
        },
        {
            'id': 'products', 'name': 'إدارة المنتجات', 'icon': 'fas fa-box-open',
            'sub_links': [
                {'id': 'products:list', 'name': 'قائمة المنتجات', 'icon': 'fas fa-barcode'},
                {'id': 'products:create', 'name': 'إضافة صنف', 'icon': 'fas fa-plus'},
                {'id': 'products:bulk_add', 'name': 'إضافة منتجات (Bulk)', 'icon': 'fas fa-plus-circle'},
                {'id': 'products:import', 'name': 'استيراد أصناف', 'icon': 'fas fa-file-import'},
                {'id': 'products:costing', 'name': 'أداة التكليف', 'icon': 'fas fa-calculator'},
                {'id': 'products:alerts', 'name': 'تنبيهات النواقص', 'icon': 'fas fa-bell'},
            ]
        },
        {
            'id': 'inventory', 'name': 'المخازن والمشتريات', 'icon': 'fas fa-warehouse',
            'sub_links': [
                {'id': 'inventory:warehouses', 'name': 'إدارة المخازن', 'icon': 'fas fa-building'},
                {'id': 'inventory:transfer', 'name': 'تحويل بين المستودعات', 'icon': 'fas fa-exchange-alt'},
                {'id': 'inventory:transactions', 'name': 'حركة المخزون', 'icon': 'fas fa-history'},
                {'id': 'inventory:audit', 'name': 'الجرد', 'icon': 'fas fa-clipboard-check'},
                {'id': 'inventory:stock_alerts', 'name': 'تنبيهات النواقص (المستودعات)', 'icon': 'fas fa-triangle-exclamation'},
                {'id': 'inventory:valuation', 'name': 'تقييم المخزون', 'icon': 'fas fa-coins'},
                {'id': 'inventory:expiry', 'name': 'تقرير الصلاحية', 'icon': 'fas fa-hourglass-end'},
                {'id': 'inventory:suppliers', 'name': 'الموردين', 'icon': 'fas fa-truck-field'},
                {'id': 'inventory:price_comparison', 'name': 'مقارنة أسعار الموردين', 'icon': 'fas fa-scale-balanced'},
                {'id': 'inventory:purchase_invoices', 'name': 'فواتير الشراء', 'icon': 'fas fa-file-invoice'},
                {'id': 'inventory:purchase_return', 'name': 'مرتجع شراء', 'icon': 'fas fa-rotate-left'},
                {'id': 'inventory:purchase_orders', 'name': 'أوامر الشراء (PO)', 'icon': 'fas fa-clipboard-list'},
                {'id': 'inventory:ap_aging', 'name': 'أعمار ديون الموردين', 'icon': 'fas fa-hourglass-half'},
            ]
        },
        {
            'id': 'master_data', 'name': 'البيانات الأساسية', 'icon': 'fas fa-database',
            'sub_links': [
                {'id': 'master_data:categories', 'name': 'الفئات', 'icon': 'fas fa-tags'},
                {'id': 'master_data:kinds', 'name': 'الأنواع', 'icon': 'fas fa-list'},
                {'id': 'master_data:sizes', 'name': 'المقاسات', 'icon': 'fas fa-ruler'},
                {'id': 'master_data:units', 'name': 'وحدات القياس', 'icon': 'fas fa-weight'},
            ]
        },
        {
            'id': 'crm', 'name': 'العملاء (CRM)', 'icon': 'fas fa-users',
            'sub_links': [
                {'id': 'crm:list', 'name': 'قائمة العملاء / استيراد', 'icon': 'fas fa-users'},
                {'id': 'crm:create', 'name': 'إضافة عميل', 'icon': 'fas fa-user-plus'},
                {'id': 'crm:ar_aging', 'name': 'أعمار ديون العملاء', 'icon': 'fas fa-hourglass-half'},
            ]
        },
        {
            'id': 'users', 'name': 'إدارة الصلاحيات والمستخدمين', 'icon': 'fas fa-users-gear',
            'sub_links': [
                {'id': 'users:view', 'name': 'المستخدمين والأدوار', 'icon': 'fas fa-users-cog'},
                {'id': 'users:logs', 'name': 'سجلات الأنشطة', 'icon': 'fas fa-history'},
                {'id': 'users:errors', 'name': 'سجل أخطاء النظام', 'icon': 'fas fa-bug'},
                {'id': 'users:broadcast', 'name': 'بث إشعار عام', 'icon': 'fas fa-bullhorn'},
            ]
        },
        {
            'id': 'settings', 'name': 'الإدارة والنظام (الإعدادات)', 'icon': 'fas fa-sliders',
            'sub_links': [
                {'id': 'settings:view', 'name': 'إعدادات النظام', 'icon': 'fas fa-cogs'},
            ]
        },
    ]
    
    if request.method == 'POST':
        selected_modules = request.POST.getlist('modules')
        selected_set = set(selected_modules)

        # direct_permissions fully OVERRIDES the role for any module it lists (see
        # UserProfile.get_all_permissions) — this form only has checkboxes for page
        # visibility ('view' + a couple of sub-pages), with no way to express
        # create/edit/delete. Without this, simply checking a module's sidebar-visibility
        # box here would silently wipe out any create/edit/delete the user's role already
        # granted for that module, replacing it with a bare ['view']. Preserve those
        # role-granted actions instead of clobbering them.
        role_actions = {}
        for role in profile.roles.all():
            for module, actions in role.permissions.items():
                role_actions.setdefault(module, set()).update(actions)

        new_perms = {}

        # Collect all unique top-level module IDs handled by this form
        all_module_ids = [m['id'] for m in modules]

        # Build a map of sub-links per module for splitting: 'mod' -> [action, ...]
        sub_links_map = {}  # e.g. {'sales': ['orders', 'reports', ...], 'financial': ['statement']}
        for m in modules:
            mid = m['id']
            sub_links_map[mid] = []
            for sub in m.get('sub_links', []):
                if ':' in sub['id']:
                    sub_mod, sub_act = sub['id'].split(':')
                    if sub_mod not in sub_links_map:
                        sub_links_map[sub_mod] = []
                    sub_links_map[sub_mod].append(sub_act)

        for m in modules:
            mid = m['id']
            if mid in selected_set:
                # Module is checked: grant 'view' access, preserving any create/edit/delete
                # actions already granted by the user's role(s) for this module.
                preserved = (role_actions.get(mid, set()) - {'__denied__'})
                new_perms[mid] = sorted(preserved | {'view'})
            else:
                # Module is NOT checked: explicit deny to override any role permission
                new_perms[mid] = ['__denied__']
        
        # Now handle sub-links: add granular actions for checked sub-links
        for selected in selected_set:
            if ':' in selected:
                sub_mod, sub_act = selected.split(':')
                if sub_mod in new_perms and '__denied__' not in new_perms[sub_mod]:
                    if sub_act not in new_perms[sub_mod]:
                        new_perms[sub_mod].append(sub_act)
                elif sub_mod not in new_perms:
                    # sub_mod might be a secondary module (e.g. 'financial' under 'sales')
                    new_perms[sub_mod] = ['view', sub_act]
                elif '__denied__' in new_perms.get(sub_mod, []):
                    # Parent module was denied but a sub-link was checked; this shouldn't happen
                    # with our JS, but just in case, grant it.
                    new_perms[sub_mod] = ['view', sub_act]
        
        # Handle special cross-module sub-link: financial:statement is shown under sales section
        # but stored under 'financial' module. If 'financial:statement' is selected,
        # we need to add 'statement' to financial perms without fully unlocking the financial module.
        if 'financial:statement' in selected_set and 'financial' in new_perms:
            if '__denied__' in new_perms['financial']:
                # Override the deny specifically for the statement sub-link
                new_perms['financial'] = ['statement']
        
        profile.direct_permissions = new_perms
        profile.save()
        
        UserActivityLog.objects.create(
            user=request.user, action_type='UPDATE', module='users',
            description=f'تعديل صلاحيات القائمة الجانبية للمستخدم: {user_obj.username}'
        )
        messages.success(request, 'تم حفظ صلاحيات القائمة الجانبية بنجاح.')
        return redirect('user_detail', pk=user_obj.pk)
    
    # GET: Check current effective visibility for the template
    current_perms = profile.get_all_permissions()
    dp = profile.direct_permissions or {}
    
    for m in modules:
        mid = m['id']
        m['has_access'] = (mid in current_perms and ('view' in current_perms[mid] or 'all' in current_perms[mid]))
        # is_direct means: explicitly granted (not __denied__, not absent)
        m['is_direct'] = (mid in dp and '__denied__' not in dp[mid] and ('view' in dp[mid] or 'all' in dp[mid]))
        m['is_denied'] = (mid in dp and '__denied__' in dp[mid])
        
        for sub in m.get('sub_links', []):
            if ':' in sub['id']:
                sub_mod, sub_act = sub['id'].split(':')
                sub['has_access'] = (sub_mod in current_perms and (sub_act in current_perms[sub_mod] or 'all' in current_perms[sub_mod]))
                sub['is_direct'] = (sub_mod in dp and sub_act in dp[sub_mod] and '__denied__' not in dp[sub_mod])
                sub['is_denied'] = (sub_mod in dp and '__denied__' in dp[sub_mod])

    return render(request, 'accounts/user_sidebar_permissions.html', {
        'user_obj': user_obj,
        'modules': modules,
        'title': f'تخصيص القائمة الجانبية: {user_obj.username}'
    })

@login_required
@require_permission('users', 'view')
def audit_log(request):
    """Searchable activity / audit log browser (Phase 3.8)."""
    from django.core.paginator import Paginator
    from .models import UserActivityLog

    logs = UserActivityLog.objects.select_related('user').order_by('-timestamp')

    q = request.GET.get('q', '').strip()
    if q:
        logs = logs.filter(Q(description__icontains=q) | Q(user__username__icontains=q))
    action = request.GET.get('action', '')
    if action:
        logs = logs.filter(action_type=action)
    module = request.GET.get('module', '').strip()
    if module:
        logs = logs.filter(module=module)
    user_id = request.GET.get('user', '')
    if user_id:
        logs = logs.filter(user_id=user_id)
    df = request.GET.get('date_from')
    dt = request.GET.get('date_to')
    if df:
        logs = logs.filter(timestamp__date__gte=df)
    if dt:
        logs = logs.filter(timestamp__date__lte=dt)

    paginator = Paginator(logs, 50)
    page_obj = paginator.get_page(request.GET.get('page'))

    modules = (UserActivityLog.objects.exclude(module='').values_list('module', flat=True)
               .distinct().order_by('module'))
    return render(request, 'accounts/audit_log.html', {
        'title': 'سجل النشاط (التدقيق)', 'page_obj': page_obj,
        'action_choices': UserActivityLog._meta.get_field('action_type').choices,
        'modules': list(modules), 'users': User.objects.order_by('username'),
        'q': q, 'action': action, 'module': module, 'user_id': user_id,
        'date_from': df or '', 'date_to': dt or '', 'total': paginator.count,
    })


@login_required
@require_permission('sales', 'manage')
def approvals_log(request):
    """Audit register of manager-authorized overrides (Phase 3.3)."""
    from django.core.paginator import Paginator
    from .models import ApprovalRequest

    rows = ApprovalRequest.objects.select_related('requested_by', 'approved_by').order_by('-created_at')

    kind = request.GET.get('kind', '').strip()
    if kind:
        rows = rows.filter(kind=kind)
    status = request.GET.get('status', '').strip()
    if status:
        rows = rows.filter(status=status)
    df = request.GET.get('date_from')
    dt = request.GET.get('date_to')
    if df:
        rows = rows.filter(created_at__date__gte=df)
    if dt:
        rows = rows.filter(created_at__date__lte=dt)

    paginator = Paginator(rows, 50)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'accounts/approvals_log.html', {
        'title': 'سجل اعتمادات المدير', 'page_obj': page_obj,
        'kind_choices': ApprovalRequest.KIND_CHOICES,
        'status_choices': ApprovalRequest.STATUS_CHOICES,
        'kind': kind, 'status': status, 'date_from': df or '', 'date_to': dt or '',
        'total': paginator.count,
    })


@login_required
def customize_shortcuts(request):
    """Let the user pick which shortcuts (favorites) appear in the sidebar/home."""
    from .shortcuts import available_for, available_grouped
    profile = getattr(request.user, 'profile', None)
    available = available_for(request.user)
    if request.method == 'POST':
        chosen = request.POST.getlist('favorites')
        valid_keys = {s['key'] for s in available}
        if profile is not None:
            profile.favorites = [k for k in chosen if k in valid_keys]
            profile.save(update_fields=['favorites'])
        messages.success(request, 'تم حفظ اختصاراتك المفضلة.')
        return redirect('customize_shortcuts')
    current = set((profile.favorites or []) if profile else [])
    groups = available_grouped(request.user)
    return render(request, 'accounts/customize_shortcuts.html', {
        'title': 'تخصيص الاختصارات', 'available': available, 'current': current, 'groups': groups,
    })


def user_shortcuts_context(request):
    """Context processor: expose the user's resolved favorites to all templates."""
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    try:
        from .shortcuts import resolve_favorites
        return {'user_favorites': resolve_favorites(request.user)}
    except Exception:
        return {'user_favorites': []}
