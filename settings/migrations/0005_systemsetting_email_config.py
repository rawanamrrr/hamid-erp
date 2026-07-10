from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0004_systemsetting_notification_sound'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsetting',
            name='email_recipients',
            field=models.TextField(blank=True, default='', verbose_name='المستلمون (مفصولين بفواصل)'),
        ),
        migrations.AddField(
            model_name='systemsetting',
            name='gmail_app_password',
            field=models.CharField(blank=True, default='', max_length=64, verbose_name='كلمة مرور التطبيق'),
        ),
        migrations.AddField(
            model_name='systemsetting',
            name='gmail_sender_email',
            field=models.EmailField(blank=True, default='', max_length=254, verbose_name='بريد Gmail المرسل'),
        ),
    ]

