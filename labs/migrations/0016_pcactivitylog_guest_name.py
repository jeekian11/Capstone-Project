from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0015_alter_equipmentissue_id_alter_inventoryitem_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='pcactivitylog',
            name='guest_name',
            field=models.CharField(
                blank=True,
                default='',
                help_text=(
                    "Whoever pc.current_guest_name was at the moment this sample was "
                    "taken, for a Manual Unlock walk-in guest — there is no registered "
                    "account to attach via `student` for these, since Manual Unlock "
                    "access is never tied to a verified login. Blank for normal "
                    "logged-in/override sessions."
                ),
                max_length=150,
            ),
        ),
    ]
