from django import forms
from .models import (
    Product, Category, Kind, Size, UnitOfMeasure,
    Supplier, StockTransaction, Warehouse, WarehouseStock,
    PurchaseOrder, PurchaseOrderItem,
)

TW_INPUT = 'w-full px-4 py-2 mt-1 text-gray-700 bg-white border border-gray-300 rounded-lg focus:border-teal-500 focus:outline-none focus:ring-2 focus:ring-teal-200'
TW_SELECT = 'w-full px-4 py-2 mt-1 text-gray-700 bg-white border border-gray-300 rounded-lg focus:border-teal-500 focus:outline-none focus:ring-2 focus:ring-teal-200'
TW_CHECK = 'w-5 h-5 text-teal-600 border-gray-300 rounded focus:ring-teal-500'
TW_TEXTAREA = 'w-full px-4 py-2 mt-1 text-gray-700 bg-white border border-gray-300 rounded-lg focus:border-teal-500 focus:outline-none focus:ring-2 focus:ring-teal-200'

# Keep old aliases for backward compat
TAILWIND_INPUT_CLASS = TW_INPUT
TAILWIND_SELECT_CLASS = TW_SELECT
TAILWIND_CHECKBOX_CLASS = TW_CHECK


class RawMaterialForm(forms.ModelForm):
    """Lean form for ingredients (milk, sugar, coffee beans...) — deliberately just the
    handful of fields a raw material actually needs, instead of ProductForm's full
    retail-product field set (color/season/serials/variants... all irrelevant here).
    """
    sku = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': TW_INPUT,
            'placeholder': 'كود المادة (اتركه فارغاً للتوليد التلقائي)',
        })
    )
    # Optional here — the real cost is usually set from the purchase invoice when the
    # material is actually bought; this just lets a manager set/override it up front.
    cost_price = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': TW_INPUT, 'step': '0.01'})
    )

    class Meta:
        model = Product
        fields = ['name', 'sku', 'unit_measure', 'cost_price', 'low_stock_threshold', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'مثال: حليب، سكر، بن'}),
            'unit_measure': forms.Select(attrs={'class': TW_SELECT}),
            'low_stock_threshold': forms.NumberInput(attrs={'class': TW_INPUT, 'step': '0.01'}),
            'is_active': forms.CheckboxInput(attrs={'class': TW_CHECK}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Same as ProductForm — without this, custom units added on وحدات القياس
        # (products/units/) never show up here, only the hardcoded UNIT_CHOICES list.
        self.fields['unit_measure'].choices = Product.get_combined_unit_choices()


class ProductForm(forms.ModelForm):
    sku = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': TW_INPUT,
            'placeholder': 'كود المنتج (اتركه فارغاً للتوليد التلقائي)',
            'id': 'id_sku'
        })
    )
    barcode = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': TW_INPUT,
            'placeholder': 'يُولَّد تلقائياً من SKU',
            'id': 'id_barcode',
            'readonly': 'readonly'
        })
    )
    # Not required at the form level: a product sold in multiple sizes has no single
    # "سعر البيع" (each size carries its own full price via ProductVariant) — the view
    # backfills a sane value (lowest size price) after save so reports/dashboard code
    # that reads price_retail directly still gets a real number.
    price_retail = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': TW_INPUT, 'id': 'id_price_retail', 'step': '0.01'}),
    )

    class Meta:
        model = Product
        fields = [
            'name', 'sku', 'barcode', 'egs_code', 'category', 'kind',
            'material', 'pattern', 'color', 'season',
            'cost_price', 'price_retail', 'price_semi_wholesale', 'price_wholesale',
            'unit_measure', 'low_stock_threshold', 'pieces_per_package', 'is_active',
            'supplier', 'packaging_type', 'strips_per_box', 'scientific_name',
            # Grocery fields
            'is_weighted', 'has_variants', 'variant_parent', 'variant_name',
            'net_weight', 'weight_unit', 'shelf_life_days', 'requires_refrigeration',
            # Electronics fields
            'brand', 'model_number', 'serial_number', 'warranty_months', 'is_serialized', 'is_refurbished', 'specifications',
            # Cafe fields
            'calories', 'allergens', 'serve_temperature', 'track_stock_no_recipe',
            # Multi-Unit fields
            'has_sub_unit', 'sub_unit', 'sub_units_per_main_unit', 'sub_unit_price',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'أدخل اسم المنتج'}),
            'category': forms.Select(attrs={'class': TW_SELECT, 'id': 'id_category'}),
            'kind': forms.Select(attrs={'class': TW_SELECT, 'id': 'id_kind'}),
            'supplier': forms.Select(attrs={'class': TW_SELECT}),
            'material': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'مثال: قطن'}),
            'pattern': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'مثال: كاروهات'}),
            'color': forms.TextInput(attrs={'class': TW_INPUT}),
            'season': forms.Select(attrs={'class': TW_SELECT}),
            'cost_price': forms.NumberInput(attrs={'class': TW_INPUT, 'id': 'id_cost_price', 'step': '0.01', 'placeholder': '0.00'}),
            'price_retail': forms.NumberInput(attrs={'class': TW_INPUT, 'id': 'id_price_retail', 'step': '0.01'}),
            'price_semi_wholesale': forms.NumberInput(attrs={'class': TW_INPUT, 'step': '0.01'}),
            'price_wholesale': forms.NumberInput(attrs={'class': TW_INPUT, 'step': '0.01'}),
            'unit_measure': forms.Select(attrs={'class': TW_SELECT}),
            'low_stock_threshold': forms.NumberInput(attrs={'class': TW_INPUT, 'step': '0.01'}),
            'pieces_per_package': forms.NumberInput(attrs={'class': TW_INPUT, 'min': '1'}),
            'is_active': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'packaging_type': forms.Select(attrs={'class': TW_SELECT, 'id': 'id_packaging_type'}),
            'strips_per_box': forms.NumberInput(attrs={'class': TW_INPUT, 'id': 'id_strips_per_box', 'min': '1'}),
            'scientific_name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'الاسم العلمي للمنتج'}),
            # Grocery widgets
            'is_weighted': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'has_variants': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'variant_parent': forms.Select(attrs={'class': TW_SELECT}),
            'variant_name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'مثال: 1 لتر أو كبير'}),
            'net_weight': forms.NumberInput(attrs={'class': TW_INPUT, 'step': '0.001', 'placeholder': '0.000'}),
            'weight_unit': forms.Select(attrs={'class': TW_SELECT}),
            'shelf_life_days': forms.NumberInput(attrs={'class': TW_INPUT, 'min': '0', 'placeholder': 'عدد الأيام'}),
            'requires_refrigeration': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            # Electronics widgets
            'brand': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'مثال: Apple, Samsung'}),
            'model_number': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'مثال: iPhone 15'}),
            'serial_number': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'الرقم التسلسلي'}),
            'warranty_months': forms.NumberInput(attrs={'class': TW_INPUT, 'min': '0', 'placeholder': 'مدة الضمان'}),
            'is_refurbished': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'specifications': forms.Textarea(attrs={'class': TW_TEXTAREA, 'rows': '4', 'placeholder': 'المواصفات الفنية...'}),
            # Cafe widgets
            'calories': forms.NumberInput(attrs={'class': TW_INPUT, 'min': '0', 'placeholder': 'مثال: 250'}),
            'allergens': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'مثال: يحتوي على مكسرات، ألبان'}),
            'serve_temperature': forms.Select(attrs={'class': TW_SELECT}),
            'track_stock_no_recipe': forms.CheckboxInput(attrs={'class': TW_CHECK, 'id': 'id_track_stock_no_recipe'}),
            # Multi-Unit widgets
            'has_sub_unit': forms.CheckboxInput(attrs={'class': TW_CHECK, 'id': 'id_has_sub_unit'}),
            'sub_unit': forms.Select(attrs={'class': TW_SELECT, 'id': 'id_sub_unit'}),
            'sub_units_per_main_unit': forms.NumberInput(attrs={'class': TW_INPUT, 'id': 'id_sub_units_per_main_unit', 'step': '1', 'min': '1'}),
            'sub_unit_price': forms.NumberInput(attrs={'class': TW_INPUT, 'id': 'id_sub_unit_price', 'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Dynamically load the combined unit choices (default choices + database custom choices)
        self.fields['unit_measure'].choices = Product.get_combined_unit_choices()
        # Make wholesale prices optional in form (helps pharmacy store type)
        self.fields['price_semi_wholesale'].required = False
        self.fields['price_wholesale'].required = False
        # Explicitly remove 'required' attribute for HTML5 validation
        self.fields['price_semi_wholesale'].widget.attrs.pop('required', None)
        self.fields['price_wholesale'].widget.attrs.pop('required', None)
        # cost_price is optional — auto-calculated from purchase invoice batches
        self.fields['cost_price'].required = False
        self.fields['cost_price'].widget.attrs.pop('required', None)
        
        # Make variant parent optional
        self.fields['variant_parent'].required = False
        self.fields['variant_parent'].queryset = Product.objects.filter(has_variants=True, is_active=True)
        
        # Make grocery fields optional
        for field in ['variant_name', 'net_weight', 'weight_unit', 'shelf_life_days']:
            self.fields[field].required = False
            
        # Make clothes fields optional
        for field in ['material', 'pattern', 'color', 'season']:
            if field in self.fields:
                self.fields[field].required = False
            
        # Make electronics fields optional
        for field in ['brand', 'model_number', 'serial_number', 'warranty_months', 'is_refurbished', 'specifications']:
            self.fields[field].required = False

        # Make cafe fields optional
        for field in ['calories', 'allergens', 'serve_temperature']:
            self.fields[field].required = False

        # Make cost_price read-only if active batches/invoices exist
        if self.instance and self.instance.pk:
            if hasattr(self.instance, 'batches') and self.instance.batches.exists():
                self.fields['cost_price'].widget.attrs['readonly'] = 'readonly'
                self.fields['cost_price'].widget.attrs['class'] = TW_INPUT + ' bg-gray-100 text-gray-500 cursor-not-allowed'
                self.fields['cost_price'].help_text = 'محسوب تلقائياً من فواتير الشراء الفعلية (غير قابل للتعديل اليدوي)'

    def save(self, commit=True):
        from decimal import Decimal
        instance = super().save(commit=False)
        # NOTE: wholesale/semi-wholesale prices are intentionally left as entered (0/blank
        # if the product doesn't sell at those tiers) — do NOT auto-copy retail into them.
        # A product with no real wholesale price should stay without one; POS already hides
        # the جملة/نص جملة tier buttons when they're unset (see pos.html), and price_retail
        # is the safe fallback at the point of sale (see products.pricing.tier_price).
        # These fields are required=False, so a blank input lands here as Python None,
        # which would violate the NOT NULL column — coerce to a real 0, not retail.
        if instance.price_semi_wholesale is None:
            instance.price_semi_wholesale = Decimal('0.00')
        if instance.price_wholesale is None:
            instance.price_wholesale = Decimal('0.00')
        # Default cost_price to 0.00 if not entered — it will be calculated from batches
        if not instance.cost_price:
            instance.cost_price = Decimal('0.00')
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'description', 'is_active', 'is_menu_category']
        widgets = {
            'name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'اسم القسم'}),
            'description': forms.Textarea(attrs={'class': TW_TEXTAREA, 'rows': 3, 'placeholder': 'وصف مختصر للقسم'}),
            'is_active': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'is_menu_category': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'packaging_type': forms.Select(attrs={'class': TW_SELECT, 'id': 'id_packaging_type'}),
        }


class KindForm(forms.ModelForm):
    class Meta:
        model = Kind
        fields = ['category', 'name', 'is_active']
        widgets = {
            'category': forms.Select(attrs={'class': TW_SELECT}),
            'name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'اسم النوع'}),
            'is_active': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'packaging_type': forms.Select(attrs={'class': TW_SELECT, 'id': 'id_packaging_type'}),
        }


class SizeForm(forms.ModelForm):
    class Meta:
        model = Size
        fields = ['name', 'size_type', 'sort_order', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'مثال: XL أو 42'}),
            'size_type': forms.Select(attrs={'class': TW_SELECT}),
            'sort_order': forms.NumberInput(attrs={'class': TW_INPUT, 'min': '0'}),
            'is_active': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'packaging_type': forms.Select(attrs={'class': TW_SELECT, 'id': 'id_packaging_type'}),
        }


