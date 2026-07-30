from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0005_notification_lab_and_alertsettings'),
    ]

    operations = [
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
                    ('roster_session_scheduled', 'Class Roster Session Scheduled'),
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
    ]
