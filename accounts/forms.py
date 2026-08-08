from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from .models import UserProfile

TAILWIND_INPUT = 'w-full px-4 py-2 mt-2 text-gray-700 bg-white border border-gray-300 rounded-md focus:border-indigo-500 focus:outline-none focus:ring focus:ring-indigo-300 focus:ring-opacity-40'

class LoginForm(AuthenticationForm):
    def __init__(self, *args, **kwargs):
        super(LoginForm, self).__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'class': TAILWIND_INPUT, 'placeholder': 'اسم المستخدم', 'dir': 'ltr'})
        self.fields['password'].widget.attrs.update({'class': TAILWIND_INPUT, 'placeholder': 'كلمة المرور', 'dir': 'ltr'})

class CreateUserForm(forms.ModelForm):
    """
    نموذج إضافة مستخدم جديد (معدل ليتوافق مع نظام الأدوار الجديدة RBAC بدلاً من Groups)
    """
    # min_length=6 (not Django's default 8) is the only hard-enforced rule — matches
    # AUTH_PASSWORD_VALIDATORS and the forgot-password/admin-reset flows. A weak-but-
    # long-enough password (common word, all-numeric, etc.) is intentionally NOT
    # blocked here.
    password = forms.CharField(label="كلمة المرور", min_length=6,
                                widget=forms.PasswordInput(attrs={'class': TAILWIND_INPUT, 'dir': 'ltr'}))
    # Optional — kept unique when provided (still the password-recovery channel for
    # whoever does have one on file), but not every staff account has an email in
    # practice, so it no longer blocks creating the account.
    email = forms.EmailField(
        label="البريد الإلكتروني", required=False,
        widget=forms.EmailInput(attrs={'class': TAILWIND_INPUT, 'dir': 'ltr', 'placeholder': 'email@example.com (اختياري)'}),
    )
    # Phone is optional but unique when provided (one phone ↔ one account).
    phone = forms.CharField(
        label="رقم الهاتف", max_length=20, required=False,
        widget=forms.TextInput(attrs={'class': TAILWIND_INPUT, 'dir': 'ltr', 'placeholder': '01xxxxxxxxx (اختياري)'}),
    )

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        if not email:
            return ''
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("البريد الإلكتروني مستخدم بالفعل لحساب آخر.")
        return email

    def clean_phone(self):
        from accounts.models import UserProfile
        phone = (self.cleaned_data.get('phone') or '').strip()
        if phone and UserProfile.objects.filter(phone=phone).exists():
            raise forms.ValidationError("رقم الهاتف مستخدم بالفعل لحساب آخر.")
        return phone or None

    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        widgets = {
            'username': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'dir': 'ltr'}),
            'first_name': forms.TextInput(attrs={'class': TAILWIND_INPUT}),
            'last_name': forms.TextInput(attrs={'class': TAILWIND_INPUT}),
            'email': forms.EmailInput(attrs={'class': TAILWIND_INPUT, 'dir': 'ltr'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'w-5 h-5 text-emerald-600 rounded'})
        }

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop('request_user', None)
        super().__init__(*args, **kwargs)
        
        # Allow Master to create Admin accounts
        if self.request_user and hasattr(self.request_user, 'profile') and self.request_user.profile.is_master:
            self.fields['is_superuser'] = forms.BooleanField(
                label="مدير نظام (Admin)",
                required=False,
                help_text="تحذير: هذا الخيار يتجاوز كافة الصلاحيات والأدوار، ويعطي تحكماً كاملاً 100% بالنظام.",
                widget=forms.CheckboxInput(attrs={'class': 'w-5 h-5 text-indigo-600 rounded'})
            )

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password"])
        
        # Only set is_superuser if current user is Master
        if self.request_user and hasattr(self.request_user, 'profile') and self.request_user.profile.is_master:
            user.is_superuser = self.cleaned_data.get('is_superuser', False)
        else:
            user.is_superuser = False
        
        if commit:
            user.save()
        return user

class EditUserForm(forms.ModelForm):
    """
    نموذج تعديل المستخدم (بدون تعديل كلمة المرور)
    """
    class Meta:
        model = User
        fields = ['username', 'first_name', 'last_name', 'email', 'is_active']
        widgets = {
            'username': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'dir': 'ltr'}),
            'first_name': forms.TextInput(attrs={'class': TAILWIND_INPUT}),
            'last_name': forms.TextInput(attrs={'class': TAILWIND_INPUT}),
            'email': forms.EmailInput(attrs={'class': TAILWIND_INPUT, 'dir': 'ltr'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'w-5 h-5 text-emerald-600 rounded'})
        }

    def __init__(self, *args, **kwargs):
        self.request_user = kwargs.pop('request_user', None)
        super().__init__(*args, **kwargs)
        
        # Allow Master to modify Admin status
        if self.request_user and hasattr(self.request_user, 'profile') and self.request_user.profile.is_master:
            self.fields['is_superuser'] = forms.BooleanField(
                label="مدير نظام (Admin)",
                required=False,
                help_text="تحذير: هذا الخيار يتجاوز كافة الصلاحيات والأدوار، ويعطي تحكماً كاملاً 100% بالنظام.",
                widget=forms.CheckboxInput(attrs={'class': 'w-5 h-5 text-indigo-600 rounded'})
            )

    def clean(self):
        cleaned_data = super().clean()
        
        # Only allow Master to change is_superuser
        if self.request_user and hasattr(self.request_user, 'profile') and self.request_user.profile.is_master:
            cleaned_data['is_superuser'] = self.cleaned_data.get('is_superuser', False)
        else:
            cleaned_data['is_superuser'] = self.instance.is_superuser
        
        return cleaned_data

class ProfileUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'الاسم الأول'}),
            'last_name': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'الاسم الأخير'}),
            'email': forms.EmailInput(attrs={'class': TAILWIND_INPUT, 'dir': 'ltr', 'placeholder': 'email@example.com'}),
        }

class ProfileDetailsForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ['phone', 'department', 'job_title', 'branch', 'profile_photo']
        widgets = {
            'phone': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'dir': 'ltr', 'placeholder': '01xxxxxxxxx'}),
            'department': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'القسم'}),
            'job_title': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'المسمى الوظيفي'}),
            'branch': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'الفرع'}),
            'profile_photo': forms.ClearableFileInput(attrs={'class': 'block w-full text-sm text-gray-700 file:mr-3 file:py-2 file:px-4 file:rounded-lg file:border-0 file:text-sm file:font-bold file:bg-teal-50 file:text-teal-700 hover:file:bg-teal-100'}),
        }

class ProfilePasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].widget.attrs.update({'class': TAILWIND_INPUT, 'dir': 'ltr', 'placeholder': 'كلمة المرور الحالية'})
        self.fields['new_password1'].widget.attrs.update({'class': TAILWIND_INPUT, 'dir': 'ltr', 'placeholder': 'كلمة المرور الجديدة'})
        self.fields['new_password2'].widget.attrs.update({'class': TAILWIND_INPUT, 'dir': 'ltr', 'placeholder': 'تأكيد كلمة المرور الجديدة'})
