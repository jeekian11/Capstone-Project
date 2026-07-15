from django.conf import settings
from django.db import models

class Lab(models.Model):
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=200, blank=True)
    opening_time = models.TimeField(
        default='07:00',
        help_text='Daily opening time, used as the capacity basis for utilization % in reports.'
    )
    closing_time = models.TimeField(
        default='21:00',
        help_text='Daily closing time, used as the capacity basis for utilization % in reports.'
    )

    def __str__(self):
        return self.name


class PC(models.Model):
    STATUS = [
        ('online', 'Online'),
        ('offline', 'Offline'),
        ('in_use', 'In Use'),
        ('issue', 'Has Issue'),
    ]
    lab = models.ForeignKey(Lab, on_delete=models.CASCADE, related_name='pcs')
    pc_id = models.CharField(max_length=10)
    ip_address = models.GenericIPAddressField(
        null=True, blank=True,
        help_text='Used to actually check whether this PC is reachable on the network.'
    )
    status = models.CharField(max_length=10, choices=STATUS, default='offline')
    last_active = models.DateTimeField(null=True, blank=True)
    current_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='pcs_in_use',
        help_text="The student currently signed in on this PC, set when they unlock it with their Student ID and password. Cleared once the PC goes offline or is reassigned.",
    )
    current_session = models.ForeignKey(
        'scheduling.Session',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        help_text="The reservation that most recently unlocked this PC. Used to auto re-lock the PC once that reservation's end_time passes, and cleared whenever the PC is locked again (by time running out or a manual lock).",
    )

    def __str__(self):
        return self.pc_id


class InventoryItem(models.Model):
    CONDITION = [
        ('good', 'Good'),
        ('fair', 'Fair'),
        ('needs_check', 'Needs Check'),
        ('faulty', 'Faulty'),
    ]
    STATUS = [
        ('operational', 'Operational'),
        ('under_repair', 'Under Repair'),
        ('retired', 'Retired'),
    ]
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=100)
    quantity = models.PositiveIntegerField()
    condition = models.CharField(max_length=20, choices=CONDITION)
    status = models.CharField(max_length=20, choices=STATUS, default='operational')
    lab = models.ForeignKey(Lab, on_delete=models.CASCADE)
    last_checked = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.name


class MaintenanceLog(models.Model):
    equipment = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='maintenance_logs')
    maintenance_date = models.DateField()
    assigned_technician = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='maintenance_tasks'
    )
    notes = models.TextField(blank=True)
    completed = models.BooleanField(default=False)
    completion_notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-maintenance_date']

    def __str__(self):
        return f'{self.equipment.name} — {self.maintenance_date}'


class EquipmentIssue(models.Model):
    STATUS = [
        ('open', 'Open'),
        ('resolved', 'Resolved'),
    ]
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='equipment_issues_reported'
    )
    equipment = models.ForeignKey(InventoryItem, on_delete=models.CASCADE, related_name='issues')
    description = models.TextField()
    date_reported = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS, default='open')
    resolution_notes = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-date_reported']

    def __str__(self):
        return f'{self.equipment.name} — {self.get_status_display()}'