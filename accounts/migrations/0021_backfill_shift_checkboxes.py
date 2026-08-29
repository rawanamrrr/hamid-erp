from django.db import migrations


def backfill_shift_access(apps, schema_editor):
    """Restore shift access the day financial:open_shift/close_shift shipped silently
    took away, for anyone it happened to.

    Those two checkboxes on the per-user permissions page (تخصيص القائمة الجانبية) never
    carried a fallback to pos:create the way this same code's sales:edit_order box does.
    So the checkbox pre-filled UNCHECKED for every cashier whose access to opening/closing
    a shift had only ever come from the functional "you can ring up a sale, so you can run
    your own drawer" fallback — nothing had ever written the literal string 'open_shift'
    into their permissions before. The FIRST time an admin saved that page for such a
    user, for any reason at all, the save wrote an explicit financial permission list
    without those two actions, and from that point on the strict "admin customized this
    module, respect the list" rule silently blocked shift management — with no way to
    tell it apart from a deliberate denial, and immune to the شفتات ← "الكاشير يقدر يفتح
    وردية" system-wide policy, which is checked before this and was already ON.

    Only additive, and only where it restores exactly what the corrected pre-fill would
    already show as checked: a user whose financial direct_permissions is an explicit,
    non-denied list missing 'open_shift'/'close_shift', and who currently has pos:create
    (the functional definition of "a cashier") from their role or their own pos
    permissions. A user whose role never granted pos:create in the first place was never
    eligible via the fallback either, so nothing changes for them.
    """
    UserProfile = apps.get_model('accounts', 'UserProfile')

    for profile in UserProfile.objects.exclude(direct_permissions=None):
        dp = profile.direct_permissions or {}
        financial = dp.get('financial')
        if not financial or financial == ['__denied__']:
            continue
        missing = [a for a in ('open_shift', 'close_shift') if a not in financial]
        if not missing:
            continue

        # Functional cashier check: pos:create from the role, or from this same
        # profile's own direct_permissions if 'pos' was customized too.
        pos_actions = set(dp.get('pos') or [])
        if 'pos' not in dp or '__denied__' in pos_actions:
            for role in profile.roles.all():
                pos_actions |= set(role.permissions.get('pos') or [])
        if 'create' not in pos_actions:
            continue

        dp['financial'] = sorted(set(financial) | set(missing))
        profile.direct_permissions = dp
        profile.save(update_fields=['direct_permissions'])


def unbackfill(apps, schema_editor):
    # Not reversible in a targeted way (we no longer know which entries this added);
    # left as a no-op rather than stripping shift access from anyone.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0020_alter_approvalrequest_kind'),
    ]

    operations = [
        migrations.RunPython(backfill_shift_access, unbackfill),
    ]
