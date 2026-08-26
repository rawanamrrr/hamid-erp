from django.db import migrations


def split_ticket_policies(apps, schema_editor):
    """One kitchen-ticket switch became two, answering separate questions.

    The old 'kitchen.auto_print_ticket_on_order' mixed together "should an order produce
    a kitchen ticket?" with "should it go straight to the kitchen printer instead of
    showing a page?" — which meant there was no way to say "no ticket at all", and the
    same setting behaved differently on the waiter screen than on the cashier screen.

    It is replaced by:
      kitchen.print_ticket_on_order — is a ticket produced at all
      kitchen.auto_print_ticket    — does it print by itself, or wait for طباعة

    Both new keys default to True, i.e. the behaviour a shop already had: every order got
    a ticket, and it printed without anyone pressing anything. So the migration only has
    to clear the retired key rather than translate a value — carrying the old boolean
    across would be wrong in both directions (its True meant "print directly", not
    "tickets on", and its False meant "show the page", not "tickets off").

    Also renames the invoice key added alongside it, before any store has configured it.
    """
    SystemPolicy = apps.get_model('settings', 'SystemPolicy')
    policy = SystemPolicy.objects.filter(pk=1).first()
    if not policy or not policy.values:
        return

    changed = False

    for retired in ('kitchen.auto_print_ticket_on_order',
                    'kitchen.auto_open_ticket_print_dialog'):
        if retired in policy.values:
            del policy.values[retired]
            changed = True

    old_invoice_key = 'receipts.auto_open_invoice_print_dialog'
    if old_invoice_key in policy.values:
        policy.values['receipts.auto_print_invoice'] = policy.values.pop(old_invoice_key)
        changed = True

    if changed:
        policy.save(update_fields=['values'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0021_systemsetting_kitchen_printer_name'),
    ]

    operations = [
        migrations.RunPython(split_ticket_policies, noop),
    ]
