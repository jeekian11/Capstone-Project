from django.views.generic import TemplateView, ListView, UpdateView, CreateView, DeleteView, FormView
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404, render
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.urls import reverse_lazy
from django.utils import timezone
from accounts.mixins import RoleRequiredMixin
from labs.models import PC, Lab, InventoryItem, MaintenanceLog, EquipmentIssue
from labs.network import refresh_pc_statuses
from labs.forms import ReservationPCLoginForm, MaintenanceScheduleForm, LabForm, InventoryItemForm


# admin main dashboard
class AdminDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin']
    template_name = 'dashboard/admin.html'

    def get_context_data(self, **kwargs):
        from issues.models import Issue
        from scheduling.models import Session
        from notifications.models import Notification
        ctx = super().get_context_data(**kwargs)
        ctx['pcs'] = PC.objects.select_related('lab').all()
        ctx['online_count'] = PC.objects.filter(status='online').count()
        ctx['offline_count'] = PC.objects.filter(status='offline').count()
        ctx['open_issues_count'] = Issue.objects.filter(status='open').count()
        ctx['todays_sessions'] = Session.objects.filter(
            date=timezone.now().date()
        ).select_related('instructor', 'lab')
        ctx['todays_sessions_count'] = ctx['todays_sessions'].count()
        ctx['recent_alerts'] = Notification.objects.filter(
            is_system_alert=True
        ).order_by('-created_at')[:5]
        return ctx


# the lock screen shown on a lab computer itself — the person enters the
# Student/Instructor ID and reservation code that the Admin/Lab In-Charge
# gave them when their walk-in request was approved. This is deliberately
# NOT behind RoleRequiredMixin/LoginRequiredMixin: it's the page someone
# sees before they're identified at all, and it never grants access to the
# web management system, only to the physical PC it's running on.
class ReservationPCLoginView(FormView):
    template_name = 'labs/pc_login.html'
    form_class = ReservationPCLoginForm

    def _resolve_pc(self):
        ip = self.request.META.get('REMOTE_ADDR')
        return PC.objects.filter(ip_address=ip).select_related('lab').first()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['pc'] = self._resolve_pc()
        return ctx

    def form_valid(self, form):
        from accounts.models import ActivityLog
        from labs.network import unlock_pc
        from scheduling.models import Session

        id_number = form.cleaned_data['id_number'].strip()
        code = form.cleaned_data['reservation_code'].strip()
        pc = self._resolve_pc()

        if pc is None:
            form.add_error(None, "This computer isn't registered in the system yet. Ask your Laboratory In-Charge to add it before it can be unlocked.")
            return self.form_invalid(form)

        session = Session.objects.filter(
            reservation_code__iexact=code,
            requester_id_number__iexact=id_number,
            lab=pc.lab,
        ).select_related('lab', 'instructor').first()

        if session is None:
            form.add_error(None, 'No matching reservation for this lab. Check your ID and reservation code, or ask your Lab In-Charge.')
            return self.form_invalid(form)

        if session.instructor is None:
            form.add_error(
                None,
                'Your reservation was found, but this ID isn\u2019t linked to a registered account yet. '
                'Ask your Admin or Lab In-Charge to register your account before you can log in here.'
            )
            return self.form_invalid(form)

        if not session.instructor.is_active:
            form.add_error(None, 'Your account has been deactivated. Ask your Admin or Lab In-Charge for assistance.')
            return self.form_invalid(form)

        now = timezone.localtime()
        if session.date != now.date():
            form.add_error(None, f"This reservation is for {session.date.strftime('%B %d, %Y')}, not today.")
            return self.form_invalid(form)
        if not (session.start_time <= now.time() <= session.end_time):
            form.add_error(
                None,
                f"This reservation is only valid from {session.start_time.strftime('%H:%M')} to {session.end_time.strftime('%H:%M')}."
            )
            return self.form_invalid(form)

        success, detail = unlock_pc(pc)

        pc.status = 'in_use'
        pc.last_active = timezone.now()
        pc.current_user = session.instructor
        pc.save(update_fields=['status', 'last_active', 'current_user'])

        ActivityLog.objects.create(
            actor=session.instructor,
            action='pc_unlock',
            target_username=id_number,
            pc=pc,
            details=(
                f"{session.requester_name} ({id_number}) checked in with reservation {session.reservation_code} — unlocked {pc.pc_id} ({pc.lab.name})."
                if success else
                f"{session.requester_name} ({id_number}) was verified for {pc.pc_id} ({pc.lab.name}) via reservation {session.reservation_code}, but the unlock command didn't run: {detail}"
            )
        )

        if success:
            messages.success(self.request, f'Welcome, {session.requester_name}. Unlocking {pc.pc_id}...')
        else:
            messages.warning(self.request, f'You were verified, but {pc.pc_id} could not be unlocked automatically. Ask your Laboratory In-Charge for help.')

        return redirect('pc_login')


