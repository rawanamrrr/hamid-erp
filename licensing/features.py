"""Dev-locked feature entitlements (Phase ②).

Features here are OFF by default and can ONLY be turned on by a signed dev token
(ENABLE_MODULE / DISABLE_MODULE) — the customer cannot self-enable them. The active set
lives in SystemLicense.enabled_modules, which is signature-protected (see
SystemLicense.get_full_signature), so editing it directly in the database invalidates the
license signature and the module is not honored.

Distinct from the policy/settings engine (customer-configurable preferences) and from the
market profile (which decides what a market exposes at all).
"""

# key -> human label (Arabic). Keep keys stable; they're what tokens reference.
FEATURE_REGISTRY = {
    'advanced_reports': 'التقارير والتحليلات المتقدمة',
    'einvoice':         'الفاتورة الإلكترونية (ETA)',
    'multi_branch':     'تعدد الفروع',
    'serials':          'تتبع الأرقام التسلسلية',
    'restaurant':       'وضع المطعم',
    'label_printing':   'طباعة الملصقات والباركود',
    'storefront':       'المتجر الإلكتروني',
    'multi_warehouse':  'تعدد المخازن',
    'loyalty':          'نقاط الولاء',
}


def _active_license():
    try:
        from licensing.models import SystemLicense
        return SystemLicense.objects.first()
    except Exception:
        return None


def has_module(module_key):
    """True only if the dev enabled this feature for this install via a valid, untampered
    license. A tampered enabled_modules breaks the license signature → returns False."""
    lic = _active_license()
    if not lic:
        return False
    try:
        if not lic.is_signature_valid():
            return False  # tampered license — do not honor any entitlement
    except Exception:
        return False
    return module_key in (lic.enabled_modules or [])


def enabled_map():
    """{feature_key: bool} for all registered features — for templates/context."""
    lic = _active_license()
    valid = False
    enabled = []
    if lic:
        try:
            valid = lic.is_signature_valid()
        except Exception:
            valid = False
        enabled = (lic.enabled_modules or []) if valid else []
    return {key: (key in enabled) for key in FEATURE_REGISTRY}


def require_module(module_key):
    """View decorator (Phase ②): block access unless the dev has enabled `module_key`
    via token. Renders a 'feature locked' page (HTTP 403) otherwise."""
    from functools import wraps
    from django.shortcuts import render

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not has_module(module_key):
                label = FEATURE_REGISTRY.get(module_key, module_key)
                return render(request, 'licensing/feature_locked.html',
                              {'module_key': module_key, 'module_label': label}, status=403)
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator
