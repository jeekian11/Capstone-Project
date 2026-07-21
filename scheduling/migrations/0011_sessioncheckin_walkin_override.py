from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0001_initial'),
        ('scheduling', '0010_classroster_schedule_days_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='sessioncheckin',
            name='checkin_type',
            field=models.CharField(
                choices=[
                    ('requester', 'Primary Requester'),
                    ('roster', 'Roster Member'),
                    ('group', 'Group Member'),
                    ('walk_in', 'Walk-in'),
                    ('override', 'Override'),
                ],
                default='requester',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='sessioncheckin',
            name='student',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='session_checkins', to=settings.AUTH_USER_MODEL,
                help_text='The registered account resolved from id_number, if any.',
            ),
        ),
        migrations.AddField(
            model_name='sessioncheckin',
            name='pc',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='checkins', to='labs.pc',
            ),
        ),
        migrations.AlterField(
            model_name='sessioncheckin',
            name='session',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.CASCADE,
                related_name='check_ins', to='scheduling.session',
            ),
        ),
    ]
