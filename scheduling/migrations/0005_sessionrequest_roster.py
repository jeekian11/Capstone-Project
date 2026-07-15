from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0004_manualattendancerecord'),
    ]

    operations = [
        migrations.AddField(
            model_name='sessionrequest',
            name='roster',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='session_requests',
                to='scheduling.classroster',
                help_text=(
                    'Optional — link a roster to let everyone on it check in with this same reservation '
                    'code, not just the requester. Use this when an Instructor books for their whole '
                    'class, or when a group of students requests together. Leave blank for a single '
                    'individual request.'
                ),
            ),
        ),
    ]
