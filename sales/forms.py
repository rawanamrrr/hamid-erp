from django import forms
from .models import Expense, ReturnInvoice, OtherIncome, CashSettlement

class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['title', 'category', 'amount', 'date', 'payment_method', 'description', 'receipt']
        widgets = {
            'title': forms.TextInput(attrs={'class': 'w-full p-2 border rounded', 'placeholder': 'اسم المصروف'}),
            'category': forms.Select(attrs={'class': 'w-full p-2 border rounded', 'onchange': 'handleOtherCategory(this)'}),
            'amount': forms.NumberInput(attrs={'class': 'w-full p-2 border rounded', 'placeholder': '0.00'}),
            'date': forms.DateInput(attrs={'class': 'w-full p-2 border rounded', 'type': 'date'}),
            'payment_method': forms.Select(attrs={'class': 'w-full p-2 border rounded'}),
            'description': forms.Textarea(attrs={'class': 'w-full p-2 border rounded', 'rows': 2, 'placeholder': 'تفاصيل إضافية...'}),
            'receipt': forms.FileInput(attrs={'class': 'w-full p-2 border rounded', 'accept': 'image/*'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # "سلفة موظفين" and "رواتب موظفين" are only ever auto-logged — by
        # financial.advance_create and financial._pay_payslip respectively (see
        # Expense.EXPENSE_CATEGORIES) — never categories a cashier picks by hand for a
        # manual expense entry. Editing an existing auto-logged row keeps whatever
        # category it already has (ModelForm validates the bound instance's current
        # value even if it's missing from `choices`), so this only ever hides them from
        # someone creating/re-picking.
        auto_only_categories = {'advance', 'salary'}
        current = self.instance.category
        self.fields['category'].choices = [
            c for c in self.fields['category'].choices
            if c[0] not in auto_only_categories or c[0] == current
        ]

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