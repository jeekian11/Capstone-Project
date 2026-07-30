from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0012_inventoryitem_created_at_inventoryitem_updated_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='pcactivitylog',
            name='page_url',
            field=models.CharField(
                blank=True,
                default='',
                help_text=(
                    "The browser address bar's URL at the moment of capture, when the "
                    "agent could read it (requires the UI Automation dependency on the "
                    "PC — older/unsupported agents leave this blank and site detection "
                    "falls back to guessing from window_title)."
                ),
                max_length=500,
            ),
        ),
    ]