# shows all PCs with their current status
class PCStatusView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/pc_status.html'

    def get_context_data(self, **kwargs):
        from django.conf import settings
        from django.db.models import Prefetch, Q
        from accounts.models import ActivityLog
        ctx = super().get_context_data(**kwargs)

        lab_id = self.request.GET.get('lab', '')
        day = self.request.GET.get('day', '')
        q = self.request.GET.get('q', '').strip()

        ctx['all_labs'] = Lab.objects.order_by('name')
        ctx['selected_lab'] = lab_id
        ctx['selected_day'] = day
        ctx['search_query'] = q
        ctx['filtered'] = bool(lab_id or day or q)
        ctx['day_mode'] = bool(day)

        if day:
            # "Day" here means "which PCs were actually used on that date" —
            # PC.last_active gets overwritten by every later health-check/login,
            # so it can't answer that for a past date. The pc_unlock ActivityLog
            # entries are the real per-day usage history, so use those instead.
            logs = ActivityLog.objects.filter(
                action='pc_unlock', pc__isnull=False, created_at__date=day
            ).select_related('pc', 'pc__lab', 'actor').order_by('pc__lab__name', 'pc__pc_id', 'created_at')

            if lab_id:
                logs = logs.filter(pc__lab_id=lab_id)
            if q:
                logs = logs.filter(
                    Q(pc__pc_id__icontains=q) |
                    Q(target_username__icontains=q) |
                    Q(actor__first_name__icontains=q) |
                    Q(actor__last_name__icontains=q) |
                    Q(actor__username__icontains=q)
                )

            labs_usage = {}
            for log in logs:
                lab = log.pc.lab
                labs_usage.setdefault(lab, []).append(log)

            ctx['labs_usage'] = [
                {'lab': lab, 'logs': entries} for lab, entries in labs_usage.items()
            ]
            ctx['total_filtered'] = logs.count()
            ctx['pcs'] = PC.objects.select_related('lab').all()
            ctx['online_count'] = PC.objects.filter(status='online').count()
            ctx['offline_count'] = PC.objects.filter(status='offline').count()
            ctx['in_use_count'] = PC.objects.filter(status='in_use').count()
            ctx['issue_count'] = PC.objects.filter(status='issue').count()
            return ctx

        pc_qs = PC.objects.select_related('current_user').order_by('pc_id')
        if q:
            pc_qs = pc_qs.filter(
                Q(pc_id__icontains=q) |
                Q(current_user__first_name__icontains=q) |
                Q(current_user__last_name__icontains=q) |
                Q(current_user__username__icontains=q)
            )
        if lab_id:
            pc_qs_scoped = pc_qs.filter(lab_id=lab_id)
        else:
            pc_qs_scoped = pc_qs
        ctx['total_filtered'] = pc_qs_scoped.count()

        labs_qs = Lab.objects.order_by('name')
        if lab_id:
            labs_qs = labs_qs.filter(pk=lab_id)
        labs_qs = labs_qs.prefetch_related(Prefetch('pcs', queryset=pc_qs, to_attr='filtered_pcs'))

        ctx['labs'] = labs_qs
        ctx['pcs'] = PC.objects.select_related('lab').all()
        ctx['online_count'] = PC.objects.filter(status='online').count()
        ctx['offline_count'] = PC.objects.filter(status='offline').count()
        ctx['in_use_count'] = PC.objects.filter(status='in_use').count()
        ctx['issue_count'] = PC.objects.filter(status='issue').count()
        ctx['auto_check_interval_seconds'] = getattr(settings, 'PC_STATUS_CHECK_INTERVAL_SECONDS', 60)
        return ctx