class UnitOfMeasureForm(forms.ModelForm):
    class Meta:
        model = UnitOfMeasure
        fields = ['name', 'abbreviation', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'اسم الوحدة (مثال: متر)'}),
            'abbreviation': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'اختصار (مثال: MTR)'}),
            'is_active': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'packaging_type': forms.Select(attrs={'class': TW_SELECT, 'id': 'id_packaging_type'}),
        }


class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = [
            'name', 'contact_name', 'phone', 'email', 'address',
            'supplier_type', 'opening_balance', 'credit_limit', 'notes', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'اسم الشركة أو المورد'}),
            'contact_name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'اسم الشخص المسؤول'}),
            'phone': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'رقم الهاتف'}),
            'email': forms.EmailInput(attrs={'class': TW_INPUT, 'placeholder': 'example@email.com'}),
            'address': forms.Textarea(attrs={'class': TW_TEXTAREA, 'rows': 2, 'placeholder': 'العنوان بالتفصيل'}),
            'supplier_type': forms.Select(attrs={'class': TW_SELECT}),
            'opening_balance': forms.NumberInput(attrs={'class': TW_INPUT, 'step': '0.01', 'min': '0', 'placeholder': '0.00'}),
            'credit_limit': forms.NumberInput(attrs={'class': TW_INPUT, 'step': '0.01', 'min': '0', 'placeholder': '0.00'}),
            'notes': forms.Textarea(attrs={'class': TW_TEXTAREA, 'rows': 2, 'placeholder': 'ملاحظات إضافية...'}),
            'is_active': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'packaging_type': forms.Select(attrs={'class': TW_SELECT, 'id': 'id_packaging_type'}),
        }


class StockTransactionForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['warehouse'].empty_label = "-- اختر المخزن --"
        self.fields['warehouse'].required = True

    class Meta:
        model = StockTransaction
        fields = ['reference_number', 'warehouse', 'product', 'transaction_type', 'quantity', 'note']
        widgets = {
            'reference_number': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'رقم الفاتورة (اختياري)'}),
            'warehouse': forms.Select(attrs={'class': TW_SELECT}),
            'product': forms.Select(attrs={'class': TW_SELECT}),
            'transaction_type': forms.Select(attrs={'class': TW_SELECT}),
            'quantity': forms.NumberInput(attrs={'class': TW_INPUT, 'placeholder': 'الكمية'}),
            'note': forms.Textarea(attrs={'class': TW_TEXTAREA, 'rows': 2, 'placeholder': 'ملاحظات...'}),
        }


class WarehouseForm(forms.ModelForm):
    class Meta:
        model = Warehouse
        fields = ['name', 'address', 'is_active', 'is_sales_point', 'warehouse_type']
        widgets = {
            'name': forms.TextInput(attrs={'class': TW_INPUT, 'placeholder': 'اسم المخزن'}),
            'address': forms.Textarea(attrs={'class': TW_TEXTAREA, 'rows': 2, 'placeholder': 'عنوان المخزن'}),
            'is_active': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'is_sales_point': forms.CheckboxInput(attrs={'class': TW_CHECK}),
            'warehouse_type': forms.Select(attrs={'class': TW_SELECT}),
        }


