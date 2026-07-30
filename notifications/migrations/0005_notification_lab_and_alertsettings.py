from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('labs', '0001_initial'),
        ('notifications', '0004_notification_pinned'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='lab',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='notifications', to='labs.lab',
            ),
        ),
        migrations.AlterField(
            model_name='notification',
            name='notification_type',
            field=models.CharField(
                choices=[
                    ('booking_confirmation', 'Booking Confirmation'),
                    ('schedule_reminder', 'Schedule Reminder'),
                    ('maintenance_alert', 'Maintenance Alert'),
                    ('system_announcement', 'System Announcement'),
                    ('general', 'General'),
                    ('reservation_submitted', 'New Reservation Request'),
                    ('reservation_approved', 'Reservation Approved'),
                    ('reservation_declined', 'Reservation Rejected'),
                    ('reservation_conflict', 'Reservation Conflict'),
                    ('walkin_override_approved', 'Walk-in/Override Approved'),
                    ('session_reminder', 'Session Starting Soon'),
                    ('pc_offline', 'PC Offline'),
                    ('pc_online', 'PC Back Online'),
                    ('pc_maintenance', 'PC Needs Maintenance'),
                    ('maintenance_submitted', 'Maintenance Request Submitted'),
                    ('maintenance_completed', 'Maintenance Completed'),
                    ('login_security_alert', 'Failed Login Attempts'),
                ],
                default='general', max_length=30,
            ),
        ),
        migrations.AlterModelOptions(
            name='notification',
            options={'ordering': ['-pinned', '-created_at']},
        ),
        migrations.CreateModel(
            name='AlertSettings',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reservation_notifications', models.BooleanField(default=True, help_text='New reservation requests, approvals/rejections, walk-in/override approvals, and reservation conflicts.')),
                ('pc_status_alerts', models.BooleanField(default=True, help_text='A lab PC goes offline unexpectedly, or comes back online.')),
                ('maintenance_alerts', models.BooleanField(default=True, help_text='A PC/equipment is flagged for maintenance, and maintenance submitted/completed updates.')),
                ('login_security_alerts', models.BooleanField(default=True, help_text='Multiple failed login attempts detected on an account.')),
                ('session_reminders', models.BooleanField(default=True, help_text='A laboratory session is about to start (10-15 minutes before).')),
                ('system_announcements', models.BooleanField(default=True, help_text='Manual announcements composed by Admin/Lab In-Charge always send regardless; this toggle is reserved for future auto-echoed system announcements.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Alert Settings',
                'verbose_name_plural': 'Alert Settings',
            },
        ),
    ]
