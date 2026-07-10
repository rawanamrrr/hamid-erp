from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('products', '0008_supplier_financial_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='packaging_type',
            field=models.CharField(
                blank=True,
                choices=[
                    ('', 'لا يوجد'),
                    ('علبة', 'علبة'),
                    ('شريط', 'شريط'),
                    ('أمبول', 'أمبول'),
                    ('كرتون', 'كرتون'),
                    ('زجاجة', 'زجاجة'),
                    ('كيس', 'كيس'),
                    ('وحدة', 'وحدة'),
                ],
                default='',
                max_length=20,
                verbose_name='نوع التعبئة (صيدلية/بقالة)',
                help_text='علبة، شريط، أمبول... للصيدليات والبقالات'
            ),
        ),
    ]
