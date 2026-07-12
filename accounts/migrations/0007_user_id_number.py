from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0006_user_email_notifications_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='id_number',
            field=models.CharField(
                blank=True, default='', max_length=50,
                help_text='Student/Instructor ID — used to match this account to reservation requests and lab PC check-ins. Separate from the login username.',
            ),
        ),
    ]