# API endpoint — returns live PC status as JSON for auto-refresh
def pc_status_api(request):
    pcs = PC.objects.select_related('current_user').values(
        'pc_id', 'status', 'lab__name', 'last_active',
        'current_user__username', 'current_user__first_name', 'current_user__last_name',
    )
    return JsonResponse({'pcs': list(pcs)})


# update a single PC's status (admin/incharge action)
class PCUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = ['admin', 'incharge']
    model = PC
    fields = ['status']
    template_name = 'labs/pc_form.html'

    def get_success_url(self):
        return '/labs/pc-status/'


# actually pings every PC that has an IP address on file and updates their
# real status — this is the "talk to the PCs" action
@login_required
def refresh_pc_status_view(request):
    if request.user.role not in ['admin', 'incharge']:
        raise PermissionDenied
    pcs = PC.objects.exclude(ip_address__isnull=True)
    if request.user.role == 'incharge' and request.user.assigned_lab:
        pcs = pcs.filter(lab=request.user.assigned_lab)
    summary = refresh_pc_statuses(pcs)
    total_with_ip = pcs.count()
    if total_with_ip == 0:
        messages.warning(request, "No PCs have an IP address on file yet — add one in each PC's settings so its real status can be checked.")
    else:
        messages.success(
            request,
            f"Checked {summary['checked']} PC(s): {summary['online']} online, {summary['offline']} offline."
        )
    return redirect(request.META.get('HTTP_REFERER', 'pc_status'))


# ---- Lab management (create/edit/delete — no more hardcoded/admin-only setup) ----
class LabListView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin']
    template_name = 'labs/lab_list.html'
    context_object_name = 'labs'

    def get_queryset(self):
        return Lab.objects.prefetch_related('pcs').order_by('name')


class LabCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin']
    model = Lab
    form_class = LabForm
    template_name = 'labs/lab_form.html'
    success_url = reverse_lazy('lab_list')

    def form_valid(self, form):
        messages.success(self.request, f'Lab "{form.instance.name}" created.')
        return super().form_valid(form)


class LabUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = ['admin']
    model = Lab
    form_class = LabForm
    template_name = 'labs/lab_form.html'
    success_url = reverse_lazy('lab_list')

    def form_valid(self, form):
        messages.success(self.request, f'Lab "{form.instance.name}" updated.')
        return super().form_valid(form)


class LabDeleteView(RoleRequiredMixin, DeleteView):
    allowed_roles = ['admin']
    model = Lab
    template_name = 'labs/lab_confirm_delete.html'
    success_url = reverse_lazy('lab_list')
    context_object_name = 'lab'

    def form_valid(self, form):
        name = self.object.name
        response = super().form_valid(form)
        messages.success(self.request, f'Lab "{name}" deleted.')
        return response


# ---- PC management (create/edit/delete, including the IP address used for real status checks) ----
class PCCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin']
    model = PC
    fields = ['lab', 'pc_id', 'ip_address', 'status']
    template_name = 'labs/pc_edit_form.html'
    success_url = reverse_lazy('lab_list')

    def get_initial(self):
        initial = super().get_initial()
        lab_id = self.request.GET.get('lab')
        if lab_id:
            initial['lab'] = lab_id
        return initial

    def form_valid(self, form):
        messages.success(self.request, f'PC "{form.instance.pc_id}" added.')
        return super().form_valid(form)


class PCEditView(RoleRequiredMixin, UpdateView):
    allowed_roles = ['admin']
    model = PC
    fields = ['lab', 'pc_id', 'ip_address', 'status']
    template_name = 'labs/pc_edit_form.html'
    success_url = reverse_lazy('lab_list')

    def form_valid(self, form):
        messages.success(self.request, f'PC "{form.instance.pc_id}" updated.')
        return super().form_valid(form)


class PCDeleteView(RoleRequiredMixin, DeleteView):
    allowed_roles = ['admin']
    model = PC
    template_name = 'labs/pc_confirm_delete.html'
    success_url = reverse_lazy('lab_list')
    context_object_name = 'pc'

    def form_valid(self, form):
        pc_id = self.object.pc_id
        response = super().form_valid(form)
        messages.success(self.request, f'PC "{pc_id}" removed.')
        return response


