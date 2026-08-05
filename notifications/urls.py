from django.urls import path
from . import views

urlpatterns = [
    path('', views.NotificationsView.as_view(), name='notifications'),
    path('instructor/', views.InstructorAlertsView.as_view(), name='inst_alerts'),
    path('<int:pk>/read/', views.mark_read, name='mark_read'),
    path('<int:pk>/unread/', views.mark_unread, name='mark_unread'),
    path('<int:pk>/pin/', views.toggle_pin, name='toggle_pin'),
    path('<int:pk>/delete/', views.delete_notification, name='delete_notification'),
    path('bulk-delete/', views.notifications_bulk_delete, name='notifications_bulk_delete'),
    path('read-all/', views.mark_all_read, name='mark_all_read'),
    path('compose/', views.NotificationComposeView.as_view(), name='notification_compose'),
    path('settings/', views.NotificationSettingsView.as_view(), name='notification_settings'),
]