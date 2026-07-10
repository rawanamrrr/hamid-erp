import uuid
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from datetime import timedelta
from .models import MasterStore, SystemLicense, TokenLog, DeveloperAccount
from .utils import validate_token, parse_token_value, dev_login_required


def activation_view(request):
    system_license = None
    try:
        system_license = SystemLicense.objects.first()
    except Exception as e:
        print(f"Error getting SystemLicense: {str(e)}")

    if request.method == 'POST':
        # Handle token activation
        token = request.POST.get('token', '')
        is_valid, result = validate_token(token)

        if is_valid:
            payload = result
            store_id = payload['store_id']
            action = payload['action']
            value = parse_token_value(action, payload['value'])

            print(f"Store: {store_id}, Action: {action}, Value: {value}")
            
            system_license, created = SystemLicense.objects.get_or_create(
                store_id=store_id
            )

            # Replay protection: a token is single-use on this install. Reject re-submission
            # of an already-consumed token (otherwise an EXTEND_SUBSCRIPTION token could be
            # replayed within its validity window to stack subscription time).
            from .utils import token_fingerprint
            fp = token_fingerprint(token)
            used = system_license.used_token_hashes or []
            if fp in used:
                return JsonResponse({
                    'success': False,
                    'message': 'هذا الرمز مستخدم بالفعل ولا يمكن استخدامه مرة أخرى'
                }, status=400)

            # If new license, set 6-month default trial
            if created:
                system_license.subscription_expires_at = timezone.now() + timedelta(days=180)
                print("Created new license with 6-month trial")
            
            # COMPLETE RESET - THIS CAN ONLY BE DONE WITH A VALID TOKEN!
            system_license.grace_period_used = False
            system_license.grace_period_started_at = None
            system_license.system_locked = False
            
            print(f"System license created/got: {system_license}, created: {created}")

            success_message = ""

            if action == "CHANGE_STORE_TYPE":
                system_license.store_type = value
                success_message = f"Store type changed to {value}"
                try:
                    from settings.models import SystemSetting
                    sys_setting, _ = SystemSetting.objects.get_or_create(pk=1)
                    sys_setting.market_type = value
                    sys_setting.is_market_type_locked = True
                    sys_setting.save()
                    print("Updated SystemSetting as well!")
                except Exception as e:
                    print(f"Error updating SystemSetting: {str(e)}")
            elif action == "EXTEND_SUBSCRIPTION":
                # Value is total days (can be float for hours)
                days = value
                if system_license.subscription_expires_at:
                    # ALWAYS add/subtract from the existing expiry date.
                    # Never reset to 'now' — that causes unpredictable jumps.
                    system_license.subscription_expires_at += timedelta(days=days)
                else:
                    system_license.subscription_expires_at = timezone.now() + timedelta(days=days)

                # Reset grace period state so it is recalculated from the new expiry.
                # This is critical for testing with negative values.
                system_license.grace_period_started_at = None
                system_license.grace_period_used = False
                system_license.system_locked = False

                success_message = f"Subscription {'extended' if days > 0 else 'reduced'} by {abs(days)} days (~{round(abs(days)/30, 1)} months)"
            elif action == "DEVICE_AUTHORIZATION":
                success_message = "Device authorized"
            elif action == "TOGGLE_DARK_MODE":
                system_license.dark_mode_enabled = (value in [True, "True", "true", "1", 1])
                success_message = f"Dark mode {'enabled' if system_license.dark_mode_enabled else 'disabled'}"
            elif action == "ENABLE_MODULE":
                if value not in system_license.enabled_modules:
                    system_license.enabled_modules.append(value)
                success_message = f"Module '{value}' enabled"
            elif action == "DISABLE_MODULE":
                if value in system_license.enabled_modules:
                    system_license.enabled_modules.remove(value)
                success_message = f"Module '{value}' disabled"
            elif action == "GENERAL_SYSTEM_OVERRIDE":
                success_message = "System override applied"
            elif action == "CREATE_MASTER_USER":
                # Remotely provision a Master account on the customer's POS.
                # value = "username|password|email|sub_days"
                # email and sub_days are optional.
                # sub_days: number of subscription days to grant (0 or empty = keep existing trial).
                from django.contrib.auth.models import User
                from accounts.models import UserProfile
                parts = [p.strip() for p in str(value).split('|')]
                if len(parts) < 2 or not parts[0] or not parts[1]:
                    return JsonResponse({'success': False, 'message': 'صيغة غير صحيحة، استخدم username|password أو username|password|email|sub_days'}, status=400)
                uname, pwd = parts[0], parts[1]
                email = parts[2].lower() if len(parts) >= 3 and parts[2] else ''
                sub_days = 0
                if len(parts) >= 4 and parts[3]:
                    try:
                        sub_days = float(parts[3])
                    except ValueError:
                        sub_days = 0
                if User.objects.filter(username=uname).exists():
                    return JsonResponse({'success': False, 'message': f'المستخدم {uname} موجود بالفعل'}, status=400)
                if email and User.objects.filter(email__iexact=email).exists():
                    return JsonResponse({'success': False, 'message': f'البريد {email} مستخدم بالفعل'}, status=400)
                new_master = User.objects.create_user(username=uname, password=pwd, email=email)
                new_master.is_staff = True
                new_master.is_superuser = True
                new_master.save()
                prof, _ = UserProfile.objects.get_or_create(user=new_master)
                prof.is_master = True
                prof.save()
                # Apply subscription period if specified
                if sub_days > 0:
                    system_license.subscription_expires_at = timezone.now() + timedelta(days=sub_days)
                    system_license.grace_period_used = False
                    system_license.system_locked = False
                success_message = f"تم إنشاء حساب المالك: {uname}" + (f" واشتراك لمدة {round(sub_days/30, 1)} شهر" if sub_days > 0 else "")
            elif action == "RESET_USER_PASSWORD":
                # Dev recovery for a locked-out owner/user (works fully offline — local HMAC).
                # value = "username|newpassword".
                from django.contrib.auth.models import User
                parts = [p.strip() for p in str(value).split('|')]
                if len(parts) < 2 or not parts[0] or not parts[1]:
                    return JsonResponse({'success': False, 'message': 'صيغة غير صحيحة، استخدم username|newpassword'}, status=400)
                uname, newpwd = parts[0], parts[1]
                target = User.objects.filter(username=uname).first()
                if not target:
                    return JsonResponse({'success': False, 'message': f'المستخدم {uname} غير موجود'}, status=400)
                target.set_password(newpwd)
                target.save()
                success_message = f"تم تغيير كلمة مرور المستخدم {uname}"

            # Update last token and SAVE - this will regenerate the secure signature!
            system_license.last_token_used = token
            # Mark this token consumed so it can never be replayed (single-use).
            used.append(fp)
            system_license.used_token_hashes = used[-500:]  # keep the list bounded
            system_license.save()  # Important: This triggers get_full_signature()!
            print("Saved system license with new secure signature!")

            try:
                from licensing.models import TokenLog, MasterStore
                master_store = MasterStore.objects.filter(store_id=store_id).first()
                if master_store:
                    token_log = TokenLog.objects.create(
                        store=master_store,
                        action=action,
                        value=str(value),
                        token=token,
                        expires_at=payload['expires_at']
                    )
                    token_log.is_used = True
                    token_log.used_at = timezone.now()
                    token_log.used_by = "anonymous"
                    token_log.save()
                    print("Saved TokenLog!")
            except Exception as e:
                print(f"Error saving TokenLog: {str(e)}")

            return JsonResponse({
                'success': True,
                'message': success_message,
                'payload': payload
            })
        else:
            print(f"Invalid token! Reason: {result}")
            return JsonResponse({
                'success': False,
                'message': result
            }, status=400)

    # If no license yet, auto-create one with 6-month trial
    if not system_license:
        try:
            system_license = SystemLicense.objects.create(
                store_id=f"STORE-{uuid.uuid4().hex[:8].upper()}",
                subscription_expires_at=timezone.now() + timedelta(days=180)
            )
            print("Auto-created initial license with 6-month trial")
        except Exception as e:
            print(f"Error auto-creating license: {e}")

    # Phase ①: ensure a per-store signing key exists, and only reveal it to a logged-in
    # master/admin (it's a secret — anyone who reads it could forge this store's tokens).
    if system_license and not system_license.license_signing_key:
        system_license.save()  # generates the key
    profile = getattr(request.user, 'profile', None)
    can_see_signing_key = bool(
        request.user.is_authenticated and (
            request.user.is_superuser or request.user.is_staff or getattr(profile, 'is_master', False)
        )
    )

    return render(request, 'licensing/activation.html', {
        'system_license': system_license,
        'can_see_signing_key': can_see_signing_key,
    })


