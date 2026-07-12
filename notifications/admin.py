from django.contrib import admin
from .models import Notification

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'read', 'is_system_alert', 'created_at']
    list_filter = ['read', 'is_system_alert']