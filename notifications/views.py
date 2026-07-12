from django.views.generic import ListView, FormView
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.urls import reverse_lazy
from accounts.mixins import RoleRequiredMixin
from notifications.models import Notification
from notifications.forms import NotificationComposeForm, NotificationSettingsForm


# all notifications for current user
class NotificationsView(LoginRequiredMixin, ListView):
    template_name = 'notifications/notifications.html'
    context_object_name = 'notifications'

    def get_queryset(self):
        return Notification.objects.filter(
            user=self.request.user
        ).order_by('-created_at')


# instructor-specific notifications
class InstructorAlertsView(RoleRequiredMixin, ListView):
    allowed_roles = ['instructor']
    template_name = 'notifications/instructor_alerts.html'
    context_object_name = 'notifications'

    def get_queryset(self):
        return Notification.objects.filter(
            user=self.request.user
        ).order_by('-created_at')[:20]


# mark one notification as read
def mark_read(request, pk):
    notif = get_object_or_404(Notification, pk=pk, user=request.user)
    notif.read = True
    notif.save()
    return redirect(request.META.get('HTTP_REFERER', 'notifications'))


# mark all notifications as read
def mark_all_read(request):
    Notification.objects.filter(
        user=request.user, read=False
    ).update(read=True)
    return redirect('notifications')


# admin/incharge: compose and broadcast a notification to chosen recipients
class NotificationComposeView(RoleRequiredMixin, FormView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'notifications/compose.html'
    form_class = NotificationComposeForm
    success_url = reverse_lazy('notifications')

    def form_valid(self, form):
        title, message = form.build_title_and_message()
        recipients = form.resolve_recipients()
        n_type = form.cleaned_data['notification_type']

        count = 0
        for recipient in recipients:
            Notification.objects.create(
                user=recipient,
                title=title,
                message=message,
                notification_type=n_type,
            )
            count += 1
            if recipient.email_notifications_enabled and recipient.email:
                try:
                    send_mail(
                        title, message,
                        settings.DEFAULT_FROM_EMAIL,
                        [recipient.email],
                        fail_silently=True,
                    )
                except Exception:
                    pass  # a broken mail server should never block the notification itself

        messages.success(self.request, f'Notification Sent to {count} recipient{"s" if count != 1 else ""}.')
        return super().form_valid(form)


# any logged-in user: configure their own notification preferences
class NotificationSettingsView(LoginRequiredMixin, FormView):
    template_name = 'notifications/settings.html'
    form_class = NotificationSettingsForm
    success_url = reverse_lazy('notification_settings')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.success(self.request, 'Settings Updated.')
        return super().form_valid(form)