from django.contrib import admin
from .models import Lab, PC, InventoryItem, PCActivityLog

@admin.register(Lab)
class LabAdmin(admin.ModelAdmin):
    list_display = ['name', 'location']

@admin.register(PC)
class PCAdmin(admin.ModelAdmin):
    list_display = ['pc_id', 'lab', 'status', 'current_user', 'last_active']
    list_filter = ['status', 'lab']

@admin.register(PCActivityLog)
class PCActivityLogAdmin(admin.ModelAdmin):
    list_display = ['pc', 'student', 'window_title', 'page_url', 'captured_at']
    list_filter = ['pc__lab', 'pc']
    search_fields = ['window_title', 'page_url', 'student__id_number', 'student__first_name', 'student__last_name']
    ordering = ['-captured_at']

@admin.register(InventoryItem)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'quantity', 'condition', 'lab']
    list_filter = ['condition', 'lab']