# ---- Inventory management (create/edit/delete) ----
class InventoryCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin']
    model = InventoryItem
    form_class = InventoryItemForm
    template_name = 'labs/inventory_form.html'
    success_url = reverse_lazy('inventory')

    def form_valid(self, form):
        messages.success(self.request, f'"{form.instance.name}" added to inventory.')
        return super().form_valid(form)


class InventoryUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = ['admin']
    model = InventoryItem
    form_class = InventoryItemForm
    template_name = 'labs/inventory_form.html'
    success_url = reverse_lazy('inventory')

    def form_valid(self, form):
        messages.success(self.request, f'"{form.instance.name}" updated.')
        return super().form_valid(form)


class InventoryDeleteView(RoleRequiredMixin, DeleteView):
    allowed_roles = ['admin']
    model = InventoryItem
    template_name = 'labs/inventory_confirm_delete.html'
    success_url = reverse_lazy('inventory')
    context_object_name = 'item'

    def form_valid(self, form):
        name = self.object.name
        response = super().form_valid(form)
        messages.success(self.request, f'"{name}" removed from inventory.')
        return response


# inventory list
class InventoryView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/inventory.html'
    context_object_name = 'items'

    def get_queryset(self):
        return InventoryItem.objects.select_related('lab').order_by('lab', 'category')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['total_items'] = InventoryItem.objects.count()
        ctx['needs_replacement'] = InventoryItem.objects.filter(
            condition='faulty'
        ).count()
        ctx['under_repair'] = InventoryItem.objects.filter(
            condition='needs_check'
        ).count()
        return ctx


# analytics — usage stats, issue breakdown
class AnalyticsView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/analytics.html'

    def get_context_data(self, **kwargs):
        from issues.models import Issue
        from scheduling.models import Session
        from django.utils import timezone
        from datetime import timedelta
        ctx = super().get_context_data(**kwargs)
        thirty_days_ago = timezone.now().date() - timedelta(days=30)
        ctx['sessions_this_month'] = Session.objects.filter(
            date__gte=thirty_days_ago
        ).count()
        ctx['resolved_issues'] = Issue.objects.filter(
            status='resolved'
        ).count()
        ctx['labs'] = Lab.objects.prefetch_related('pcs').all()
        ctx['issue_breakdown'] = Issue.objects.values(
            'issue_type'
        ).order_by('issue_type')
        return ctx


# system-wide alerts for admin
class AlertsView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/alerts.html'
    context_object_name = 'alerts'

    def get_queryset(self):
        from notifications.models import Notification
        return Notification.objects.filter(
            is_system_alert=True
        ).order_by('-created_at')


# equipment health for lab in-charge
class EquipmentView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/equipment.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['labs'] = Lab.objects.prefetch_related('pcs').all()
        ctx['items'] = InventoryItem.objects.select_related('lab').all()
        ctx['total_pcs'] = PC.objects.count()
        ctx['online_pcs'] = PC.objects.filter(status='online').count()
        return ctx


# ---------------------------------------------------------------------------
# Equipment (inventory) management: view details, status, maintenance,
# repair history, and equipment-specific issue reporting.
# ---------------------------------------------------------------------------

class InventoryDetailView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/inventory_detail.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        item = get_object_or_404(InventoryItem, pk=kwargs['pk'])
        ctx['item'] = item
        ctx['maintenance_logs'] = item.maintenance_logs.select_related('assigned_technician').order_by('-maintenance_date')[:10]
        ctx['open_issues'] = item.issues.filter(status='open').order_by('-date_reported')
        return ctx


class InventoryStatusListView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/inventory_status_list.html'
    context_object_name = 'items'

    def get_queryset(self):
        return InventoryItem.objects.select_related('lab').order_by('lab__name', 'name')


def inventory_status_update(request, pk):
    if not request.user.is_authenticated or request.user.role not in ('admin', 'incharge'):
        raise PermissionDenied
    item = get_object_or_404(InventoryItem, pk=pk)
    redirect_target = 'inventory_detail' if request.POST.get('next') == 'detail' else 'inventory_status_list'
    redirect_args = [item.pk] if redirect_target == 'inventory_detail' else []
    if request.method == 'POST':
        new_status = request.POST.get('status')
        if new_status in dict(InventoryItem.STATUS):
            item.status = new_status
            item.save(update_fields=['status'])
            messages.success(request, f'Status Updated Successfully for {item.name}.')
        return redirect(redirect_target, *redirect_args)
    return redirect(redirect_target, *redirect_args)


