from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0005_sessionrequest_roster'),
    ]

    operations = [
        migrations.AlterField(
            model_name='session',
            name='requester_type',
            field=models.CharField(
                choices=[
                    ('instructor', 'Instructor'),
                    ('student', 'Student'),
                    ('group', 'Group of students'),
                ],
                default='instructor',
                max_length=10,
            ),
        ),
        migrations.AlterField(
            model_name='sessionrequest',
            name='requester_type',
            field=models.CharField(
                choices=[
                    ('instructor', 'Instructor'),
                    ('student', 'Student'),
                    ('group', 'Group of students'),
                ],
                default='instructor',
                max_length=10,
            ),
        ),
        migrations.AlterField(
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
                    'code, not just the requester. Use this for an Instructor booking their whole class, '
                    'so attendance shows real names. Not needed for "Group of students" — any ID works '
                    'with the shared code for that type. Leave blank for a single individual (Student) '
                    'request.'
                ),
            ),
        ),
    ]
