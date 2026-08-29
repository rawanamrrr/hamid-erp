from django.db import migrations


def seed_plain_sequence(apps, schema_editor):
    """Start the new running invoice number above everything already in use.

    Invoice numbers used to be INV-YYYY-NNNNN, and only cashier sales got one — a waiter's
    check showed its database id instead. They are now one plain running sequence for
    every order, which means the counter has to begin somewhere that cannot collide with
    what is already on paper in a customer's hand:

      * higher than the largest order id, because every unnumbered order has been shown
        to someone as "#<id>" and reusing those digits would put the same number on two
        different receipts;
      * higher than any numeric invoice_number that already exists, for the same reason.

    Old INV-YYYY-NNNNN numbers are deliberately left alone. Rewriting them would change
    the number printed on invoices customers already have, and on the accounting records
    that reference them.
    """
    DocumentSequence = apps.get_model('sales', 'DocumentSequence')
    Order = apps.get_model('sales', 'Order')

    highest = 0
    last_order = Order.objects.order_by('-id').first()
    if last_order:
        highest = last_order.id

    for number in Order.objects.exclude(invoice_number=None).values_list('invoice_number', flat=True):
        if number and str(number).isdigit():
            highest = max(highest, int(number))

    DocumentSequence.objects.update_or_create(
        doc_type='INV', year=0, defaults={'last_number': highest},
    )


def unseed(apps, schema_editor):
    DocumentSequence = apps.get_model('sales', 'DocumentSequence')
    DocumentSequence.objects.filter(doc_type='INV', year=0).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0050_expense_approved_by_expense_payment_method_and_more'),
    ]

    operations = [
        migrations.RunPython(seed_plain_sequence, unseed),
    ]
