from django import forms
from .models import SystemSetting
from .market_profiles import MARKET_TYPE_CHOICES

TAILWIND_INPUT = 'w-full px-4 py-2 mt-2 text-gray-700 bg-white border border-gray-300 rounded-md focus:border-teal-500 focus:outline-none focus:ring focus:ring-teal-300 focus:ring-opacity-40'
TAILWIND_CHECKBOX = 'w-5 h-5 text-teal-600 rounded border-gray-300 focus:ring-teal-500'
TAILWIND_SELECT = 'w-full px-4 py-2 mt-2 text-gray-700 bg-white border border-gray-300 rounded-md focus:border-teal-500 focus:outline-none focus:ring focus:ring-teal-300 focus:ring-opacity-40'

class SystemSettingForm(forms.ModelForm):
    # حقل وهمي لرفع الصورة
    logo_upload = forms.ImageField(required=False, label="رفع اللوجو", widget=forms.FileInput(attrs={'class': 'block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-teal-50 file:text-teal-700 hover:file:bg-teal-100'}))
    # No longer editable from the settings UI at all — it's now purely carried through as
    # a hidden field so the store's existing value keeps round-tripping on every save
    # (products' add-item fields, etc. still key off SystemSetting.market_type elsewhere
    # in the app; only the ability to change it from here is removed). The
    # is_market_type_locked/CHANGE_STORE_TYPE-token mechanism below is unaffected.
    market_type = forms.ChoiceField(
        choices=MARKET_TYPE_CHOICES,
        required=False,
        label="نوع المتجر / السوق",
        widget=forms.HiddenInput()
    )

    # Explicitly define printer_name to use a ChoiceField (Dropdown)
    # We initialize choices as empty list, then populate in __init__
    printer_name = forms.ChoiceField(
        choices=[], 
        required=False, 
        label="الطابعة الأساسية", 
        widget=forms.Select(attrs={'class': TAILWIND_SELECT})
    )

    recipient_1 = forms.EmailField(required=False, label="بريد المستلم 1", widget=forms.EmailInput(attrs={'class': TAILWIND_INPUT}))
    recipient_2 = forms.EmailField(required=False, label="بريد المستلم 2", widget=forms.EmailInput(attrs={'class': TAILWIND_INPUT}))
    recipient_3 = forms.EmailField(required=False, label="بريد المستلم 3", widget=forms.EmailInput(attrs={'class': TAILWIND_INPUT}))
    recipient_4 = forms.EmailField(required=False, label="بريد المستلم 4", widget=forms.EmailInput(attrs={'class': TAILWIND_INPUT}))
    recipient_5 = forms.EmailField(required=False, label="بريد المستلم 5", widget=forms.EmailInput(attrs={'class': TAILWIND_INPUT}))

    class Meta:
        model = SystemSetting
        fields = [
            'shop_name', 'address', 'phone', 'market_type', 'ui_color_theme', 'return_policy', 'thank_you_text',
            'show_qr', 'qr_link', 'printer_name', 'notification_sound',
            'gmail_sender_email', 'gmail_app_password', 'email_recipients'
        ]
        widgets = {
            'ui_color_theme': forms.Select(attrs={'class': TAILWIND_SELECT, 'id': 'id_ui_color_theme'}),
            'shop_name': forms.TextInput(attrs={'class': TAILWIND_INPUT}),
            'address': forms.Textarea(attrs={'class': TAILWIND_INPUT, 'rows': 2}),
            'phone': forms.TextInput(attrs={'class': TAILWIND_INPUT}),
            'return_policy': forms.Textarea(attrs={'class': TAILWIND_INPUT, 'rows': 2}),
            'thank_you_text': forms.TextInput(attrs={'class': TAILWIND_INPUT}),
            'show_qr': forms.CheckboxInput(attrs={'class': TAILWIND_CHECKBOX}),
            'qr_link': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'أدخل الرابط هنا (مثال: صفحة الفيسبوك)'}),
            'notification_sound': forms.FileInput(attrs={'class': 'block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-teal-50 file:text-teal-700 hover:file:bg-teal-100', 'accept': 'audio/mpeg,audio/wav'}),
            'gmail_sender_email': forms.EmailInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'example@gmail.com'}),
            'gmail_app_password': forms.PasswordInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'xxxx xxxx xxxx xxxx', 'autocomplete': 'new-password'}, render_value=True),
            'email_recipients': forms.HiddenInput(),
        }

    def __init__(self, *args, **kwargs):
        # Extract 'printer_choices' if passed from the view
        printer_choices = kwargs.pop('printer_choices', [])
        super(SystemSettingForm, self).__init__(*args, **kwargs)
        
        # Populate the printer selection dropdown
        # Ensure we have a default empty option if needed, though get_available_printers usually handles it
        if printer_choices:
            self.fields['printer_name'].choices = printer_choices
        else:
            self.fields['printer_name'].choices = [('', 'لا توجد طابعات متاحة')]
            
        # IMPORTANT: Set initial value if instance has one
        if self.instance and self.instance.printer_name:
             self.fields['printer_name'].initial = self.instance.printer_name

        # Phase ③: once the market type is locked (set at onboarding / by dev token), the
        # customer can no longer change it from settings — only a CHANGE_STORE_TYPE token can.
        if self.instance and getattr(self.instance, 'is_market_type_locked', False):
            f = self.fields['market_type']
            f.disabled = True
            f.required = False
            f.help_text = "نوع المتجر مقفل — لا يمكن تغييره إلا عبر رمز تفعيل من المطوّر."

        recipients = []
        if self.instance and self.instance.email_recipients:
            recipients = [r.strip() for r in self.instance.email_recipients.split(',') if r.strip()]
        for i in range(1, 6):
            self.fields[f'recipient_{i}'].initial = recipients[i - 1] if len(recipients) >= i else ''