import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('scheduling', '0018_alter_classroster_id_alter_manualattendancerecord_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='sessioncheckin',
            name='checked_out_at',
            field=models.DateTimeField(blank=True, null=True, help_text='Set when this check-in was force-ended by an Admin/In-Charge Override before it ended naturally.'),
        ),
        migrations.AddField(
            model_name='sessioncheckin',
            name='ended_by',
            field=models.ForeignKey(blank=True, help_text='Admin/In-Charge who force-ended this session via Override, if applicable.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ended_checkins', to=settings.AUTH_USER_MODEL),
        ),
    ]
