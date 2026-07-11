from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0005_lab_operating_hours'),
        ('scheduling', '0002_session_requester_id_number_session_requester_name_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ClassRoster',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='e.g. "BSCS 2A — CS101 Programming Logic"', max_length=200)),
                ('subject', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('instructor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='class_rosters', to=settings.AUTH_USER_MODEL)),
                ('lab', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='class_rosters', to='labs.lab')),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='RosterStudent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('id_number', models.CharField(max_length=50)),
                ('full_name', models.CharField(max_length=150)),
                ('roster', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='students', to='scheduling.classroster')),
            ],
            options={'ordering': ['full_name'], 'unique_together': {('roster', 'id_number')}},
        ),
        migrations.AddField(
            model_name='session',
            name='roster',
            field=models.ForeignKey(
                blank=True, null=True,
                help_text='Optional — attach a class roster so attendance reports can show real present/absent names.',
                on_delete=django.db.models.deletion.SET_NULL, related_name='sessions', to='scheduling.classroster',
            ),
        ),
    ]
