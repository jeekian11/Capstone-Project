from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0008_alter_activitylog_action'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='course_year_section',
            field=models.CharField(
                blank=True, default='', max_length=100,
                help_text='Students only — e.g. "BSIS 4A". Shown when this student is picked as a requester.',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='department',
            field=models.CharField(
                blank=True, default='', max_length=100,
                help_text='Instructors only — e.g. "College of Computer Studies". Shown when this instructor is picked as a requester.',
            ),
        ),
    ]
