from django.urls import path
from . import views
from . import reports

urlpatterns = [
    path('pc-login/', views.ReservationPCLoginView.as_view(), name='pc_login'),
    path('api/pc-agent-login/', views.pc_agent_login_api, name='pc_agent_login_api'),
    path('api/pc-agent-info/', views.pc_agent_info_api, name='pc_agent_info_api'),
    path('api/pc-agent-activity/', views.pc_agent_activity_api, name='pc_agent_activity_api'),
    # These two views already existed in views.py but were never routed here —
    # every call the agent made to them (notify_end_session_sync /
    # submit_logout_in_background, both in lab_pc_agent/agent.py) was
    # silently 404ing. Net effect: pc.current_user/current_session were
    # never cleared when a session ended normally, so a PC kept showing
    # the PREVIOUS student as "current user" indefinitely — which then
    # caused pc_agent_activity_api to mis-attribute any later activity
    # (e.g. during a manual/remote unlock with no new login) to that
    # stale, no-longer-accurate student instead of skipping it.
    path('api/pc-agent-end-session/', views.pc_agent_end_session_api, name='pc_agent_end_session_api'),
    path('api/pc-agent-logout/', views.pc_agent_logout_api, name='pc_agent_logout_api'),
    # Called from lab_pc_agent/manual_unlock.html (the offline emergency
    # tool) once it can reach the server, to record a guest unlock that may
    # have happened while the server was down. See manual_unlock_log_api
    # for details.
    path('api/manual-unlock-log/', views.manual_unlock_log_api, name='manual_unlock_log_api'),
    path('', views.AdminDashboardView.as_view(), name='admin_dashboard'),
    path('pc-status/', views.PCStatusView.as_view(), name='pc_status'),
    path('pc-status/api/', views.pc_status_api, name='pc_status_api'),
    path('pc-status/refresh/', views.refresh_pc_status_view, name='pc_status_refresh'),
    path('pc-status/export/', reports.export_pc_status_report, name='pc_status_export'),
    path('pc-status/override-checkin/', views.OverrideCheckInView.as_view(), name='override_checkin'),
    path('pc-status/manual-unlock/', views.ManualUnlockView.as_view(), name='manual_unlock'),
    path('pc-activity/', views.PCActivityLogView.as_view(), name='pc_activity_log'),
    path('pc-activity/export/', views.export_pc_activity_log, name='export_pc_activity_log'),
    path('pc-activity/delete/', views.delete_pc_activity_log, name='delete_pc_activity_log'),
    path('pc/<int:pk>/update/', views.PCUpdateView.as_view(), name='pc_update'),
    path('inventory/', views.InventoryView.as_view(), name='inventory'),
    path('inventory/add/', views.InventoryCreateView.as_view(), name='inventory_create'),
    path('inventory/<int:pk>/edit/', views.InventoryUpdateView.as_view(), name='inventory_update'),
    path('inventory/<int:pk>/delete/', views.InventoryDeleteView.as_view(), name='inventory_delete'),
    path('inventory/bulk-delete/', views.inventory_bulk_delete, name='inventory_bulk_delete'),
    path('analytics/', views.AnalyticsView.as_view(), name='analytics'),
    path('analytics/export/', reports.export_analytics_overview, name='analytics_export'),
    path('alerts/', views.AlertsView.as_view(), name='alerts'),
    path('equipment/', views.EquipmentView.as_view(), name='lab_equipment'),

    path('manage/', views.LabListView.as_view(), name='lab_list'),
    path('manage/add/', views.LabCreateView.as_view(), name='lab_create'),
    path('manage/<int:pk>/edit/', views.LabUpdateView.as_view(), name='lab_update'),
    path('manage/<int:pk>/delete/', views.LabDeleteView.as_view(), name='lab_delete'),

    path('manage/pc/add/', views.PCCreateView.as_view(), name='pc_create'),
    path('manage/pc/import/', views.PCImportView.as_view(), name='pc_import'),
    path('manage/pc/<int:pk>/edit/', views.PCEditView.as_view(), name='pc_edit'),
    path('manage/pc/<int:pk>/delete/', views.PCDeleteView.as_view(), name='pc_delete'),

    path('inventory/<int:pk>/', views.InventoryDetailView.as_view(), name='inventory_detail'),
    path('inventory/status/', views.InventoryStatusListView.as_view(), name='inventory_status_list'),
    path('inventory/<int:pk>/status/', views.inventory_status_update, name='inventory_status_update'),

    path('maintenance/', views.MaintenanceLogListView.as_view(), name='maintenance_logs'),
    path('maintenance/schedule/', views.MaintenanceScheduleCreateView.as_view(), name='maintenance_schedule'),
    path('maintenance/<int:pk>/complete/', views.maintenance_complete, name='maintenance_complete'),

    path('repair-history/', views.RepairHistoryView.as_view(), name='repair_history'),

    path('equipment-issues/', views.EquipmentIssueListView.as_view(), name='equipment_issues'),
    path('equipment-issues/report/', views.EquipmentIssueCreateView.as_view(), name='equipment_issue_report'),
    path('equipment-issues/<int:pk>/', views.EquipmentIssueDetailView.as_view(), name='equipment_issue_detail'),
    path('equipment-issues/<int:pk>/resolve/', views.equipment_issue_resolve, name='equipment_issue_resolve'),

    # reporting & analytics
    path('reports/lab-utilization/', reports.LabUtilizationReportView.as_view(), name='report_lab_utilization'),
    path('reports/lab-utilization/export/', reports.export_lab_utilization, name='report_lab_utilization_export'),
    path('reports/equipment-status/', reports.EquipmentStatusReportView.as_view(), name='report_equipment_status'),
    path('reports/equipment-status/export/', reports.export_equipment_status, name='report_equipment_status_export'),
    path('reports/instructor-usage/', reports.InstructorUsageReportView.as_view(), name='report_instructor_usage'),
    path('reports/instructor-usage/export/', reports.export_instructor_usage, name='report_instructor_usage_export'),
    path('reports/attendance/', reports.AttendanceReportView.as_view(), name='report_attendance'),
    path('reports/attendance/export/', reports.export_attendance, name='report_attendance_export'),
    path('reports/maintenance/', reports.MaintenanceReportView.as_view(), name='report_maintenance'),
    path('reports/maintenance/export/', reports.export_maintenance, name='report_maintenance_export'),
]