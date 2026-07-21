from django.db import models
from django.conf import settings

class Notification(models.Model):
    TYPE_CHOICES = [
        ('booking_confirmation', 'Booking Confirmation'),
        ('schedule_reminder', 'Schedule Reminder'),
        ('maintenance_alert', 'Maintenance Alert'),
        ('system_announcement', 'System Announcement'),
        ('general', 'General'),
    ]
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    notification_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default='general')
    read = models.BooleanField(default=False)
    pinned = models.BooleanField(default=False)
    is_system_alert = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title