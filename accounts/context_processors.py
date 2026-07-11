# accounts/context_processors.py
from notifications.models import Notification
from issues.models import Issue

def sidebar_counts(request):
    if not request.user.is_authenticated:
        return {}
    return {
        'unread_notifications_count': Notification.objects.filter(
            user=request.user, read=False
        ).count(),
        'open_issues_count': Issue.objects.filter(status='open').count()
        if request.user.role in ['admin', 'incharge'] else 0,
    }