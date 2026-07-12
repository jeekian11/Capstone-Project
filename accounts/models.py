from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models

class User(AbstractUser):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('incharge', 'Lab In-charge'),
        ('instructor', 'Instructor'),
        ('student', 'Student'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='instructor')
    id_number = models.CharField(
        max_length=50, blank=True, default='',
        help_text='Student/Instructor ID — used to match this account to reservation requests and lab PC check-ins. Separate from the login username.'
    )
    assigned_lab = models.ForeignKey(
        'labs.Lab',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_users'
    )
    email_notifications_enabled = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.get_full_name()} ({self.role})'


class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('register', 'User Registered'),
        ('activate', 'Account Activated'),
        ('deactivate', 'Account Deactivated'),
        ('delete', 'User Deleted'),
        ('reset_password', 'Password Reset'),
        ('pc_unlock', 'Lab PC Unlocked'),
    ]
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='actions_performed'
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    target_username = models.CharField(max_length=150)
    details = models.TextField(blank=True)
    pc = models.ForeignKey(
        'labs.PC',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='usage_logs',
        help_text='The lab PC this event happened on, set for pc_unlock events. Lets us answer "which PC was used on date X" reliably.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_action_display()} — {self.target_username}'