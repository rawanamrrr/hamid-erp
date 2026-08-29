"""
User-customizable shortcuts (favorites) registry.

Each entry: key (url name), label, icon, section, (module, action) or None, master_only flag.
"""
from django.urls import reverse, NoReverseMatch

# key, label, icon (Font Awesome), section, (module, action) or None, master_only
AVAILABLE_SHORTCUTS = [
    # ── الرئيسية ──
    # Not None: the dashboard is gated on dashboard.view like any other page. Leaving it
    # unconditional made it look available to everyone, and since it is the first entry
    # here it became the landing page for every role without one of its own — so a
    # stock-keeper, an accountant or a CRM user logging in was sent straight to a page
    # they are not allowed to open, and met "ممنوع" instead of their own screen.
    ('dashboard',                    'الرئيسية',           'fa-gauge-high',            'الرئيسية',            ('dashboard', 'view'),   False),
    ('pos_view',                     'نقطة البيع',         'fa-cash-register',         'الرئيسية',            ('pos', 'view'),          False),

    # ── المبيعات ──
    ('order_list',                   'سجل الفواتير',       'fa-file-invoice',          'المبيعات',            ('pos', 'view'),          False),
    ('sold_items_list',              'مبيعات الأصناف',     'fa-list-check',            'المبيعات',            ('sales', 'view'),        False),
    ('refund_view',                  'مرتجع جديد',         'fa-rotate-left',           'المبيعات',            ('sales', 'refund'),      False),
    ('returns_register',             'سجل المرتجعات',      'fa-clock-rotate-left',     'المبيعات',            ('sales', 'refund'),      False),
    ('oversold_register',            'سجل مبيعات تجاوزت المخزون المتاح', 'fa-triangle-exclamation', 'المبيعات', ('sales', 'view'),  False),
    ('quotation_list',               'عروض الأسعار',       'fa-file-lines',            'المبيعات',            ('pos', 'view'),          False),
    ('reservation_list',             'الحجوزات',           'fa-calendar-check',        'المبيعات',            ('pos', 'view'),          False),
    ('expense_list',                 'المصروفات',          'fa-receipt',               'المبيعات',            ('financial', 'view'),    False),
    ('financial:salary_list',        'رواتب الموظفين',     'fa-money-bill-wave',       'المبيعات',            ('financial', 'view'),    False),
    ('financial:deal_list',          'العروض والخصومات',   'fa-tags',                  'المبيعات',            ('financial', 'view'),    False),

    # ── المخزون والمستودعات ──
    ('product_list',                 'إدارة الأصناف',      'fa-barcode',               'المخزون والمستودعات', ('products', 'view'),     False),
    ('warehouse_list',               'قائمة المستودعات',   'fa-warehouse',             'المخزون والمستودعات', ('products', 'view'),     False),
    ('transaction_list',             'حركة المخزون',       'fa-right-left',            'المخزون والمستودعات', ('products', 'view'),     False),
    ('low_stock_report',             'النواقص',            'fa-triangle-exclamation',  'المخزون والمستودعات', ('products', 'view'),     False),
    ('inventory_insights',           'تحليل الطلب',        'fa-lightbulb',             'المخزون والمستودعات', ('products', 'view'),     False),
    ('stock_valuation',              'تقييم المخزون',      'fa-coins',                 'المخزون والمستودعات', ('products', 'view'),     False),
    ('stocktake_list',               'الجرد',              'fa-clipboard-check',       'المخزون والمستودعات', ('products', 'view'),     False),
    ('supplier_list',                'قائمة الموردين',     'fa-truck',                 'المخزون والمستودعات', ('products', 'view'),     False),
    ('purchase_invoice_create',      'فاتورة شراء جديدة',  'fa-file-invoice-dollar',   'المخزون والمستودعات', ('products', 'view'),     False),
    ('purchase_order_list',          'أوامر الشراء (PO)',  'fa-cart-flatbed',          'المخزون والمستودعات', ('products', 'view'),     False),

    # ── الشحن ──
    ('shipping_dashboard',           'متابعة الشحن',       'fa-shipping-fast',         'الشحن',               ('shipping', 'view'),     False),
    ('company_list',                 'شركات الشحن',        'fa-truck-moving',          'الشحن',               ('shipping', 'view'),     False),

    # ── العملاء ──
    ('customer_list',                'قاعدة بيانات العملاء','fa-users-viewfinder',      'العملاء',             ('crm', 'view'),          False),
    ('ar_aging',                     'أعمار الديون',       'fa-hourglass-half',        'العملاء',             ('crm', 'view'),          False),

    # ── التقارير المالية ──
    ('financial:dashboard',          'الخزنة والشيفتات',   'fa-vault',                 'التقارير المالية',    ('financial', 'view'),    False),
    ('financial:daily_summary',      'ملخص اليوم',         'fa-calendar-day',          'التقارير المالية',    ('financial', 'view'),    False),
    ('financial:profitability',      'الربحية',            'fa-sack-dollar',           'التقارير المالية',    ('financial', 'view'),    False),
    ('financial:financial_position', 'المركز المالي',      'fa-scale-unbalanced',      'التقارير المالية',    ('financial', 'view'),    False),
    ('financial:income_statement',   'قائمة الدخل',        'fa-chart-pie',             'التقارير المالية',    ('financial', 'view'),    False),
    ('financial:sales_analytics',    'تحليلات المبيعات',   'fa-chart-line',            'التقارير المالية',    ('financial', 'view'),    False),

    # ── التحكم والنظام ──
    ('user_list',                    'المستخدمين',         'fa-users-gear',            'التحكم والنظام',      ('users', 'view'),        False),
    ('settings_view',                'إعدادات النظام',     'fa-sliders',               'التحكم والنظام',      ('settings', 'view'),     False),

    # ── أدوات المالك (master only) ──
    ('license_activation',           'إدارة الترخيص',      'fa-key',                   'أدوات المالك',        None,                    True),
    ('activity_logs',                'سجل الأنشطة',        'fa-clipboard-list',        'أدوات المالك',        None,                    True),
    ('error_history',                'سجل الأخطاء',        'fa-exclamation-triangle',  'أدوات المالك',        None,                    True),
    ('financial:all_transactions',   'كل الحركات المالية', 'fa-money-bill-transfer',   'أدوات المالك',        None,                    True),
    ('financial:shift_history',      'سجل الورديات',       'fa-clock-rotate-left',     'أدوات المالك',        None,                    True),
    ('role_list',                    'إدارة الأدوار',      'fa-shield-halved',         'أدوات المالك',        None,                    True),
]

