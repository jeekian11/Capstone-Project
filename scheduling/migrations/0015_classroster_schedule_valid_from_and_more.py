from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0014_sessioncheckin_guest_name_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='classroster',
            name='schedule_valid_from',
            field=models.DateField(
                blank=True, null=True,
                help_text='First date this weekly schedule applies from (e.g. the start of the semester).',
            ),
        ),
        migrations.AddField(
            model_name='classroster',
            name='schedule_valid_until',
            field=models.DateField(
                blank=True, null=True,
                help_text='Last date this weekly schedule applies to (e.g. the end of the semester).',
            ),
        ),
        migrations.AlterField(
            model_name='classroster',
            name='schedule',
            field=models.CharField(
                blank=True, max_length=200,
                help_text='Auto-generated from the selected day(s), time range, and validity period.',
            ),
        ),
    ]
