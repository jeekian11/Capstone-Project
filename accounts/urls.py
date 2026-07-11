from django.urls import path
from . import views

urlpatterns = [
    path('redirect/', views.role_redirect, name='role_redirect'),
    path('dashboard/incharge/', views.InchargeDashboardView.as_view(), name='incharge_dashboard'),
    path('dashboard/instructor/', views.InstructorDashboardView.as_view(), name='instructor_dashboard'),
    path('users/', views.UsersListView.as_view(), name='users'),
    path('users/add/', views.UserCreateView.as_view(), name='user_create'),
    path('users/<int:pk>/edit/', views.UserUpdateView.as_view(), name='user_update'),
    path('users/<int:pk>/activate/', views.user_activate, name='user_activate'),
    path('users/<int:pk>/deactivate/', views.user_deactivate, name='user_deactivate'),
    path('users/<int:pk>/delete/', views.UserDeleteView.as_view(), name='user_delete'),
    path('users/<int:pk>/reset-password/', views.UserResetPasswordView.as_view(), name='user_reset_password'),
    path('logs/', views.LogHistoryView.as_view(), name='log_history'),
    path('logs/export/', views.export_logs_pdf, name='log_export_pdf'),
    path('attendance/', views.AttendanceView.as_view(), name='attendance'),
    path('attendance/summary/export/', views.attendance_summary_export, name='attendance_summary_export'),
    path('attendance/session/<int:pk>/mark/', views.session_attendance_mark, name='session_attendance_mark'),
]