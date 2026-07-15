from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0006_requester_type_group_choice'),
    ]

    operations = [
        migrations.CreateModel(
            name='SessionCheckIn',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('id_number', models.CharField(max_length=50)),
                ('checked_in_at', models.DateTimeField(auto_now_add=True)),
                ('session', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='check_ins', to='scheduling.session')),
            ],
            options={
                'ordering': ['checked_in_at'],
                'unique_together': {('session', 'id_number')},
            },
        ),
    ]