class MaintenanceLogListView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/maintenance_logs.html'
    context_object_name = 'logs'

    def get_queryset(self):
        return MaintenanceLog.objects.select_related('equipment', 'assigned_technician').order_by('completed', '-maintenance_date')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['equipment_list'] = InventoryItem.objects.all().order_by('name')
        ctx['pending_count'] = MaintenanceLog.objects.filter(completed=False).count()
        return ctx


class MaintenanceScheduleCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin']
    model = MaintenanceLog
    form_class = MaintenanceScheduleForm
    template_name = 'labs/maintenance_schedule_form.html'
    success_url = reverse_lazy('maintenance_logs')

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, 'Schedule Created Successfully.')
        return response


def maintenance_complete(request, pk):
    if request.user.role not in ['admin', 'incharge']:
        raise PermissionDenied
    log = get_object_or_404(MaintenanceLog, pk=pk)
    if request.method == 'POST':
        log.completion_notes = request.POST.get('completion_notes', '')
        log.completed = True
        log.completed_at = timezone.now()
        log.save(update_fields=['completion_notes', 'completed', 'completed_at'])
        messages.success(request, 'Maintenance Marked as Completed.')
        return redirect('maintenance_logs')
    return render(request, 'labs/maintenance_complete_form.html', {'log': log})


class RepairHistoryView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/repair_history.html'
    context_object_name = 'logs'

    def get_queryset(self):
        qs = MaintenanceLog.objects.select_related('equipment', 'assigned_technician').filter(completed=True)
        equipment_id = self.request.GET.get('equipment')
        technician_id = self.request.GET.get('technician')
        date_from = self.request.GET.get('date_from')
        date_to = self.request.GET.get('date_to')
        status = self.request.GET.get('status')

        if equipment_id:
            qs = qs.filter(equipment_id=equipment_id)
        if technician_id:
            qs = qs.filter(assigned_technician_id=technician_id)
        if date_from:
            qs = qs.filter(maintenance_date__gte=date_from)
        if date_to:
            qs = qs.filter(maintenance_date__lte=date_to)
        if status == 'pending':
            qs = MaintenanceLog.objects.none()  # this view only ever shows completed repairs
        return qs.order_by('-completed_at')

    def get_context_data(self, **kwargs):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        ctx = super().get_context_data(**kwargs)
        ctx['equipment_list'] = InventoryItem.objects.all().order_by('name')
        ctx['technicians'] = User.objects.filter(role__in=['admin', 'incharge'])
        ctx['selected_equipment'] = self.request.GET.get('equipment', '')
        ctx['selected_technician'] = self.request.GET.get('technician', '')
        ctx['date_from'] = self.request.GET.get('date_from', '')
        ctx['date_to'] = self.request.GET.get('date_to', '')
        return ctx


class EquipmentIssueListView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/equipment_issues.html'
    context_object_name = 'equipment_issues'

    def get_queryset(self):
        return EquipmentIssue.objects.select_related('equipment', 'reporter').order_by('-date_reported')


class EquipmentIssueCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin', 'incharge']
    model = EquipmentIssue
    fields = ['equipment', 'description']
    template_name = 'labs/equipment_issue_form.html'
    success_url = reverse_lazy('equipment_issues')

    def get_initial(self):
        initial = super().get_initial()
        equipment_id = self.request.GET.get('equipment')
        if equipment_id:
            initial['equipment'] = equipment_id
        return initial

    def form_valid(self, form):
        form.instance.reporter = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, 'Issue reported successfully.')
        return response


class EquipmentIssueDetailView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/equipment_issue_detail.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['equipment_issue'] = get_object_or_404(EquipmentIssue, pk=kwargs['pk'])
        return ctx


def equipment_issue_resolve(request, pk):
    if request.user.role not in ['admin', 'incharge']:
        raise PermissionDenied
    equipment_issue = get_object_or_404(EquipmentIssue, pk=pk)
    if request.method == 'POST':
        equipment_issue.resolution_notes = request.POST.get('resolution_notes', '')
        equipment_issue.status = 'resolved'
        equipment_issue.resolved_at = timezone.now()
        equipment_issue.save(update_fields=['resolution_notes', 'status', 'resolved_at'])
        messages.success(request, 'Resolution Saved Successfully.')
    return redirect('equipment_issues')