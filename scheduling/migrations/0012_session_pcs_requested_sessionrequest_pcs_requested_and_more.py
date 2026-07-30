# Generated manually — adds Walk-in / Override reservation support.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0011_sessioncheckin_walkin_override'),
    ]

    operations = [
        migrations.AddField(
            model_name='session',
            name='pcs_requested',
            field=models.PositiveIntegerField(default=0, help_text='Required for Walk-in and Override requests — how many PCs are needed. Ignored for Instructor/Student/Group requests, which still reserve the whole lab.'),
        ),
        migrations.AddField(
            model_name='sessionrequest',
            name='pcs_requested',
            field=models.PositiveIntegerField(default=0, help_text='Required for Walk-in and Override requests — how many PCs are needed. Ignored for Instructor/Student/Group requests, which still reserve the whole lab.'),
        ),
        migrations.AlterField(
            model_name='session',
            name='requester_type',
            field=models.CharField(choices=[('instructor', 'Instructor'), ('student', 'Student'), ('group', 'Group of students'), ('walk_in', 'Walk-in'), ('override', 'Override')], default='instructor', max_length=10),
        ),
        migrations.AlterField(
            model_name='sessionrequest',
            name='requester_type',
            field=models.CharField(choices=[('instructor', 'Instructor'), ('student', 'Student'), ('group', 'Group of students'), ('walk_in', 'Walk-in'), ('override', 'Override')], default='instructor', max_length=10),
        ),
    ]
