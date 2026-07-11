from django.urls import path
from . import views

urlpatterns = [
    path('', views.IssuesView.as_view(), name='issues'),
    path('add/', views.IssueCreateView.as_view(), name='issue_create'),
    path('equipment/', views.EquipmentView.as_view(), name='equipment'),
    path('<int:pk>/', views.IssueDetailView.as_view(), name='issue_detail'),
    path('<int:pk>/edit/', views.IssueUpdateView.as_view(), name='issue_update'),
    path('<int:pk>/assign/', views.issue_assign, name='issue_assign'),
    path('<int:pk>/resolve/', views.issue_resolve, name='issue_resolve'),
]