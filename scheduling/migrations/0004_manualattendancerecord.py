# Generated manually to match scheduling/models.py changes

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('scheduling', '0003_classroster_rosterstudent_session_roster'),
    ]

    operations = [
        migrations.CreateModel(
            name='ManualAttendanceRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('id_number', models.CharField(max_length=50)),
                ('full_name', models.CharField(blank=True, max_length=150)),
                ('status', models.CharField(choices=[('present', 'Present'), ('absent', 'Absent')], default='present', max_length=10)),
                ('marked_at', models.DateTimeField(auto_now=True)),
                ('marked_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='manual_attendance', to='scheduling.session')),
            ],
            options={
                'ordering': ['id_number'],
                'unique_together': {('session', 'id_number')},
            },
        ),
    ]
