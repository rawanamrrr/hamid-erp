from django.contrib import admin
from .models import Camera

@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ('name', 'ip_address', 'port', 'username', 'updated_at')
    search_fields = ('name', 'ip_address')
    list_filter = ('created_at',)
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('name',)
        }),
        ('Connection Credentials', {
            'fields': ('ip_address', 'port', 'username', 'password')
        }),
        ('Stream Paths', {
            'fields': ('stream_path_hd', 'stream_path_sd'),
            'description': 'Customize these paths if your camera uses different suffixes like /main or /sub'
        }),
    )