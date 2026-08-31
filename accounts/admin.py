from django.contrib import admin
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin, GroupAdmin
from .models import SystemError

# تعريب عناوين لوحة التحكم الرئيسية
admin.site.site_header = "DigiFlow | لوحة الإدارة"
admin.site.site_title = "DigiFlow Admin"
admin.site.index_title = "لوحة التحكم الرئيسية"

# يمكننا إلغاء تسجيل النماذج الافتراضية وإعادة تسجيلها إذا أردنا تخصيصها مستقبلاً
# حالياً سنتركها كما هي ولكن العناوين أعلاه ستغير واجهة الدخول والقائمة الرئيسية


@admin.register(SystemError)
class SystemErrorAdmin(admin.ModelAdmin):
    list_display = ('exception_type', 'path', 'user', 'source', 'is_resolved', 'timestamp')
    list_filter = ('source', 'is_resolved')
    search_fields = ('path', 'exception_type', 'message')
    readonly_fields = ('user', 'path', 'exception_type', 'message', 'traceback',
                        'ip_address', 'user_agent', 'source', 'timestamp')