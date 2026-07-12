from django.contrib import admin
from .models import Lab, PC, InventoryItem

@admin.register(Lab)
class LabAdmin(admin.ModelAdmin):
    list_display = ['name', 'location']

@admin.register(PC)
class PCAdmin(admin.ModelAdmin):
    list_display = ['pc_id', 'lab', 'status', 'current_user', 'last_active']
    list_filter = ['status', 'lab']

@admin.register(InventoryItem)
class InventoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'quantity', 'condition', 'lab']
    list_filter = ['condition', 'lab']