from .models import SystemLicense


def license_info(request):
    try:
        license = SystemLicense.objects.first()
        return {
            'system_license': license
        }
    except:
        return {
            'system_license': None
        }


def feature_flags(request):
    """Phase ②: expose dev-locked feature entitlements to templates as `features.<key>`
    (e.g. {% if features.einvoice %}). Token-gated; customer cannot self-enable."""
    try:
        from .features import enabled_map
        return {'features': enabled_map()}
    except Exception:
        return {'features': {}}
