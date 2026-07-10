import hashlib

from django.conf import settings
from django.db import migrations


def _signature(lic):
    """Re-implements SystemLicense.get_full_signature() using only fields that exist as of
    this migration, so it works on the HISTORICAL model (apps.get_model) and never references
    columns added by later migrations. Keep in sync with the model method if the formula changes."""
    fields = [
        str(lic.store_id),
        str(lic.subscription_expires_at.isoformat() if lic.subscription_expires_at else "none"),
        str(lic.grace_period_used),
        str(lic.grace_period_started_at.isoformat() if lic.grace_period_started_at else "none"),
        str(lic.system_locked),
        str(lic.store_type),
        str(lic.device_id),
        str(sorted(lic.enabled_modules or [])),
        str(settings.SECRET_KEY),
    ]
    return hashlib.sha3_512("||".join(fields).encode('utf-8')).hexdigest()


def resign_licenses(apps, schema_editor):
    """Phase ②: the license signature formula now includes enabled_modules. Re-sign every
    existing license so its stored signature matches the new formula (otherwise
    is_signature_valid() would return False and the system would lock).

    Uses the HISTORICAL model via apps.get_model — the concrete model selects newer columns
    (e.g. used_token_hashes) that don't exist yet during a fresh-DB replay, which would crash
    `migrate` on every new install. .update() avoids re-running save() side-effects."""
    SystemLicense = apps.get_model('licensing', 'SystemLicense')
    for lic in SystemLicense.objects.all():
        try:
            SystemLicense.objects.filter(pk=lic.pk).update(license_signature=_signature(lic))
        except Exception:
            pass


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('licensing', '0008_alter_tokenlog_action'),
    ]
    operations = [
        migrations.RunPython(resign_licenses, noop),
    ]
