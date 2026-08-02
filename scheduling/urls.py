from django.urls import path
from . import views

urlpatterns = [
    # "Schedule & requests" now lands straight on the calendar (View
    # Schedule) instead of an intermediate hub page — Manage Requests is
    # just a button on this page now (see view_schedule.html), not a
    # separate stop. Both URL names still resolve so old links/bookmarks
    # to either one keep working.
    path('', views.ViewScheduleView.as_view(), name='schedule'),
    path('lab/', views.LabScheduleView.as_view(), name='lab_schedule'),
    path('instructor/', views.InstructorScheduleView.as_view(), name='inst_schedule'),

    # View schedule flow
    path('view/', views.ViewScheduleView.as_view(), name='view_schedule'),
    path('view/export/pdf/', views.export_schedule_pdf, name='view_schedule_export_pdf'),
    path('view/export/excel/', views.export_schedule_excel, name='view_schedule_export_excel'),
    path('session/<int:pk>/edit/', views.SessionUpdateView.as_view(), name='session_edit'),
    path('session/<int:pk>/delete/', views.delete_session, name='session_delete'),

    # Manage request flow
    path('requests/', views.ManageRequestsView.as_view(), name='manage_requests'),
    path('requests/new/', views.RequestCreateView.as_view(), name='request_new'),
    path('requests/new/mine/', views.InstructorRequestCreateView.as_view(), name='instructor_request_new'),
    path('requests/<int:pk>/', views.RequestDetailView.as_view(), name='request_detail'),
    path('requests/<int:pk>/edit/', views.RequestUpdateView.as_view(), name='request_edit'),
    path('requests/<int:pk>/export/', views.export_request_pdf, name='request_export_pdf'),
    path('request/<int:pk>/approve/', views.approve_request, name='approve_request'),
    path('request/<int:pk>/decline/', views.decline_request, name='decline_request'),

    # Class roster
    path('rosters/', views.RosterListView.as_view(), name='roster_list'),
    path('rosters/new/', views.RosterCreateView.as_view(), name='roster_create'),
    path('rosters/<int:pk>/', views.RosterDetailView.as_view(), name='roster_detail'),
    path('rosters/<int:pk>/edit/', views.RosterUpdateView.as_view(), name='roster_edit'),
    path('rosters/<int:pk>/archive/', views.roster_archive, name='roster_archive'),
    path('rosters/<int:pk>/approve/', views.roster_approve, name='roster_approve'),
    path('rosters/<int:pk>/reject/', views.roster_reject, name='roster_reject'),
    path('rosters/<int:pk>/generate-sessions/', views.roster_generate_sessions, name='roster_generate_sessions'),
    path('rosters/<int:pk>/delete/', views.RosterDeleteView.as_view(), name='roster_delete'),
    path('rosters/check-availability/', views.roster_check_availability, name='roster_check_availability'),
    path('rosters/<int:pk>/students/add/', views.roster_add_student, name='roster_add_student'),
    path('rosters/<int:pk>/students/import/', views.roster_import_students, name='roster_import_students'),
    path('rosters/<int:pk>/students/search/', views.roster_search_students, name='roster_search_students'),
    path('rosters/<int:pk>/students/<int:student_pk>/remove/', views.roster_remove_student, name='roster_remove_student'),
]
