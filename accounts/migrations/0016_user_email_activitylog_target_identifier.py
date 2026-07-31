from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0015_user_theme'),
    ]

    operations = [
        # Preserve existing ActivityLog history across the rename (a plain
        # remove+add would silently blank out every past log entry's target).
        migrations.RenameField(
            model_name='activitylog',
            old_name='target_username',
            new_name='target_identifier',
        ),
        migrations.AlterField(
            model_name='activitylog',
            name='target_identifier',
            field=models.CharField(
                max_length=150,
                help_text='Email (Admin/Lab In-Charge/Instructor) or Student/Instructor ID '
                           '(Student) of the account this log entry is about — kept as plain '
                           'text so the log entry survives even if the account is later '
                           'edited or deleted.',
            ),
        ),
        migrations.RemoveField(
            model_name='user',
            name='username',
        ),
        migrations.AddField(
            model_name='user',
            name='email',
            field=models.EmailField(
                blank=True, null=True, unique=True, max_length=254,
                verbose_name='email address',
                error_messages={'unique': 'A user with that email address already exists.'},
                help_text='Login credential for Admin, Lab In-Charge, and Instructor accounts. '
                           'Required and must be unique for those roles. Not used by Students — '
                           'they sign in at the lab PC with their Student ID Number and a '
                           'reservation code instead.',
            ),
        ),
        migrations.AlterField(
            model_name='user',
            name='id_number',
            field=models.CharField(
                blank=True, default='', max_length=50,
                help_text='Student/Instructor ID — used to match this account to reservation '
                           'requests and lab PC check-ins. For Students, this doubles as their '
                           'login credential at the lab PC (paired with a reservation code).',
            ),
        ),
    ]
