from django import forms
from .models import DailyShift, Transaction, Account


class StartShiftForm(forms.ModelForm):
    class Meta:
        model = DailyShift
        fields = ['start_balance', 'opening_notes']
        widgets = {
            'start_balance': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'}),
            'opening_notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class CloseShiftForm(forms.ModelForm):
    class Meta:
        model = DailyShift
        fields = ['actual_closing_balance', 'notes']
        widgets = {
            'actual_closing_balance': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': 'any',
                'placeholder': 'أدخل المبلغ الموجود فعلياً في الدرج'
            }),
            'notes': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'أي ملاحظات عن العجز أو الزيادة...'
            }),
        }


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ['account', 'transaction_type', 'amount', 'description', 'to_account']
        widgets = {
            'account': forms.Select(attrs={'class': 'form-select'}),
            'transaction_type': forms.Select(attrs={'class': 'form-select', 'id': 'trans_type'}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any', 'min': '0.01'}),
            'description': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'وصف العملية...'}),
            'to_account': forms.Select(attrs={'class': 'form-select', 'id': 'to_account_field'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Two guards on the same underlying problem: (1) never offer a dead coded
        # duplicate account, and (2) manual expense/income/withdrawal/transfer entry
        # should only ever touch real cash/bank/wallet accounts — not the nominal
        # chart-of-accounts rows (الأصول/الإيرادات/المصروفات/COGS/AR/AP/...), which
        # exist purely for the internal double-entry journal (see financial/posting.py)
        # and would silently corrupt those balances if hand-picked here.
        cash_like_accounts = Account.exclude_dead_duplicates(
            Account.objects.filter(
                is_active=True,
                account_type__in=['CASH_DRAWER', 'SAFE', 'BANK', 'VODAFONE_CASH', 'INSTAPAY'],
            )
        )
        self.fields['account'].queryset = cash_like_accounts
        self.fields['to_account'].queryset = cash_like_accounts

    def clean(self):
        cleaned_data = super().clean()
        t_type = cleaned_data.get("transaction_type")
        to_acc = cleaned_data.get("to_account")
        account = cleaned_data.get("account")
        amount = cleaned_data.get("amount")

        if t_type == 'TRANSFER' and not to_acc:
            raise forms.ValidationError("يجب اختيار الحساب المحول إليه في حالة التحويل.")

        if t_type == 'TRANSFER' and account and to_acc and account == to_acc:
            raise forms.ValidationError("لا يمكن التحويل من حساب لنفسه!")

        if account and amount:
            # FIX: Include SUPPLIER_PAYMENT in balance check
            if t_type in ['EXPENSE', 'WITHDRAWAL', 'TRANSFER', 'SUPPLIER_PAYMENT', 'REFUND']:
                if account.balance < amount:
                    raise forms.ValidationError(
                        f"رصيد الحساب ({account.balance} ج.م) لا يكفي لإتمام هذه العملية ({amount} ج.م)."
                    )

        if amount and amount <= 0:
            raise forms.ValidationError("يجب أن يكون المبلغ أكبر من صفر.")

        return cleaned_data


class AccountForm(forms.ModelForm):
    """Form for creating/editing financial accounts"""
    class Meta:
        model = Account
        fields = ['name', 'account_type', 'balance', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اسم الحساب'}),
            'account_type': forms.Select(attrs={'class': 'form-select'}),
            'balance': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any', 'placeholder': '0.00'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'اسم الحساب',
            'account_type': 'نوع الحساب',
            'balance': 'الرصيد الابتدائي',
            'is_active': 'نشط',
        }
        help_texts = {
            'balance': 'يُستخدم عند إنشاء الحساب فقط. لاحقاً تتغير الأرصدة عبر المعاملات.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # The help text already said "creation only" but nothing enforced it —
            # editing an existing account let anyone silently overwrite its real,
            # ledger-derived balance with zero Transaction/audit trail. `disabled`
            # both greys out the field and makes Django ignore any submitted value,
            # always keeping the DB's current balance.
            self.fields['balance'].disabled = True
            self.fields['balance'].help_text = 'لا يمكن تعديل الرصيد مباشرة — يتغير فقط عبر المعاملات المالية (سحب/إيداع/تحويل).'


class EmployeeSalaryForm(forms.ModelForm):
    class Meta:
        from .payroll_models import EmployeeSalary
        model = EmployeeSalary
        fields = ['employee', 'basic_salary', 'allowances', 'deductions', 'notes']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-select'}),
            'basic_salary': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'}),
            'allowances': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'}),
            'deductions': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }


class DealDiscountForm(forms.ModelForm):
    class Meta:
        from .payroll_models import DealDiscount
        model = DealDiscount
        fields = ['name', 'promo_type', 'discount_type', 'value', 'buy_x_qty', 'get_y_qty', 'buy_n_qty', 'for_price', 'minimum_order_value', 'start_date', 'end_date', 'coupon_code', 'apply_to_all', 'categories', 'products', 'get_products', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'promo_type': forms.Select(attrs={'class': 'form-select', 'id': 'id_promo_type'}),
            'discount_type': forms.Select(attrs={'class': 'form-select', 'id': 'id_discount_type'}),
            'value': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any', 'id': 'id_value'}),
            'buy_x_qty': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'get_y_qty': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'buy_n_qty': forms.NumberInput(attrs={'class': 'form-control', 'min': '0'}),
            'for_price': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'}),
            'minimum_order_value': forms.NumberInput(attrs={'class': 'form-control', 'step': 'any'}),
            'start_date': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'end_date': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'coupon_code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'اختياري'}),
            'apply_to_all': forms.CheckboxInput(attrs={'class': 'form-check-input', 'id': 'id_apply_to_all'}),
            'categories': forms.SelectMultiple(attrs={'class': 'form-select', 'rows': 5, 'id': 'id_categories'}),
            'products': forms.SelectMultiple(attrs={'class': 'form-select', 'rows': 5, 'id': 'id_products'}),
            'get_products': forms.SelectMultiple(attrs={'class': 'form-select', 'rows': 5, 'id': 'id_get_products'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from products.models import Product

        # Menu items only — never raw materials (they're never sold directly, so they
        # can't be part of a customer-facing offer). Not filtered by stock: cafe/recipe
        # menu items are made to order and never carry a real stock count, so a
        # total_stock>0 filter used to hide almost every menu product from this picker.
        available_products = Product.objects.filter(
            is_active=True, is_raw_material=False,
        ).order_by('name')

        self.fields['products'].queryset = available_products
        self.fields['get_products'].queryset = available_products

        from products.models import Category
        self.fields['categories'].queryset = Category.objects.filter(is_active=True).order_by('name')
        self.fields['categories'].required = False
        self.fields['products'].required = False

        # 'value' is only meaningful for promo_type='discount' — its input is hidden by
        # JS for buy_x_get_y/buy_n_for_price, so leaving it required=True made those two
        # promo types silently fail validation (the "قيمة الخصم" error rendered inside a
        # div the JS keeps hidden, so nothing ever appeared to explain why nothing saved).
        self.fields['value'].required = False

    def clean(self):
        from decimal import Decimal
        cleaned = super().clean()
        promo_type = cleaned.get('promo_type')
        if promo_type == 'discount':
            if not cleaned.get('value'):
                self.add_error('value', 'قيمة الخصم مطلوبة لهذا النوع من العروض.')
        else:
            # Not used by this promo type, and the model column has no default — fill it
            # in so _post_clean()'s model.full_clean() doesn't reject a blank 'value'.
            cleaned['value'] = Decimal('0.00')
        if promo_type == 'buy_x_get_y':
            if not cleaned.get('buy_x_qty'):
                self.add_error('buy_x_qty', 'يجب تحديد كمية الشراء (X).')
            if not cleaned.get('get_y_qty'):
                self.add_error('get_y_qty', 'يجب تحديد الكمية المجانية (Y).')
        elif promo_type == 'buy_n_for_price':
            if not cleaned.get('buy_n_qty'):
                self.add_error('buy_n_qty', 'يجب تحديد كمية العرض (N).')
            if not cleaned.get('for_price'):
                self.add_error('for_price', 'يجب تحديد السعر الإجمالي للعرض.')
        return cleaned