def licensing_status(request):
    try:
        system_license = SystemLicense.objects.first()
        if not system_license:
            return JsonResponse({
                'status': 'not_configured',
                'store_id': None,
                'store_type': 'general',
                'is_locked': True
            })
        
        # Check tampering
        if not system_license.is_signature_valid():
            return JsonResponse({
                'status': 'tampered',
                'store_id': system_license.store_id,
                'store_type': system_license.store_type,
                'is_locked': True,
                'message': 'License tampered'
            })

        return JsonResponse({
            'status': 'active',
            'store_id': system_license.store_id,
            'store_type': system_license.store_type,
            'subscription_expires_at': system_license.subscription_expires_at.isoformat() if system_license.subscription_expires_at else None,
            'days_remaining': system_license.days_remaining,
            'is_locked': system_license.is_locked,
            'device_id': str(system_license.device_id) if system_license.device_id else None,
            'in_grace_period': system_license.days_remaining == 5 and not system_license.is_expired,
            'grace_period_used': system_license.grace_period_used
        })
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)


def dev_login_view(request):
    """Developer login page for licensing admin"""
    from .urls import SECRET_DEV_PATH
    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password', '')
        
        try:
            dev_account = DeveloperAccount.objects.get(username=username, is_active=True)
            if dev_account.check_password(password):
                # Login successful!
                request.session['licensing_dev_logged_in'] = True
                request.session['licensing_dev_username'] = username
                return redirect(f'/licensing/{SECRET_DEV_PATH}/')
            else:
                return render(request, 'licensing/dev_login.html', {'error': 'Invalid credentials'})
        except DeveloperAccount.DoesNotExist:
            return render(request, 'licensing/dev_login.html', {'error': 'Invalid credentials'})
            
    if request.session.get('licensing_dev_logged_in', False):
        return redirect(f'/licensing/{SECRET_DEV_PATH}/')
    
    # Auto-create initial dev account for first use (we'll prompt user to change it later)
    if not DeveloperAccount.objects.exists():
        initial_dev = DeveloperAccount(username='dev')
        initial_dev.set_password('admin123')  # Default initial password - CHANGE FIRST THING!
        initial_dev.save()
        
    return render(request, 'licensing/dev_login.html')


def dev_logout_view(request):
    """Developer logout"""
    request.session.flush()
    return redirect('licensing_dev_login')
