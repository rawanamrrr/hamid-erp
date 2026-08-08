"""System Policy engine (Layer 2) — the customer-configurable "ثوابت النظام" constants.

This is the middle layer of the 3-layer config model:
- Layer 1 = MarketProfile (settings/market_profiles.py): what a market exposes at all.
- Layer 2 = THIS: store-wide behavior toggles the master/customer CAN change (preferences).
- Layer 3 = Feature entitlements (licensing/features.py): dev-locked paid capabilities.

Policies are NOT per-user permissions (those live on Role/UserProfile) and NOT dev-locked
features. They are store-wide behavior switches — the SKY SOFT "system constants" page.

`POLICY_REGISTRY` is the single source of truth. Stored values live in the SystemPolicy
singleton (settings.models). `get_policy(key)` returns the stored value or the registry
default; `resolved_policies()` returns the full resolved map for templates/context.

To add a policy: add one registry entry. To enforce it: call get_policy('key') at the
decision point (or read `policies.key` in a template). Adding an entry never changes behavior
until something reads it — defaults are chosen to preserve current behavior.
"""

# Tab groups (mirror the SKY SOFT constants tabs). key -> Arabic label.
POLICY_GROUPS = {
    'sales':     'المبيعات',
    'cashier':   'نقطة البيع (الكاشير)',
    'tax':       'الضريبة ورسوم الخدمة',
    'inventory': 'المخزون',
    'purchases': 'المشتريات',
    'customers': 'العملاء',
    'receipts':  'الإيصالات والفواتير',
    'shifts':    'الورديات والخزنة',
    'payroll':   'الرواتب',
    'kitchen':   'شاشة المطبخ',
}

