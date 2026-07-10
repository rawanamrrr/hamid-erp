from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('financial', '0004_add_refund_transaction_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShiftEmailLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sent_at', models.DateTimeField(auto_now_add=True, verbose_name='وقت الإرسال')),
                ('success', models.BooleanField(default=False, verbose_name='تم الإرسال بنجاح')),
                ('error_message', models.TextField(blank=True, default='', verbose_name='رسالة الخطأ')),
                ('shift', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='email_logs', to='financial.dailyshift', verbose_name='الشيفت')),
            ],
            options={
                'verbose_name': 'سجل إرسال إيميل الشيفت',
                'verbose_name_plural': 'سجلات إرسال إيميلات الشيفت',
                'ordering': ['-sent_at'],
            },
        ),
    ]

