from django.db.models import Q
from django.utils import timezone
from notifications.models import Notification

CATEGORY_CHOICES = [
    ('all', 'All Notifications'),
    (Notification.CRITICAL, 'Critical'),
    (Notification.WARNING, 'Warning'),
    (Notification.INFO, 'Information'),
    (Notification.SUCCESS, 'Success'),
]


def apply_filters(qs, request):
    """Applies ?category=, ?q= and ?sort= query params, in that order, to a
    Notification queryset. Shared so every notifications list page (Admin's
    Notifications & Alerts, the instructor feed, etc.) filters the same way."""
    category = request.GET.get('category', 'all')
    if category in dict(CATEGORY_CHOICES) and category != 'all':
        qs = qs.filter(notification_type__in=Notification.types_for_category(category))

    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(message__icontains=q))

    sort = request.GET.get('sort', 'latest')
    if sort == 'oldest':
        qs = qs.order_by('created_at')
    else:
        qs = qs.order_by('-pinned', '-created_at')
    return qs


def stat_counts(base_qs):
    """Unread / Critical / Today / Total counts for the summary cards,
    computed off the UNFILTERED base queryset (so the cards always reflect
    the whole inbox, not just whatever filter/search is currently applied)."""
    today = timezone.localdate()
    return {
        'unread': base_qs.filter(read=False).count(),
        'critical': base_qs.filter(notification_type__in=Notification.types_for_category(Notification.CRITICAL)).count(),
        'today': base_qs.filter(created_at__date=today).count(),
        'total': base_qs.count(),
    }