# Each policy: key -> dict(group, label, help, type, default, [choices]).
# type: 'bool' | 'int' | 'decimal' | 'choice'
POLICY_REGISTRY = {
    # ── Sales ──────────────────────────────────────────────────────────────
    'sales.require_customer_on_credit': dict(
        group='sales', type='bool', default=True,
        label='إلزام اختيار عميل عند البيع الآجل',
        help='لا يمكن إتمام فاتورة آجلة بدون تحديد العميل.'),
    'sales.allow_negative_stock': dict(
        group='sales', type='bool', default=False,
        label='السماح بالبيع بكمية أكبر من المتاح (رصيد سالب)',
        help='عند التفعيل يمكن البيع حتى لو لم يكفِ المخزون.'),
    'sales.show_profit_on_invoice': dict(
        group='sales', type='bool', default=False,
        label='إظهار الربح على الفواتير والتقارير',
        help='يعرض هامش الربح للمستخدمين المصرّح لهم.'),
    'sales.default_price_tier': dict(
        group='sales', type='choice', default='retail',
        choices=[('retail', 'قطاعي'), ('semi_wholesale', 'نص جملة'), ('wholesale', 'جملة')],
        label='شريحة السعر الافتراضية في الكاشير',
        help='السعر المختار تلقائياً عند إضافة منتج للسلة.'),

    # ── Cashier / POS ──────────────────────────────────────────────────────
    'cashier.allow_discount': dict(
        group='cashier', type='bool', default=True,
        label='السماح بالخصم في الكاشير',
        help='إيقافه يمنع أي خصم على مستوى المتجر (تحدّه أيضاً صلاحية المستخدم).'),
    'cashier.confirm_before_checkout': dict(
        group='cashier', type='bool', default=True,
        label='تأكيد قبل إتمام عملية البيع',
        help='يعرض نافذة مراجعة قبل حفظ الفاتورة.'),

    # ── Tax & service charge ─────────────────────────────────────────────────
    'tax.vat_rate': dict(
        group='tax', type='decimal', default='0.00',
        label='نسبة ضريبة القيمة المضافة %',
        help='0 = غير مفعّلة. تُستخدم في تقرير الضريبة والفاتورة (لا تغيّر أسعار البيع نفسها).'),
    'tax.vat_number': dict(
        group='tax', type='text', default='',
        label='الرقم الضريبي للمنشأة',
        help='يُطبع أسفل الفاتورة عند تفعيل الضريبة (اختياري).'),
    'tax.vat_included_in_price': dict(
        group='tax', type='bool', default=True,
        label='الضريبة مضمنة في الأسعار',
        help='مفعّلة (الافتراضي): الأسعار شاملة الضريبة بالفعل — الفاتورة تعرض جزء الضريبة دون تغيير الإجمالي. '
             'غير مفعّلة: الأسعار بدون ضريبة، وتُضاف الضريبة فوق الإجمالي في الفاتورة.'),
    'tax.service_charge_percent': dict(
        group='tax', type='decimal', default='0.00',
        label='نسبة رسوم الخدمة % (صالة فقط)',
        help='0 = غير مفعّلة. تُطبّق على طلبات الصالة (Dine-in) فقط — لا تُحسب أبداً على تيك أواي أو دليفري.'),
    'tax.service_charge_included_in_price': dict(
        group='tax', type='bool', default=False,
        label='رسوم الخدمة مضمنة في الأسعار',
        help='مفعّلة: الأسعار شاملة رسوم الخدمة بالفعل. غير مفعّلة (الافتراضي): تُضاف الرسوم فوق إجمالي '
             'الأصناف في فاتورة الصالة فقط.'),

    # ── Inventory ──────────────────────────────────────────────────────────
    'inventory.warn_low_stock': dict(
        group='inventory', type='bool', default=True,
        label='تنبيه عند انخفاض الرصيد عن حد النواقص',
        help='يظهر تحذير النواقص في الشاشات والتقارير.'),
    'inventory.block_sale_when_out_of_stock': dict(
        group='inventory', type='bool', default=False,
        label='منع بيع المنتجات منتهية الرصيد تماماً',
        help='يمنع إضافة منتج رصيده صفر للسلة (مستقل عن الرصيد السالب).'),

    # ── Purchases ──────────────────────────────────────────────────────────
    'purchases.update_cost_on_receipt': dict(
        group='purchases', type='bool', default=True,
        label='تحديث متوسط سعر التكلفة عند استلام المشتريات',
        help='يعيد حساب التكلفة تلقائياً من فواتير الشراء.'),
    'purchases.update_sale_price_on_receipt': dict(
        group='purchases', type='bool', default=False,
        label='اقتراح تحديث سعر البيع عند استلام المشتريات',
        help='يعرض اقتراحاً لتعديل سعر البيع وفق آخر تكلفة (7.3).'),

    # ── Customers ──────────────────────────────────────────────────────────
    'customers.enforce_credit_limit': dict(
        group='customers', type='bool', default=True,
        label='تطبيق حد الائتمان للعميل',
        help='يمنع تجاوز رصيد العميل لحد الائتمان المحدد له (يتجاوزه المدير).'),

    # ── Receipts ───────────────────────────────────────────────────────────
    'receipts.show_tax_breakdown': dict(
        group='receipts', type='bool', default=False,
        label='إظهار تفاصيل الضريبة على الإيصال',
        help='يعرض قيمة ضريبة القيمة المضافة منفصلة (يتطلب تفعيل الضريبة).'),
    'receipts.show_salesman_name': dict(
        group='receipts', type='bool', default=True,
        label='إظهار اسم البائع على الإيصال',
        help='يطبع اسم البائع/الكاشير في ترويسة الإيصال.'),

    # ── Shifts / Cash Register ───────────────────────────────────────────────
    'shifts.require_open_shift_before_sales': dict(
        group='shifts', type='bool', default=True,
        label='إلزام فتح وردية قبل البيع',
        help='يمنع أي عملية بيع/مرتجع في نقطة البيع بدون وردية مفتوحة أولاً (لا يشمل المالك/الأدمن).'),
    'shifts.cashier_can_open_shift': dict(
        group='shifts', type='bool', default=True,
        label='السماح للكاشير بفتح وردية',
        help='عند التفعيل يظهر للكاشير زر/نافذة فتح الوردية في شاشة الكاشير (تحدّه أيضاً صلاحية المستخدم). عند الإيقاف لا يظهر لأي كاشير، ويبقى فتح الوردية متاحاً للمالك/الأدمن دائماً.'),
    'shifts.cashier_can_close_shift': dict(
        group='shifts', type='bool', default=True,
        label='السماح للكاشير بإغلاق وردية',
        help='عند التفعيل يظهر للكاشير زر إغلاق الوردية في شاشة الكاشير (تحدّه أيضاً صلاحية المستخدم). عند الإيقاف لا يظهر لأي كاشير، ويبقى إغلاق الوردية متاحاً للمالك/الأدمن دائماً.'),

    # ── Kitchen (KDS) ──────────────────────────────────────────────────────
    'kitchen.orders_per_page': dict(
        group='kitchen', type='choice', default='8',
        choices=[('4', '4 قبل التمرير (بطاقات كبيرة)'), ('8', '8 قبل التمرير'),
                 ('12', '12 قبل التمرير'), ('16', '16 قبل التمرير'),
                 ('20', '20 قبل التمرير (بطاقات صغيرة)')],
        label='عدد الطلبات الظاهرة قبل الحاجة للتمرير (شاشة المطبخ)',
        help='كل الطلبات المفتوحة تظل معروضة دائماً ويمكن الوصول لها بالتمرير — هذا فقط يتحكم في حجم البطاقات: رقم أقل = بطاقات أكبر وأوضح، رقم أكبر = بطاقات أصغر تعرض أصنافاً أكثر بنظرة واحدة.'),

    # ── Payroll ──────────────────────────────────────────────────────────────
    'payroll.absence_deduction_percent': dict(
        group='payroll', type='decimal', default='3.33',
        label='نسبة الخصم عن كل يوم غياب (%)',
        help='النسبة المئوية من الراتب الأساسي التي تُخصم عن كل يوم غياب في قسيمة الراتب (الافتراضي 3.33% ≈ يوم من 30).'),
}


