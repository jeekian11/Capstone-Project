from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0012_session_pcs_requested_sessionrequest_pcs_requested_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='reminder_sent',
            field=models.BooleanField(default=False, help_text='Set once the "session starting soon" auto-notification has been sent for this session, so it is not sent twice.'),
        ),
    ]
