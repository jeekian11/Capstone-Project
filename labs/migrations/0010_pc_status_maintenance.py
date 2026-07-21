from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0009_pcactivitylog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pc',
            name='status',
            field=models.CharField(
                choices=[
                    ('online', 'Available'),
                    ('offline', 'Offline'),
                    ('in_use', 'In Use'),
                    ('maintenance', 'Under Maintenance'),
                    ('issue', 'Has Issue'),
                ],
                default='offline',
                max_length=11,
            ),
        ),
    ]
