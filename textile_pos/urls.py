from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from textile_pos.admin_site import CustomAdminSite


# Monkey-patch the default admin.site to use our custom class
# This preserves all existing model registrations from app admin.py files
admin.site.__class__ = CustomAdminSite
admin.site.site_header = "MR MEKAWY | لوحة الإدارة"
admin.site.site_title  = "MR MEKAWY Admin"
admin.site.index_title = "لوحة التحكم الرئيسية"

urlpatterns = [
    path('licensing/', include('licensing.urls')),
    path('admin/', admin.site.urls),

    # Authentication & User Management
    path('accounts/', include('accounts.urls')),

    # Dashboard & Inventory (Root URL points here)
    path('', include('dashboard.urls')),
    path('products/', include('products.urls')),

    # CRM (Customer Relationship Management)
    path('crm/', include('crm.urls')),

    # Sales & POS System
    path('sales/', include('sales.urls')),
    path('', include('settings.urls')),
    
    # Search System
    path('search/', include('search_system.urls')),
    
    # camera live
    path('camera/', include('camera_view.urls')),
    
    path('shipping/', include('shipping.urls')),
    
    path('financial/', include('financial.urls', namespace='financial')),
    path('notifications/', include('notifications.urls')),
    path('restaurant/', include('restaurant.urls', namespace='restaurant')),
    path('attendance-devices/', include('attendance_devices.urls', namespace='attendance_devices')),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)