def _coerce(meta, raw):
    """Coerce a stored raw value to the policy's declared type, falling back to default.

    The registry default is coerced too (not returned raw) — a 'decimal' policy declared
    with a string default like '3.33' must still come back as a real Decimal, or arithmetic
    on the unconfigured (default) value blows up the first time anyone reads it.
    """
    t = meta.get('type', 'bool')
    default = meta.get('default')
    if raw is None:
        raw = default
        if raw is None:
            return None
    try:
        if t == 'bool':
            if isinstance(raw, bool):
                return raw
            return str(raw).lower() in ('1', 'true', 'on', 'yes')
        if t == 'int':
            return int(raw)
        if t == 'decimal':
            from decimal import Decimal
            return Decimal(str(raw))
        if t == 'choice':
            valid = {c[0] for c in meta.get('choices', [])}
            return raw if raw in valid else default
        return raw
    except Exception:
        return default


def _stored_values():
    try:
        from .models import SystemPolicy
        obj = SystemPolicy.objects.first()
        return (obj.values or {}) if obj else {}
    except Exception:
        return {}


def get_policy(key):
    """Resolved value for one policy key (stored value or registry default)."""
    meta = POLICY_REGISTRY.get(key)
    if not meta:
        return None
    return _coerce(meta, _stored_values().get(key))


def resolved_policies():
    """Full {key: resolved_value} map — for the context processor / forms."""
    stored = _stored_values()
    return {key: _coerce(meta, stored.get(key)) for key, meta in POLICY_REGISTRY.items()}


def grouped_registry():
    """[(group_key, group_label, [(key, meta), ...]), ...] in registry order — for the UI."""
    out = []
    for gkey, glabel in POLICY_GROUPS.items():
        items = [(k, m) for k, m in POLICY_REGISTRY.items() if m.get('group') == gkey]
        if items:
            out.append((gkey, glabel, items))
    return out
