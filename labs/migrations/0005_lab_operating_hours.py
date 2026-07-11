import datetime
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0004_inventoryitem_status_equipmentissue_maintenancelog'),
    ]

    operations = [
        migrations.AddField(
            model_name='lab',
            name='opening_time',
            field=models.TimeField(
                default=datetime.time(7, 0),
                help_text='Daily opening time, used as the capacity basis for utilization % in reports.',
            ),
        ),
        migrations.AddField(
            model_name='lab',
            name='closing_time',
            field=models.TimeField(
                default=datetime.time(21, 0),
                help_text='Daily closing time, used as the capacity basis for utilization % in reports.',
            ),
        ),
    ]
