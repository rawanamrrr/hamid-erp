from django import forms
from django.contrib.auth.models import User
from accounts.models import Role

class BroadcastNotificationForm(forms.Form):
    TARGET_CHOICES = [
        ('all', 'جميع المستخدمين'),
        ('role', 'رتبة معينة'),
        ('user', 'مستخدمين محددين (متعدد)'),
    ]
    
    target_type = forms.ChoiceField(choices=TARGET_CHOICES, label="الفئة المستهدفة")
    target_role = forms.ModelChoiceField(queryset=Role.objects.all(), required=False, label="الرتبة")
    # Changed to ModelMultipleChoiceField
    target_users = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(is_active=True), 
        required=False, 
        label="المستخدمين المختارين",
        widget=forms.SelectMultiple(attrs={
            'class': 'w-full px-4 py-2 border rounded-xl focus:ring focus:ring-teal-200 outline-none h-40'
        })
    )
    
    title = forms.CharField(max_length=255, label="عنوان الإشعار", widget=forms.TextInput(attrs={
        'placeholder': 'مثال: تحديث جديد في النظام',
        'class': 'w-full px-4 py-2 border rounded-xl focus:ring focus:ring-teal-200 outline-none'
    }))
    
    message = forms.CharField(widget=forms.Textarea(attrs={
        'rows': 4,
        'placeholder': 'اكتب نص الإشعار هنا...',
        'class': 'w-full px-4 py-2 border rounded-xl focus:ring focus:ring-teal-200 outline-none'
    }), label="محتوى الإشعار")
    
    link = forms.CharField(max_length=255, required=False, label="رابط (اختياري)", widget=forms.TextInput(attrs={
        'placeholder': '/sales/orders/',
        'class': 'w-full px-4 py-2 border rounded-xl focus:ring focus:ring-teal-200 outline-none'
    }))
