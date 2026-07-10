from django import forms
from .models import ShippingCompany, Shipment
from crm.models import Customer
from sales.models import Order

TAILWIND_INPUT = 'w-full px-4 py-2 mt-2 text-gray-700 bg-white border border-gray-300 rounded-md focus:border-teal-500 focus:outline-none focus:ring focus:ring-teal-300 focus:ring-opacity-40'
TAILWIND_SELECT = 'w-full px-4 py-2 mt-2 text-gray-700 bg-white border border-gray-300 rounded-md focus:border-teal-500 focus:outline-none focus:ring focus:ring-teal-300 focus:ring-opacity-40'

class ShippingCompanyForm(forms.ModelForm):
    class Meta:
        model = ShippingCompany
        fields = ['name', 'phone', 'contact_person', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'اسم الشركة'}),
            'phone': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'رقم الهاتف'}),
            'contact_person': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'المندوب'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'w-5 h-5 text-teal-600 rounded'}),
        }

class ShipmentForm(forms.ModelForm):
    class Meta:
        model = Shipment
        fields = ['shipping_company', 'tracking_number', 'shipping_address', 'delivery_type']
        widgets = {
            'shipping_company': forms.Select(attrs={'class': TAILWIND_SELECT}),
            'delivery_type': forms.Select(attrs={'class': TAILWIND_SELECT, 'id': 'delivery_type'}),
            'shipping_address': forms.Textarea(attrs={'class': TAILWIND_INPUT, 'rows': 3}),
            'tracking_number': forms.TextInput(attrs={'class': TAILWIND_INPUT, 'placeholder': 'اختياري في البداية'}),
        }
    
    def __init__(self, *args, **kwargs):
        super(ShipmentForm, self).__init__(*args, **kwargs)
        # Make tracking number optional
        self.fields['tracking_number'].required = False
        self.fields['shipping_company'].required = False

class CustomerAddressForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['address', 'phone']
        widgets = {
            'address': forms.Textarea(attrs={'class': TAILWIND_INPUT, 'rows': 3, 'placeholder': 'أدخل العنوان التفصيلي...'}),
            'phone': forms.TextInput(attrs={'class': TAILWIND_INPUT}),
        }