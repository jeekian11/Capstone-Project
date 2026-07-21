# Generated for the pinned notification feature

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0003_alter_notification_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='pinned',
            field=models.BooleanField(default=False),
        ),
    ]
