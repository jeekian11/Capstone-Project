import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0006_alter_lab_closing_time_alter_lab_opening_time'),
        ('scheduling', '0004_manualattendancerecord'),
    ]

    operations = [
        migrations.AddField(
            model_name='pc',
            name='current_session',
            field=models.ForeignKey(
                blank=True,
                help_text="The reservation that most recently unlocked this PC. Used to auto re-lock the PC once that reservation's end_time passes, and cleared whenever the PC is locked again (by time running out or a manual lock).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='+',
                to='scheduling.session',
            ),
        ),
    ]
