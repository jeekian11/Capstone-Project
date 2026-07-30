from django.contrib import admin
from .models import Notification, AlertSettings

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'notification_type', 'read', 'is_system_alert', 'created_at']
    list_filter = ['read', 'is_system_alert', 'notification_type']


@admin.register(AlertSettings)
class AlertSettingsAdmin(admin.ModelAdmin):
    list_display = ['__str__', 'updated_at']