from django import forms
from .models import Expense, ReturnInvoice, OtherIncome, CashSettlement

class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['title', 'category', 'amount', 'date', 'payment_method', 'description', 'receipt']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'w-full p-2 border rounded', 'placeholder': 'اسم المصروف'}),
            'category': forms.Select(attrs={'class': 'w-full p-2 border rounded'}),
            'amount': forms.NumberInput(attrs={'class': 'w-full p-2 border rounded', 'placeholder': '0.00'}),
            'date': forms.DateInput(attrs={'class': 'w-full p-2 border rounded', 'type': 'date'}),
            'payment_method': forms.Select(attrs={'class': 'w-full p-2 border rounded'}),
            'description': forms.Textarea(attrs={'class': 'w-full p-2 border rounded', 'rows': 2, 'placeholder': 'تفاصيل إضافية...'}),
            'receipt': forms.FileInput(attrs={'class': 'w-full p-2 border rounded', 'accept': 'image/*'}),
        }

class OtherIncomeForm(forms.ModelForm):
    class Meta:
        model = OtherIncome
        fields = ['title', 'amount', 'description']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'w-full p-2 border rounded', 'placeholder': 'مصدر الأموال (مثال: سداد سلفة)'}),
            'amount': forms.NumberInput(attrs={'class': 'w-full p-2 border rounded', 'placeholder': '0.00'}),
            'description': forms.Textarea(attrs={'class': 'w-full p-2 border rounded', 'rows': 2, 'placeholder': 'تفاصيل...'}),
        }

class CashSettlementForm(forms.ModelForm):
    class Meta:
        model = CashSettlement
        fields = ['actual_cash', 'note']
        widgets = {
            'actual_cash': forms.NumberInput(attrs={'class': 'w-full p-2 border rounded font-bold text-lg', 'placeholder': 'المبلغ الموجود فعلياً'}),
            'note': forms.Textarea(attrs={'class': 'w-full p-2 border rounded', 'rows': 2, 'placeholder': 'ملاحظات حول العجز أو الزيادة...'}),
        }