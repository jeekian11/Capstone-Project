from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0013_session_reminder_sent'),
    ]

    operations = [
        migrations.AddField(
            model_name='sessioncheckin',
            name='guest_name',
            field=models.CharField(
                blank=True,
                default='',
                help_text="Full name of a walk-in guest with no registered account, entered by the Lab In-Charge before a Manual Unlock. Only used when checkin_type='guest' (student stays null for these).",
                max_length=150,
            ),
        ),
        migrations.AlterField(
            model_name='sessioncheckin',
            name='checkin_type',
            field=models.CharField(
                choices=[
                    ('requester', 'Primary Requester'),
                    ('roster', 'Roster Member'),
                    ('group', 'Group Member'),
                    ('walk_in', 'Walk-in'),
                    ('override', 'Override'),
                    ('guest', 'Guest (Manual Unlock)'),
                ],
                default='requester',
                max_length=10,
            ),
        ),
    ]
