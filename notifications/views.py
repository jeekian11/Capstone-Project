from django.views.generic import ListView, FormView
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.urls import reverse_lazy
from accounts.mixins import RoleRequiredMixin, ModalFormMixin
from notifications.models import Notification, AlertSettings
from notifications.forms import NotificationComposeForm, AlertSettingsForm
from notifications.filters import apply_filters, stat_counts, CATEGORY_CHOICES


# all notifications for current user (generic feed — Admin/In-Charge use
# the richer labs.AlertsView "Notifications & Alerts" page instead, see
# labs/views.py AlertsView + templates/labs/alerts.html)
class NotificationsView(LoginRequiredMixin, ListView):
    template_name = 'notifications/notifications.html'
    context_object_name = 'notifications'

    def get_base_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    def get_queryset(self):
        return apply_filters(self.get_base_queryset(), self.request)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['stats'] = stat_counts(self.get_base_queryset())
        ctx['category_choices'] = CATEGORY_CHOICES
        ctx['selected_category'] = self.request.GET.get('category', 'all')
        ctx['q'] = self.request.GET.get('q', '')
        ctx['sort'] = self.request.GET.get('sort', 'latest')
        return ctx


# instructor-specific notifications — instructors only ever see notifications
# tied to their own reservations/sessions, since those are the only ones
# ever addressed to them (see notifications/services.py)
class InstructorAlertsView(RoleRequiredMixin, ListView):
    allowed_roles = ['instructor']
    template_name = 'notifications/instructor_alerts.html'
    context_object_name = 'notifications'

    def get_base_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    def get_queryset(self):
        return apply_filters(self.get_base_queryset(), self.request)[:50]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['stats'] = stat_counts(self.get_base_queryset())
        ctx['category_choices'] = CATEGORY_CHOICES
        ctx['selected_category'] = self.request.GET.get('category', 'all')
        ctx['q'] = self.request.GET.get('q', '')
        ctx['sort'] = self.request.GET.get('sort', 'latest')
        return ctx


# mark one notification as read
def mark_read(request, pk):
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.read = True
    notif.save()
    return redirect(request.META.get('HTTP_REFERER', 'notifications'))


# mark one notification as unread
def mark_unread(request, pk):
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.read = False
    notif.save()
    return redirect(request.META.get('HTTP_REFERER', 'notifications'))


# mark all notifications as read
def mark_all_read(request):
    Notification.objects.filter(
        user=request.user, read=False
    ).update(read=True)
    return redirect(request.META.get('HTTP_REFERER', 'notifications'))


# pin/unpin a notification so it stays at the top of the list
def toggle_pin(request, pk):
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.pinned = not notif.pinned
    notif.save()
    return redirect(request.META.get('HTTP_REFERER', 'notifications'))


# delete a single notification — Admin can delete any notification in the
# system (moderation); everyone else may only delete their own.
def delete_notification(request, pk):
    if request.user.role == 'admin':
        notif = get_object_or_404(Notification, pk=pk)
    else:
        notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.delete()
    return redirect(request.META.get('HTTP_REFERER', 'notifications'))


# bulk-delete one or more checked notifications from the "Delete Selected"
# toolbar on the Notifications dashboard — same per-row rule as
# delete_notification above (own notifications, or any notification if
# admin), just applied to however many rows were checked.
def notifications_bulk_delete(request):
    if request.method != 'POST':
        return redirect(request.META.get('HTTP_REFERER', 'notifications'))
    ids = [v for v in request.POST.getlist('selected_ids') if v]
    if request.user.role == 'admin':
        qs = Notification.objects.filter(pk__in=ids)
    else:
        qs = Notification.objects.filter(pk__in=ids, user=request.user)
    count = qs.count()
    qs.delete()
    if count:
        messages.success(request, f'Deleted {count} notification{"s" if count != 1 else ""}.')
    else:
        messages.error(request, 'No notifications were selected.')
    return redirect(request.META.get('HTTP_REFERER', 'notifications'))


# admin/incharge: compose and broadcast a manual notification to chosen
# recipients (announcements, schedule changes, closure notices, etc.) —
# manual notifications always send regardless of the Alert Settings
# toggles, since a human explicitly chose to send them.
class NotificationComposeView(RoleRequiredMixin, ModalFormMixin, FormView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'notifications/compose.html'
    form_class = NotificationComposeForm

    def get_success_url(self):
        return reverse_lazy('alerts') if self.request.user.role in ('admin', 'incharge') else reverse_lazy('notifications')

    def form_valid(self, form):
        title, message = form.build_title_and_message()
        recipients = form.resolve_recipients()
        n_type = form.cleaned_data['notification_type']

        count = 0
        for recipient in recipients:
            # Don't notify the sender about their own broadcast — this used
            # to leave admins with a stuck "unread" badge for a notification
            # that never showed up anywhere they could read/dismiss it.
            if recipient.pk == self.request.user.pk:
                continue
            Notification.objects.create(
                user=recipient,
                title=title,
                message=message,
                notification_type=n_type,
            )
            count += 1

        messages.success(self.request, f'Notification sent to {count} recipient{"s" if count != 1 else ""}.')
        return super().form_valid(form)


# Admin-only: turn categories of auto-generated notifications on/off.
class NotificationSettingsView(RoleRequiredMixin, ModalFormMixin, FormView):
    allowed_roles = ['admin']
    template_name = 'notifications/settings.html'
    form_class = AlertSettingsForm
    success_url = reverse_lazy('notification_settings')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = AlertSettings.get_solo()
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Alert settings updated.')
        return super().form_valid(form)
