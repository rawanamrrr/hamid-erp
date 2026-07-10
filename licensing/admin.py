from django.contrib import admin
from django.shortcuts import render, redirect
from django.urls import path, reverse
from django.utils import timezone
from django.http import JsonResponse
from django.conf import settings
from django.shortcuts import HttpResponseRedirect
from datetime import timedelta
from .models import MasterStore, Device, TokenLog, SystemLicense, DeveloperAccount
from .utils import generate_token, validate_token, dev_login_required


@admin.register(DeveloperAccount)
class DeveloperAccountAdmin(admin.ModelAdmin):
    list_display = ['username', 'is_active', 'created_at']
    search_fields = ['username']
    
    def get_readonly_fields(self, request, obj=None):
        if obj:
            return ['created_at']
        return ['created_at']

    def save_model(self, request, obj, form, change):
        if not change or 'password' in form.changed_data:
            # If creating or changing password, hash it
            if 'password' in form.cleaned_data and form.cleaned_data['password']:
                obj.set_password(form.cleaned_data['password'])
        super().save_model(request, obj, form, change)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if obj is None:
            # For new account, add password field
            if 'password_hash' in form.base_fields:
                del form.base_fields['password_hash']
            from django import forms
            class DynamicDeveloperAccountForm(form):
                password = forms.CharField(
                    widget=forms.PasswordInput,
                    required=True,
                    help_text="Enter a secure password"
                )
            return DynamicDeveloperAccountForm
        else:
            return form


@admin.register(MasterStore)
class MasterStoreAdmin(admin.ModelAdmin):
    list_display = ['store_id', 'store_name', 'store_type', 'license_status', 'subscription_expires_at', 'created_at']
    list_filter = ['store_type', 'license_status']
    search_fields = ['store_id', 'store_name', 'public_id']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = [
        ('Basic Info', {'fields': ['store_id', 'public_id', 'store_name', 'store_type']}),
        ('License', {'fields': ['license_status', 'subscription_expires_at']}),
        ('Dates', {'fields': ['created_at', 'updated_at']}),
    ]


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ['id', 'store', 'device_name', 'is_authorized', 'last_used_at', 'created_at']
    list_filter = ['is_authorized', 'store']
    search_fields = ['device_name', 'device_info']
    readonly_fields = ['created_at', 'last_used_at']


@admin.register(TokenLog)
class TokenLogAdmin(admin.ModelAdmin):
    list_display = ['action', 'store', 'is_used', 'generated_at', 'expires_at', 'used_at']
    list_filter = ['action', 'is_used', 'store']
    readonly_fields = ['token', 'generated_at', 'used_at']


@admin.register(SystemLicense)
class SystemLicenseAdmin(admin.ModelAdmin):
    list_display = ['store_id', 'store_type', 'subscription_expires_at', 'is_locked', 'last_updated_at']
    list_filter = ['store_type', 'is_locked']
    readonly_fields = ['last_updated_at', 'device_id']


class LicensingAdmin(admin.AdminSite):
    site_header = 'Licensing System'
    site_title = 'Licensing Admin'
    index_title = 'Licensing Dashboard'
    site_url = None

    def index(self, request, extra_context=None):
        extra_context = extra_context or {}
        return super().index(request, extra_context=extra_context)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('token-generator/', dev_login_required(self.admin_view(self.token_generator_view)), name='token_generator'),
            path('api/generate-token/', dev_login_required(self.admin_view(self.generate_token_api)), name='api_generate_token'),
        ]
        return custom_urls + urls

    def token_generator_view(self, request):
        stores = MasterStore.objects.all()
        context = dict(
            self.each_context(request),
            stores=stores,
            title='Activation Token Generator',
        )
        return render(request, 'admin/licensing/token_generator.html', context)

    def generate_token_api(self, request):
        if request.method == 'POST':
            import json
            data = json.loads(request.body)
            
            store_id = data.get('store_id')
            action = data.get('action')
            value = data.get('value')
            expires_in = int(data.get('expires_in', 60))  # Default 60 minutes

            try:
                if not store_id:
                    return JsonResponse({'success': False, 'error': 'store_id is required'}, status=400)
                # Auto-register the store so an onboarding token can be minted for a brand-new
                # customer in one step (no separate "create store" action needed).
                store, _ = MasterStore.objects.get_or_create(
                    store_id=store_id, defaults={'store_name': store_id})
                token = generate_token(store_id, action, value, expires_in)

                expires_at = timezone.now() + timedelta(minutes=expires_in)
                TokenLog.objects.create(
                    store=store, action=action, value=str(value),
                    token=token, expires_at=expires_at,
                )
                return JsonResponse({
                    'success': True, 'token': token, 'expires_at': expires_at.isoformat(),
                })
            except Exception as e:
                return JsonResponse({'success': False, 'error': str(e)}, status=400)

        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


licensing_admin = LicensingAdmin(name='licensing_admin')
licensing_admin.register(DeveloperAccount, DeveloperAccountAdmin)
licensing_admin.register(MasterStore, MasterStoreAdmin)
licensing_admin.register(Device, DeviceAdmin)
licensing_admin.register(TokenLog, TokenLogAdmin)
licensing_admin.register(SystemLicense, SystemLicenseAdmin)
