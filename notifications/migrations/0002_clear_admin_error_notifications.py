from django.db import migrations


def clear_admin_error_notifications(apps, schema_editor):
    """Remove the bell alerts that pointed the shop owner at the raw Django admin.

    A 500 error used to create one notification per superuser linking to
    /admin/accounts/systemerror/. The admin is an internal developer tool, deliberately
    kept out of the shop's own UI — but this notification was still surfacing it, and its
    only possible action was to open that page. accounts/middleware.py no longer creates
    them, and this clears the ones already sitting unread in the bell so the change is
    visible immediately instead of only applying to future errors.

    Scoped to exactly that link, so every other notification (low stock, new order,
    customer payment, ...) is untouched. Nothing of value is lost: the errors themselves
    live in accounts.SystemError with their full traceback, which is where they are
    actually diagnosed from.
    """
    Notification = apps.get_model('notifications', 'Notification')
    Notification.objects.filter(link='/admin/accounts/systemerror/').delete()


def noop_reverse(apps, schema_editor):
    """Nothing to restore — these were transient alerts, not records. The underlying
    SystemError rows they pointed at were never touched."""


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(clear_admin_error_notifications, noop_reverse),
    ]
