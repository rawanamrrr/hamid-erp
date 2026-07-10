from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0005_systemsetting_email_config'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsetting',
            name='market_type',
            field=models.CharField(
                choices=[
                    ('clothes', 'ملابس وأقمشة'),
                    ('pharmacy', 'صيدلية'),
                    ('electronics', 'إلكترونيات'),
                    ('grocery', 'بقالة وسوبرماركت'),
                    ('general', 'متجر عام'),
                ],
                default='clothes',
                max_length=20,
                verbose_name='نوع المتجر / السوق'
            ),
        ),
    ]
