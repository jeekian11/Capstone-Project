from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.views.generic import ListView, CreateView, UpdateView, DetailView
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse_lazy
from django.utils import timezone
from accounts.mixins import RoleRequiredMixin, ModalFormMixin, ModalDetailMixin, modal_redirect
from issues.models import Issue


# list all open issues
class IssuesView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'issues/issues.html'
    context_object_name = 'issues'

    def get_queryset(self):
        return Issue.objects.select_related(
            'pc', 'lab', 'reported_by'
        ).order_by('-created_at')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['open_count'] = Issue.objects.filter(status='open').count()
        ctx['in_progress_count'] = Issue.objects.filter(status='in_progress').count()
        ctx['resolved_today'] = Issue.objects.filter(
            status='resolved',
            resolved_at__date=timezone.now().date()
        ).count()
        return ctx


# view one issue in detail
class IssueDetailView(RoleRequiredMixin, ModalDetailMixin, DetailView):
    allowed_roles = ['admin', 'incharge']
    model = Issue
    template_name = 'issues/issue_detail.html'
    context_object_name = 'issue'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        User = get_user_model()
        ctx['technicians'] = User.objects.filter(role__in=['admin', 'incharge']).order_by('first_name', 'last_name')
        return ctx


# log a new issue
class IssueCreateView(RoleRequiredMixin, ModalFormMixin, CreateView):
    allowed_roles = ['admin', 'incharge', 'instructor']
    model = Issue
    fields = ['pc', 'lab', 'title', 'description', 'issue_type', 'priority']
    template_name = 'issues/issue_form.html'
    success_url = reverse_lazy('issues')

    def form_valid(self, form):
        form.instance.reported_by = self.request.user
        form.instance.status = 'open'
        # send alert to admin
        from notifications.models import Notification
        from django.contrib.auth import get_user_model
        User = get_user_model()
        admins = User.objects.filter(role='admin')
        for admin in admins:
            Notification.objects.create(
                user=admin,
                title=f'New issue: {form.instance.title}',
                message=f'Reported at {form.instance.lab} — {form.instance.pc}',
                is_system_alert=True,
            )
        messages.success(self.request, 'Issue logged.')
        return super().form_valid(form)


# update issue status (in progress, resolved)
class IssueUpdateView(RoleRequiredMixin, ModalFormMixin, UpdateView):
    allowed_roles = ['admin', 'incharge']
    model = Issue
    fields = ['status', 'priority', 'notes']
    template_name = 'issues/issue_form.html'
    success_url = reverse_lazy('issues')

    def form_valid(self, form):
        if form.instance.status == 'resolved':
            from django.utils import timezone
            form.instance.resolved_at = timezone.now()
        messages.success(self.request, 'Issue updated.')
        return super().form_valid(form)


# equipment monitor view
class EquipmentView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'issues/equipment.html'
    context_object_name = 'issues'

    def get_queryset(self):
        return Issue.objects.filter(
            status__in=['open', 'in_progress']
        ).select_related('pc', 'lab').order_by('priority')


# assign an issue to a technician (admin or incharge staff member)
def issue_assign(request, pk):
    if not request.user.is_authenticated or request.user.role not in ('admin', 'incharge'):
        raise PermissionDenied
    issue = get_object_or_404(Issue, pk=pk)
    if request.method == 'POST':
        User = get_user_model()
        technician_id = request.POST.get('assigned_technician')
        technician = User.objects.filter(pk=technician_id, role__in=['admin', 'incharge']).first()
        if technician:
            issue.assigned_technician = technician
            issue.status = 'assigned'
            issue.save(update_fields=['assigned_technician', 'status'])
            messages.success(request, f'Issue assigned to {technician.display_name}.')
        else:
            messages.error(request, 'Please select a valid technician.')
    return modal_redirect(request, 'issue_detail', pk=issue.pk)


# mark an issue resolved, with resolution notes
def issue_resolve(request, pk):
    if not request.user.is_authenticated or request.user.role not in ('admin', 'incharge'):
        raise PermissionDenied
    issue = get_object_or_404(Issue, pk=pk)
    if request.method == 'POST':
        resolution_notes = request.POST.get('resolution_notes', '').strip()
        issue.resolution_notes = resolution_notes
        issue.status = 'resolved'
        issue.resolved_at = timezone.now()
        issue.save(update_fields=['resolution_notes', 'status', 'resolved_at'])
        messages.success(request, 'Issue marked as resolved.')
    return modal_redirect(request, 'issue_detail', pk=issue.pk)