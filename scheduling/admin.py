from django.contrib import admin
from .models import Session, SessionRequest

@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ['subject', 'instructor', 'lab', 'date', 'start_time', 'end_time']
    list_filter = ['lab', 'date']

@admin.register(SessionRequest)
class SessionRequestAdmin(admin.ModelAdmin):
    list_display = ['subject', 'instructor', 'lab', 'date', 'status']
    list_filter = ['status', 'lab']