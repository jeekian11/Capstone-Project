from django.contrib import admin
from .models import Issue

@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ['title', 'lab', 'pc', 'priority', 'status', 'created_at']
    list_filter = ['status', 'priority', 'lab']