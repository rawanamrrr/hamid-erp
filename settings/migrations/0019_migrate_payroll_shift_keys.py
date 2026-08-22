from django.db import migrations


def migrate_shift_keys(apps, schema_editor):
    """The single-shift payroll.work_start_time/work_end_time/grace_period_minutes keys
    were replaced by payroll.shift_1_start/shift_1_end/shift_1_grace_minutes (plus
    shifts 2-5). Any store that had already customized the old keys needs those values
    carried over into shift 1 — otherwise they'd silently revert to registry defaults
    the moment this ships."""
    SystemPolicy = apps.get_model('settings', 'SystemPolicy')
    policy = SystemPolicy.objects.filter(pk=1).first()
    if not policy or not policy.values:
        return

    old_to_new = {
        'payroll.work_start_time': 'payroll.shift_1_start',
        'payroll.work_end_time': 'payroll.shift_1_end',
        'payroll.grace_period_minutes': 'payroll.shift_1_grace_minutes',
    }
    changed = False
    for old_key, new_key in old_to_new.items():
        if old_key in policy.values:
            policy.values[new_key] = policy.values.pop(old_key)
            changed = True
    if changed:
        policy.save(update_fields=['values'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0018_alter_systemsetting_address'),
    ]

    operations = [
        migrations.RunPython(migrate_shift_keys, noop),
    ]
