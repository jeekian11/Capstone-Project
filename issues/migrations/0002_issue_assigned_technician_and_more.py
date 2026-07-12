# Generated manually to match issues/models.py changes

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('issues', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='issue',
            name='assigned_technician',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='issues_assigned',
                to=settings.AUTH_USER_MODEL,
                help_text='Staff member (admin or lab in-charge) assigned to fix this issue.',
            ),
        ),
        migrations.AddField(
            model_name='issue',
            name='resolution_notes',
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name='issue',
            name='status',
            field=models.CharField(
                choices=[('open', 'Open'), ('assigned', 'Assigned'), ('in_progress', 'In Progress'), ('resolved', 'Resolved')],
                default='open', max_length=15,
            ),
        ),
    ]
