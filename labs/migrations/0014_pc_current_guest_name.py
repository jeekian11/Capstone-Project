from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0013_pcactivitylog_page_url'),
    ]

    operations = [
        migrations.AddField(
            model_name='pc',
            name='current_guest_name',
            field=models.CharField(
                blank=True,
                default='',
                help_text="Full name of the walk-in guest currently using this PC via Manual Unlock, when there is no registered account (current_user stays null for guests). Cleared whenever the PC is locked again.",
                max_length=150,
            ),
        ),
    ]