_BY_KEY = {s[0]: s for s in AVAILABLE_SHORTCUTS}

SECTION_ORDER = [
    'الرئيسية', 'المبيعات', 'المخزون والمستودعات', 'الشحن',
    'العملاء', 'التقارير المالية', 'التحكم والنظام', 'أدوات المالك',
]


def _can(user, perm, master_only):
    if master_only:
        return getattr(getattr(user, 'profile', None), 'is_master', False)
    if perm is None:
        return True
    from .permissions import has_permission
    return has_permission(user, perm[0], perm[1])


def available_for(user):
    """Shortcuts the user is allowed to pin (resolvable + permitted)."""
    out = []
    for key, label, icon, section, perm, master_only in AVAILABLE_SHORTCUTS:
        if not _can(user, perm, master_only):
            continue
        try:
            url = reverse(key)
        except NoReverseMatch:
            continue
        out.append({'key': key, 'label': label, 'icon': icon, 'section': section, 'url': url})
    return out


def available_grouped(user):
    """Return shortcuts grouped by section, in SECTION_ORDER."""
    flat = available_for(user)
    groups = {}
    for s in flat:
        groups.setdefault(s['section'], []).append(s)
    return [{'section': sec, 'items': groups[sec]} for sec in SECTION_ORDER if sec in groups]


def resolve_favorites(user):
    """Resolve a user's saved favorite keys into renderable {key,label,icon,url}."""
    profile = getattr(user, 'profile', None)
    keys = (profile.favorites or []) if profile else []
    out = []
    for key in keys:
        meta = _BY_KEY.get(key)
        if not meta or not _can(user, meta[4], meta[5]):
            continue
        try:
            url = reverse(key)
        except NoReverseMatch:
            continue
        out.append({'key': key, 'label': meta[1], 'icon': meta[2], 'url': url})
    return out
