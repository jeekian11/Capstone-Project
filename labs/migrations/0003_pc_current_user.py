from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0002_pc_ip_address'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='pc',
            name='current_user',
            field=models.ForeignKey(
                blank=True,
                null=True,
                help_text="The student currently signed in on this PC, set when they unlock it with their Student ID and password. Cleared once the PC goes offline or is reassigned.",
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='pcs_in_use',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
