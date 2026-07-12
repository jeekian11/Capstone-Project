from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0004_inventoryitem_status_equipmentissue_maintenancelog'),
        ('accounts', '0004_alter_activitylog_action_alter_user_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='activitylog',
            name='pc',
            field=models.ForeignKey(
                blank=True,
                null=True,
                help_text='The lab PC this event happened on, set for pc_unlock events. Lets us answer "which PC was used on date X" reliably.',
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='usage_logs',
                to='labs.pc',
            ),
        ),
    ]
