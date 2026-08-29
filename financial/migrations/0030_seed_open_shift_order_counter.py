from django.db import migrations


def seed_open_shifts(apps, schema_editor):
    """Let a shift that is already open carry on counting instead of restarting mid-way.

    Receipts now count 1, 2, 3... inside each shift. A shift that is open at the moment
    this ships already has orders in it, numbered the old shop-wide way, and those are
    deliberately left as they are — rewriting them would change a number already printed
    on a receipt somebody is holding, and on a shift report that may already be filed.

    So the counter for an open shift starts where its existing orders leave off: the next
    sale in it gets N+1 rather than 1, and only genuinely NEW shifts begin at 1. Closed
    shifts are untouched; nothing will ever be added to them.
    """
    DailyShift = apps.get_model('financial', 'DailyShift')
    Order = apps.get_model('sales', 'Order')

    for shift in DailyShift.objects.filter(is_closed=False):
        DailyShift.objects.filter(pk=shift.pk).update(
            last_order_number=Order.objects.filter(shift_id=shift.pk).count()
        )


def unseed(apps, schema_editor):
    DailyShift = apps.get_model('financial', 'DailyShift')
    DailyShift.objects.filter(is_closed=False).update(last_order_number=0)


class Migration(migrations.Migration):

    dependencies = [
        ('financial', '0029_dailyshift_last_order_number'),
        ('sales', '0052_order_shift_number'),
    ]

    operations = [
        migrations.RunPython(seed_open_shifts, unseed),
    ]
