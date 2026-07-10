from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('crm', '0003_customer_opening_balance'),
        ('products', '0006_productcosting'),
        ('sales', '0012_order_shift'),
    ]

    operations = [
        migrations.CreateModel(
            name='Draft',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(blank=True, max_length=255, verbose_name='اسم المسودة')),
                ('cart_data', models.JSONField(default=list, verbose_name='بيانات السلة')),
                ('delivery_cost', models.DecimalField(decimal_places=2, default=0.0, max_digits=10, verbose_name='تكلفة التوصيل')),
                ('discount', models.DecimalField(decimal_places=2, default=0.0, max_digits=10, verbose_name='الخصم')),
                ('notes', models.TextField(blank=True, verbose_name='ملاحظات')),
                ('status', models.CharField(choices=[('open', 'مفتوحة'), ('closed', 'مغلقة')], default='open', max_length=10, verbose_name='الحالة')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='تاريخ الإنشاء')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='آخر تحديث')),
                ('customer', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='crm.customer', verbose_name='العميل')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='pos_drafts', to='auth.user', verbose_name='الموظف')),
                ('warehouse', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='products.warehouse', verbose_name='المخزن')),
            ],
            options={
                'ordering': ['-updated_at', '-id'],
            },
        ),
    ]
