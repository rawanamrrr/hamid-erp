from .models import SystemSetting
from .market_profiles import get_market_profile, DEFAULT_MARKET


def _canonical_market_type(settings_obj):
    """Single source of truth for the active market type (Phase ③).

    The dev token (CHANGE_STORE_TYPE) writes SystemLicense.store_type and locks the market.
    When the market is locked, the license is canonical and SystemSetting.market_type is
    self-healed to match — this fixes the desync where a customer's settings choice could
    override the dev token. When NOT locked (fresh onboarding), the customer's setting wins.
    """
    setting_mt = getattr(settings_obj, 'market_type', None) or DEFAULT_MARKET
    locked = bool(getattr(settings_obj, 'is_market_type_locked', False))
    if not locked:
        return setting_mt
    try:
        from licensing.models import SystemLicense
        lic = SystemLicense.objects.first()
        store_type = getattr(lic, 'store_type', None) if lic else None
    except Exception:
        store_type = None
    if store_type and store_type != setting_mt:
        # Self-heal the divergence so every direct read of market_type also agrees.
        try:
            SystemSetting.objects.filter(pk=getattr(settings_obj, 'pk', 1)).update(market_type=store_type)
            settings_obj.market_type = store_type
        except Exception:
            pass
        return store_type
    return store_type or setting_mt


def system_settings(request):
    try:
        settings_obj = SystemSetting.objects.first()
    except Exception:
        settings_obj = None

    try:
        from products.models import Product
        unit_choices = Product.get_combined_unit_choices()
    except Exception:
        unit_choices = []

    return {
        'sys_settings': settings_obj,
        'unit_choices': unit_choices,
    }


def system_policies(request):
    """Expose the resolved Layer-2 policy map to every template.

    Keys are dotted (`sales.show_profit_on_invoice`); Django templates can't resolve dotted
    dict keys, so we nest them by group: templates read `policies.sales.show_profit_on_invoice`.
    Backend code uses settings.policies.get_policy('sales.show_profit_on_invoice') instead.
    """
    try:
        from .policies import resolved_policies
        nested = {}
        for key, val in resolved_policies().items():
            group, _, name = key.partition('.')
            nested.setdefault(group, {})[name] = val
        return {'policies': nested}
    except Exception:
        return {'policies': {}}


def user_capabilities(request):
    """Expose resolved per-user operational capabilities to every template (Phase 3.2).

    `can_view_profit` — templates wrap profit columns/figures in `{% if can_view_profit %}`.
    `can_edit_price` — controls whether the POS cart's inline price field is editable; the
    backend already silently overrides the price server-side when this is False (see
    submit_order_ajax), but the input stayed editable in the UI regardless, which is
    confusing — the cashier could type a different price and only find out at checkout
    that it didn't actually apply.
    Master/superuser always pass (see UserProfile helpers). Anonymous/profile-less users
    default to False (most restrictive).
    """
    can_view_profit = False
    can_edit_price = False
    can_change_unit = True
    try:
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            prof = getattr(user, 'profile', None)
            if prof is not None:
                can_view_profit = prof.allows_view_profit()
                can_edit_price = prof.allows_price_edit()
                can_change_unit = prof.allows_change_unit()
            elif user.is_superuser:
                can_view_profit = True
                can_edit_price = True
                can_change_unit = True
    except Exception:
        can_view_profit = False
        can_edit_price = False
        can_change_unit = True
    return {
        'can_view_profit': can_view_profit,
        'can_edit_price': can_edit_price,
        'can_change_unit': can_change_unit,
    }


def market_profile(request):
    """Expose the active market profile (Phase ③) to every template.

    `mp`          -> the profile dict (terms/show/features) — templates read mp.terms.cost_label etc.
    `market_type` -> the canonical market-type key (single source of truth).
    """
    try:
        settings_obj = SystemSetting.objects.first()
    except Exception:
        settings_obj = None
    market_type = _canonical_market_type(settings_obj)
    return {
        'mp': get_market_profile(market_type),
        'market_type': market_type,
    }
