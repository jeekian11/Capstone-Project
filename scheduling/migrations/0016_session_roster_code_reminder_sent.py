from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0015_classroster_schedule_valid_from_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='roster_code_reminder_sent',
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Set once the \"here's your reservation code\" reminder (sent 1 hour before start, "
                    "for sessions auto-generated from a Class Roster schedule) has gone out, so it is "
                    "not sent twice."
                ),
            ),
        ),
    ]
