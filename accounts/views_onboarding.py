import uuid
from datetime import timedelta
from django.shortcuts import render, redirect
from django.utils import timezone
from django.contrib.auth.decorators import login_required
from accounts.models import UserProfile
from django.contrib import messages
from settings.models import SystemSetting
from licensing.models import SystemLicense

@login_required
def onboarding_wizard(request):
    """
    عجلة التهيئة للمستخدمين الجدد أو الذين لديهم ملفات غير مكتملة
    """
    profile = request.user.profile
    
    # Only Master can access onboarding wizard
    if not profile.is_master:
        return redirect('dashboard')
        
    if profile.onboarding_completed:
        return redirect('dashboard')
        
    sys_settings = SystemSetting.objects.first()

    # Prepare context
    context = {
        'user': request.user,
        'profile': profile,
        'sys_settings': sys_settings,
        'market_choices': SystemSetting.MARKET_TYPE_CHOICES
    }

    if request.method == 'POST':
        # Simple step handling
        step = request.POST.get('step', '1')
        direction = request.POST.get('action', 'next')
        
        # Handle Back Navigation
        if direction == 'back':
            prev_step = int(step) - 1
            if prev_step < 0: prev_step = 0
            context['step'] = prev_step
            return render(request, 'accounts/onboarding.html', context)

        if step == '0':
            market_type = request.POST.get('market_type')
            if market_type:
                if not sys_settings:
                    sys_settings = SystemSetting.objects.create(pk=1, market_type=market_type, is_market_type_locked=False)
                else:
                    sys_settings.market_type = market_type
                    sys_settings.is_market_type_locked = False
                    sys_settings.save()
                
                # Automatically reuse or create SystemLicense with unique Store ID!
                system_license = SystemLicense.objects.first()
                if not system_license:
                    system_license = SystemLicense.objects.create(
                        store_id=f"STORE-{uuid.uuid4().hex[:8].upper()}",
                        store_type=market_type,
                        is_locked=False,
                        # Same 6-month trial the activation view grants when it auto-creates
                        # a license. Without it the row is stored with a NULL expiry, and
                        # every later "how long is left?" calculation has nothing to work
                        # from — which is what broke the activation page on fresh installs.
                        subscription_expires_at=timezone.now() + timedelta(days=180),
                    )
                else:
                    system_license.store_type = market_type
                    system_license.save()
                
                messages.success(request, f"تم ضبط نوع المتجر بنجاح: {sys_settings.get_market_type_display()}")
            
            context['step'] = 1
            return render(request, 'accounts/onboarding.html', context)
            
        elif step == '1':
            first_name = request.POST.get('first_name', '').strip()
            phone = request.POST.get('phone', '').strip()
            
            if not first_name or not phone:
                messages.error(request, "يرجى إدخال الاسم الأول ورقم الهاتف للمتابعة.")
                context['step'] = 1
                return render(request, 'accounts/onboarding.html', context)

            # Check if phone is already used by another user
            from accounts.models import UserProfile as UP
            if phone and UP.objects.filter(phone=phone).exclude(pk=profile.pk).exists():
                messages.error(request, "رقم الهاتف هذا مسجل بالفعل لحساب آخر.")
                context['step'] = 1
                return render(request, 'accounts/onboarding.html', context)

            request.user.first_name = first_name
            request.user.last_name = request.POST.get('last_name', '')
            request.user.save()
            # Store None instead of "" so unique constraint allows multiple blank phones
            profile.phone = phone if phone else None
            profile.save()
            context['step'] = 2
            return render(request, 'accounts/onboarding.html', context)
            
        elif step == '2':
            profile.department = request.POST.get('department', '')
            profile.job_title = request.POST.get('job_title', '')
            profile.branch = request.POST.get('branch', '')
            profile.save()
            context['step'] = 3
            return render(request, 'accounts/onboarding.html', context)
            
        elif step == '3':
            # This is now the "Overview/Confirm" step
            context['step'] = 4
            return render(request, 'accounts/onboarding.html', context)
            
        elif step == '4':
            profile.onboarding_completed = True
            profile.save()
            
            # Lock the market type now that onboarding is completed!
            if sys_settings:
                sys_settings.is_market_type_locked = True
                sys_settings.save()
                
            system_license = SystemLicense.objects.first()
            if system_license:
                system_license.is_locked = True
                system_license.save()
                
            messages.success(request, "تم اكتمال تهيئة الحساب بنجاح! مرحباً بك يا ملك النظام.")
            return redirect('dashboard')
            

    # ALWAYS start at step 0 (market type selection), always!
    context['step'] = 0
    return render(request, 'accounts/onboarding.html', context)