class StockTransferForm(forms.Form):
    from_warehouse = forms.ModelChoiceField(
        queryset=Warehouse.objects.filter(is_active=True),
        label="من مخزن",
        widget=forms.Select(attrs={'class': TW_SELECT})
    )
    to_warehouse = forms.ModelChoiceField(
        queryset=Warehouse.objects.filter(is_active=True),
        label="إلى مخزن",
        widget=forms.Select(attrs={'class': TW_SELECT})
    )
    product = forms.ModelChoiceField(
        queryset=Product.objects.filter(is_active=True),
        label="المنتج",
        widget=forms.Select(attrs={'class': TW_SELECT})
    )
    quantity = forms.DecimalField(
        label="الكمية",
        widget=forms.NumberInput(attrs={'class': TW_INPUT})
    )
    note = forms.CharField(
        label="ملاحظات",
        required=False,
        widget=forms.Textarea(attrs={'class': TW_TEXTAREA, 'rows': 2})
    )

    def clean(self):
        cleaned_data = super().clean()
        from_wh = cleaned_data.get('from_warehouse')
        to_wh = cleaned_data.get('to_warehouse')
        product = cleaned_data.get('product')
        qty = cleaned_data.get('quantity')

        if from_wh == to_wh:
            raise forms.ValidationError("لا يمكن التحويل لنفس المخزن")

        if from_wh and product and qty:
            stock = WarehouseStock.objects.filter(warehouse=from_wh, product=product).first()
            if not stock or stock.quantity < qty:
                raise forms.ValidationError(
                    f"الرصيد غير كافي في {from_wh.name}. المتاح: {stock.quantity if stock else 0}"
                )


class PurchaseOrderForm(forms.ModelForm):
    class Meta:
        model = PurchaseOrder
        fields = ['supplier', 'destination_warehouse', 'expected_date', 'notes']
        widgets = {
            'supplier': forms.Select(attrs={'class': TW_SELECT}),
            'destination_warehouse': forms.Select(attrs={'class': TW_SELECT}),
            'expected_date': forms.DateInput(attrs={'class': TW_INPUT, 'type': 'date'}),
            'notes': forms.Textarea(attrs={'class': TW_TEXTAREA, 'rows': 2, 'placeholder': 'ملاحظات...'}),
        }