from django.contrib.admin import AdminSite
from django.urls import path


class CustomAdminSite(AdminSite):
    site_header  = "MR MEKAWY | لوحة الإدارة"
    site_title   = "MR MEKAWY Admin"
    index_title  = "لوحة التحكم الرئيسية"

    def get_urls(self):
        from dashboard.admin_views import admin_dashboard
        urls = super().get_urls()
        custom_urls = [
            path('dashboard/', self.admin_view(admin_dashboard), name='admin_dashboard'),
        ]
        return custom_urls + urls

    def index(self, request, extra_context=None):
        """Override the default index to redirect to our custom dashboard."""
        from django.shortcuts import redirect
        return redirect('admin:admin_dashboard')
