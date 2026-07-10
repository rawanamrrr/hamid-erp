from django import forms
from .models import Customer

TAILWIND_INPUT_CLASS = 'w-full px-4 py-2 mt-2 text-gray-700 bg-white border border-gray-300 rounded-md focus:border-teal-500 focus:outline-none focus:ring focus:ring-teal-300 focus:ring-opacity-40'

class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['first_name', 'last_name', 'phone', 'phone2', 'tax_number', 'is_blacklisted',
                  'address', 'customer_type', 'opening_balance', 'credit_limit']
        widgets = {
            'first_name': forms.TextInput(attrs={'class': TAILWIND_INPUT_CLASS, 'placeholder': 'الاسم الأول'}),
            'last_name': forms.TextInput(attrs={'class': TAILWIND_INPUT_CLASS, 'placeholder': 'الاسم الثاني أو العائلة'}),
            'phone': forms.TextInput(attrs={'class': TAILWIND_INPUT_CLASS, 'placeholder': 'رقم الهاتف'}),
            'phone2': forms.TextInput(attrs={'class': TAILWIND_INPUT_CLASS, 'placeholder': 'رقم إضافي (اختياري)'}),
            'tax_number': forms.TextInput(attrs={'class': TAILWIND_INPUT_CLASS, 'placeholder': 'الرقم الضريبي (للشركات)'}),
            'is_blacklisted': forms.CheckboxInput(attrs={'class': 'w-5 h-5 text-red-600 rounded border-gray-300'}),
            'address': forms.Textarea(attrs={'class': TAILWIND_INPUT_CLASS, 'rows': 3, 'placeholder': 'العنوان (اختياري)'}),
            'customer_type': forms.Select(attrs={'class': TAILWIND_INPUT_CLASS}),
            'opening_balance': forms.NumberInput(attrs={'class': TAILWIND_INPUT_CLASS, 'step': '0.01', 'placeholder': '0.00'}),
            'credit_limit': forms.NumberInput(attrs={'class': TAILWIND_INPUT_CLASS, 'step': '0.01', 'placeholder': '0 = غير محدود', 'min': '0'}),
        }