from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('financial', '0002_alter_transaction_created_by_alter_transaction_shift_and_more'),
        ('sales', '0011_order_discount_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='shift',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='financial.dailyshift', verbose_name='الشيفت'),
        ),
    ]

