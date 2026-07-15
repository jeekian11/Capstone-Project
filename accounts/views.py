from django.shortcuts import redirect, get_object_or_404, render
from django.http import JsonResponse
from django.db.models import Q, Value
from django.db.models.functions import Concat
from django.views.generic import TemplateView, ListView, CreateView, UpdateView, DeleteView, FormView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.urls import reverse_lazy
from accounts.mixins import RoleRequiredMixin
from accounts.models import ActivityLog
from accounts.forms import AdminUserCreateForm, AdminSetPasswordForm, ProfileUpdateForm


# custom login view — same as Django's built-in one, but also hands the
# real PC inventory count to the template (used by the login page's
# boot-sequence panel, so it's not a hardcoded number)
class CompulabLoginView(LoginView):
    template_name = 'registration/login.html'

    def form_valid(self, form):
        # Students authenticate at the lab computer (see labs.ReservationPCLoginView),
        # never here — this is the web management system, which is off-limits
        # to student accounts per the system's access rules.
        user = form.get_user()
        if user.role == 'student':
            form.add_error(None, "Student accounts can't sign in here. Please use the login screen on a lab computer instead.")
            return self.form_invalid(form)
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        from labs.models import PC
        ctx = super().get_context_data(**kwargs)
        ctx['pc_count'] = PC.objects.count()
        return ctx

User = get_user_model()


class ProfileView(LoginRequiredMixin, UpdateView):
    """Lets any logged-in user (admin, in-charge, or instructor) update
    their own name, email, and profile picture from the navbar avatar."""
    model = User
    form_class = ProfileUpdateForm
    template_name = 'accounts/profile.html'
    success_url = reverse_lazy('profile')

    def get_object(self, queryset=None):
        return self.request.user

    def form_valid(self, form):
        messages.success(self.request, 'Profile updated.')
        return super().form_valid(form)


# sends each role to their own dashboard after login
def role_redirect(request):
    if not request.user.is_authenticated:
        return redirect('login')
    role = request.user.role
    if role == 'admin':
        return redirect('admin_dashboard')
    elif role == 'incharge':
        return redirect('incharge_dashboard')
    elif role == 'instructor':
        return redirect('instructor_dashboard')
    return redirect('login')


# incharge dashboard
# incharge dashboard — scoped entirely to the incharge's assigned lab
class InchargeDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['incharge']
    template_name = 'dashboard/incharge.html'

    def get_context_data(self, **kwargs):
        from issues.models import Issue
        from scheduling.models import Session
        from labs.models import PC, InventoryItem
        from notifications.models import Notification
        from django.utils import timezone
        ctx = super().get_context_data(**kwargs)

        lab = self.request.user.assigned_lab
        ctx['lab'] = lab

        pcs = PC.objects.filter(lab=lab) if lab else PC.objects.none()
        ctx['pcs'] = pcs
        ctx['online_count'] = pcs.filter(status='online').count()
        ctx['offline_count'] = pcs.filter(status='offline').count()

        issues_qs = Issue.objects.filter(lab=lab) if lab else Issue.objects.none()
        ctx['open_issues'] = issues_qs.filter(status='open').select_related('pc').order_by('-created_at')
        ctx['open_issues_count'] = ctx['open_issues'].count()

        sessions_qs = Session.objects.filter(lab=lab) if lab else Session.objects.none()
        ctx['todays_sessions'] = sessions_qs.filter(
            date=timezone.now().date()
        ).select_related('instructor', 'lab').order_by('start_time')
        ctx['todays_sessions_count'] = ctx['todays_sessions'].count()

        items = InventoryItem.objects.filter(lab=lab) if lab else InventoryItem.objects.none()
        ctx['needs_attention_count'] = items.filter(condition__in=['needs_check', 'faulty']).count()

        ctx['recent_alerts'] = Notification.objects.filter(
            is_system_alert=True
        ).order_by('-created_at')[:5] if lab else Notification.objects.none()

        return ctx


# instructor dashboard
class InstructorDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['instructor']
    template_name = 'dashboard/instructor.html'

    def get_context_data(self, **kwargs):
        from notifications.models import Notification
        from scheduling.models import Session, SessionRequest
        from django.utils import timezone
        ctx = super().get_context_data(**kwargs)
        today = timezone.now().date()

        ctx['upcoming_sessions'] = Session.objects.filter(
            instructor=self.request.user, date__gte=today
        ).select_related('lab').order_by('date', 'start_time')[:8]
        ctx['todays_sessions_count'] = Session.objects.filter(
            instructor=self.request.user, date=today
        ).count()

        ctx['pending_requests_count'] = SessionRequest.objects.filter(
            instructor=self.request.user, status='pending'
        ).count()

        ctx['notifications'] = Notification.objects.filter(
            user=self.request.user
        ).order_by('-created_at')[:6]
        ctx['unread_count'] = Notification.objects.filter(
            user=self.request.user, read=False
        ).count()
        return ctx


# admin user list
class UsersListView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin']
    template_name = 'accounts/users.html'
    context_object_name = 'users'
    queryset = User.objects.all().order_by('role', 'last_name')


# admin create new user
class UserCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin']
    template_name = 'accounts/user_form.html'
    model = User
    form_class = AdminUserCreateForm
    success_url = reverse_lazy('users')

    def form_valid(self, form):
        response = super().form_valid(form)
        ActivityLog.objects.create(
            actor=self.request.user,
            action='register',
            target_username=self.object.username,
            details=f'{self.object.get_full_name() or self.object.username} registered as {self.object.get_role_display()}.'
        )
        messages.success(self.request, 'User Registered Successfully.')
        return response


# admin edit existing user
class UserUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = ['admin']
    template_name = 'accounts/user_form.html'
    model = User
    fields = ['first_name', 'last_name', 'username', 'email', 'id_number',
              'course_year_section', 'department', 'role', 'assigned_lab', 'is_active']
    success_url = reverse_lazy('users')


# attendance module for instructor: (1) generate a per-day attendance
# summary for their own sessions with PDF/Excel export, and (2) browse
# individual attendance log records filtered by date / student / session.
def _pc_detected_attendance(session):
    """Present/absent breakdown for one session, based ONLY on PC-unlock
    activity logs in that session's lab/time window (and the attached
    roster, if any). Mirrors the logic used by the admin/incharge
    Attendance Report. Does not include manual overrides — see
    _instructor_session_attendance for the merged version."""
    from datetime import datetime
    from django.utils import timezone as tz

    start_dt = datetime.combine(session.date, session.start_time)
    end_dt = datetime.combine(session.date, session.end_time)
    start_dt = tz.make_aware(start_dt) if tz.is_naive(start_dt) else start_dt
    end_dt = tz.make_aware(end_dt) if tz.is_naive(end_dt) else end_dt

    logs = ActivityLog.objects.filter(
        action='pc_unlock', pc__lab_id=session.lab_id,
        created_at__gte=start_dt, created_at__lte=end_dt,
    ).select_related('pc').order_by('created_at')

    roster_by_id = {}
    if session.roster_id:
        roster_by_id = {rs.id_number: rs.full_name for rs in session.roster.students.all()}

    present, seen_ids = [], set()
    for log in logs:
        if log.target_username in seen_ids:
            continue
        seen_ids.add(log.target_username)
        name = roster_by_id.get(log.target_username) or session.requester_name or '—'
        present.append({
            'id_number': log.target_username, 'name': name,
            'pc': log.pc.pc_id if log.pc else '—', 'time': log.created_at,
        })

    if session.roster_id:
        absent = [{'id_number': i, 'full_name': n} for i, n in roster_by_id.items() if i not in seen_ids]
        expected = len(roster_by_id)
    else:
        absent = None
        expected = session.student_count or 0

    return {
        'session': session, 'has_roster': bool(session.roster_id),
        'present': present, 'absent': absent,
        'present_count': len(present),
        'absent_count': len(absent) if absent is not None else max(expected - len(present), 0),
        'expected_count': expected,
    }


def _mark_candidates(session):
    """Rows to show on the manual attendance-marking page: every roster
    student (if a roster is attached), or otherwise just the PC-detected
    present students — anyone else is added ad hoc from that page."""
    base = _pc_detected_attendance(session)
    candidates = []
    if base['has_roster']:
        for p in base['present']:
            candidates.append({'id_number': p['id_number'], 'name': p['name'], 'default_status': 'present'})
        for a in base['absent']:
            candidates.append({'id_number': a['id_number'], 'name': a['full_name'], 'default_status': 'absent'})
    else:
        for p in base['present']:
            candidates.append({'id_number': p['id_number'], 'name': p['name'], 'default_status': 'present'})
    return candidates


def _instructor_session_attendance(session):
    """PC-detected attendance, with any manual present/absent marks (for
    students who attended without logging into a lab PC) layered on top —
    a manual mark always wins over the PC-unlock estimate."""
    from scheduling.models import ManualAttendanceRecord

    base = _pc_detected_attendance(session)
    manual_records = list(ManualAttendanceRecord.objects.filter(session=session))
    if not manual_records:
        base['manually_marked'] = False
        return base

    combined = {}
    for p in base['present']:
        combined[p['id_number']] = {'name': p['name'], 'status': 'present', 'pc': p['pc'], 'time': p['time']}
    if base['absent'] is not None:
        for a in base['absent']:
            combined[a['id_number']] = {'name': a['full_name'], 'status': 'absent', 'pc': '—', 'time': None}

    for m in manual_records:
        existing = combined.get(m.id_number, {})
        combined[m.id_number] = {
            'name': m.full_name or existing.get('name') or m.id_number,
            'status': m.status,
            'pc': existing.get('pc') if m.status == 'present' and existing.get('pc') else ('Manual' if m.status == 'present' else '—'),
            'time': existing.get('time') if m.status == 'present' else None,
        }

    present = [{'id_number': k, 'name': v['name'], 'pc': v['pc'], 'time': v['time']} for k, v in combined.items() if v['status'] == 'present']
    absent = [{'id_number': k, 'full_name': v['name']} for k, v in combined.items() if v['status'] == 'absent']

    return {
        'session': session, 'has_roster': base['has_roster'],
        'present': present, 'absent': absent,
        'present_count': len(present), 'absent_count': len(absent),
        'expected_count': max(len(combined), base['expected_count']),
        'manually_marked': True,
    }


def session_attendance_mark(request, pk):
    if not request.user.is_authenticated or request.user.role != 'instructor':
        raise PermissionDenied
    from scheduling.models import Session, ManualAttendanceRecord
    session = get_object_or_404(Session, pk=pk, instructor=request.user)

    if request.method == 'POST':
        if request.POST.get('action') == 'add_student':
            id_number = request.POST.get('new_id_number', '').strip()
            full_name = request.POST.get('new_full_name', '').strip()
            status = request.POST.get('new_status', 'present')
            if id_number:
                ManualAttendanceRecord.objects.update_or_create(
                    session=session, id_number=id_number,
                    defaults={'full_name': full_name, 'status': status, 'marked_by': request.user},
                )
                messages.success(request, f'{full_name or id_number} marked {status}.')
            else:
                messages.error(request, 'Student ID is required.')
        else:
            for c in _mark_candidates(session):
                status = request.POST.get(f"status_{c['id_number']}")
                if status in ('present', 'absent'):
                    ManualAttendanceRecord.objects.update_or_create(
                        session=session, id_number=c['id_number'],
                        defaults={'full_name': c['name'], 'status': status, 'marked_by': request.user},
                    )
            messages.success(request, 'Attendance saved.')
        return redirect('session_attendance_mark', pk=session.pk)

    manual_by_id = {m.id_number: m for m in ManualAttendanceRecord.objects.filter(session=session)}
    candidates = _mark_candidates(session)
    candidate_ids = {c['id_number'] for c in candidates}
    for c in candidates:
        c['current_status'] = manual_by_id[c['id_number']].status if c['id_number'] in manual_by_id else c['default_status']
    ad_hoc = [m for m in manual_by_id.values() if m.id_number not in candidate_ids]

    return render(request, 'accounts/session_attendance_mark.html', {
        'session': session, 'candidates': candidates, 'ad_hoc': ad_hoc,
    })


def _instructor_summary_rows(user, day):
    from scheduling.models import Session
    sessions = Session.objects.filter(instructor=user, date=day).select_related('lab', 'roster').order_by('start_time')
    return [_instructor_session_attendance(s) for s in sessions]


def _instructor_log_rows(user, day=None, session_id=None, student=None):
    """Flattened, individually-filterable attendance records (one row per
    student per session) for 'View attendance logs'."""
    from scheduling.models import Session
    sessions = Session.objects.filter(instructor=user).select_related('lab', 'roster')
    if day:
        sessions = sessions.filter(date=day)
    if session_id:
        sessions = sessions.filter(pk=session_id)
    sessions = sessions.order_by('-date', 'start_time')

    rows = []
    for s in sessions:
        data = _instructor_session_attendance(s)
        for p in data['present']:
            rows.append({
                'date': s.date, 'session': s, 'id_number': p['id_number'],
                'name': p['name'], 'status': 'present', 'pc': p['pc'], 'time': p['time'],
            })
        if data['absent'] is not None:
            for a in data['absent']:
                rows.append({
                    'date': s.date, 'session': s, 'id_number': a['id_number'],
                    'name': a['full_name'], 'status': 'absent', 'pc': '—', 'time': None,
                })

    if student:
        needle = student.strip().lower()
        rows = [r for r in rows if needle in r['id_number'].lower() or needle in r['name'].lower()]

    return rows


class AttendanceView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['instructor']
    template_name = 'accounts/attendance.html'

    def get_context_data(self, **kwargs):
        from labs.reports import _parse_date
        from django.utils import timezone as tz
        from scheduling.models import Session

        ctx = super().get_context_data(**kwargs)
        today = tz.now().date()
        user = self.request.user

        ctx['my_sessions'] = Session.objects.filter(instructor=user).order_by('date')

        # 1) generate attendance summary
        summary_date = _parse_date(self.request.GET.get('summary_date'), today)
        ctx['summary_date'] = summary_date
        ctx['summary_rows'] = _instructor_summary_rows(user, summary_date)

        # 2) view attendance logs, filtered by date / student / session
        log_date = self.request.GET.get('log_date') or ''
        ctx['log_date'] = log_date
        ctx['log_student'] = self.request.GET.get('log_student', '')
        ctx['log_session'] = self.request.GET.get('log_session', '')
        ctx['log_rows'] = _instructor_log_rows(
            user,
            day=_parse_date(log_date, None) if log_date else None,
            session_id=ctx['log_session'] or None,
            student=ctx['log_student'] or None,
        )
        return ctx


def attendance_summary_export(request):
    if not request.user.is_authenticated or request.user.role != 'instructor':
        raise PermissionDenied
    from labs.reports import _parse_date, _export_pdf, _export_excel
    from django.utils import timezone as tz

    day = _parse_date(request.GET.get('summary_date'), tz.now().date())
    rows = _instructor_summary_rows(request.user, day)
    fmt = request.GET.get('format', 'pdf')
    title = 'My Attendance Summary'
    period = f'{day}'
    headers = ['Subject', 'Lab', 'Time', 'Present', 'Absent', 'Expected']
    body = [[
        r['session'].subject, r['session'].lab.name,
        f"{r['session'].start_time.strftime('%H:%M')}-{r['session'].end_time.strftime('%H:%M')}",
        r['present_count'], r['absent_count'], r['expected_count'],
    ] for r in rows]
    if fmt == 'excel':
        return _export_excel(title, period, headers, body, 'my_attendance_summary.xlsx')
    return _export_pdf(title, period, headers, body, 'my_attendance_summary.pdf')


def _require_admin(request):
    if not request.user.is_authenticated:
        raise PermissionDenied
    if request.user.role != 'admin':
        raise PermissionDenied


# admin activates a disabled account
@login_required
def user_activate(request, pk):
    _require_admin(request)
    target = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        target.is_active = True
        target.save()
        ActivityLog.objects.create(
            actor=request.user,
            action='activate',
            target_username=target.username,
            details=f'{target.get_full_name() or target.username} was activated.'
        )
        messages.success(request, f'{target.username} has been activated.')
    return redirect('users')


# admin deactivates an active account
@login_required
def user_deactivate(request, pk):
    _require_admin(request)
    target = get_object_or_404(User, pk=pk)
    if request.method == 'POST':
        target.is_active = False
        target.save()
        ActivityLog.objects.create(
            actor=request.user,
            action='deactivate',
            target_username=target.username,
            details=f'{target.get_full_name() or target.username} was deactivated.'
        )
        messages.warning(request, f'{target.username} has been deactivated.')
    return redirect('users')


# admin deletes a user — GET shows the confirmation page, POST performs the delete
class UserDeleteView(RoleRequiredMixin, DeleteView):
    allowed_roles = ['admin']
    model = User
    template_name = 'accounts/user_confirm_delete.html'
    success_url = reverse_lazy('users')
    context_object_name = 'target_user'

    def form_valid(self, form):
        username = self.object.username
        display_name = self.object.get_full_name() or username
        response = super().form_valid(form)
        ActivityLog.objects.create(
            actor=self.request.user,
            action='delete',
            target_username=username,
            details=f'{display_name} was deleted.'
        )
        messages.success(self.request, f'{username} has been deleted.')
        return response


# admin sets a brand-new password for an existing user (can't show the old one — it's hashed)
class UserResetPasswordView(RoleRequiredMixin, FormView):
    allowed_roles = ['admin']
    template_name = 'accounts/user_reset_password.html'
    form_class = AdminSetPasswordForm

    def dispatch(self, request, *args, **kwargs):
        self.target_user = get_object_or_404(User, pk=kwargs['pk'])
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['target_user'] = self.target_user
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['target_user'] = self.target_user
        return ctx

    def form_valid(self, form):
        form.save()
        ActivityLog.objects.create(
            actor=self.request.user,
            action='reset_password',
            target_username=self.target_user.username,
            details=f"{self.target_user.get_full_name() or self.target_user.username}'s password was reset by an admin."
        )
        messages.success(self.request, f"{self.target_user.username}'s password has been reset.")
        return redirect('users')


# system log history — filterable by actor, action type, and date range
class LogHistoryView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin']
    template_name = 'accounts/logs.html'
    context_object_name = 'logs'
    paginate_by = 50

    def get_queryset(self):
        return _filter_logs(self.request)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['action_choices'] = ActivityLog.ACTION_CHOICES
        ctx['actors'] = User.objects.filter(actions_performed__isnull=False).distinct()
        ctx['selected_action'] = self.request.GET.get('action', '')
        ctx['selected_actor'] = self.request.GET.get('actor', '')
        ctx['date_from'] = self.request.GET.get('date_from', '')
        ctx['date_to'] = self.request.GET.get('date_to', '')
        return ctx


def _filter_logs(request):
    qs = ActivityLog.objects.select_related('actor').all()
    action = request.GET.get('action')
    actor = request.GET.get('actor')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    if action:
        qs = qs.filter(action=action)
    if actor:
        qs = qs.filter(actor_id=actor)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    return qs


# export the currently filtered log history as a PDF
def export_logs_pdf(request):
    _require_admin(request)
    logs = _filter_logs(request)

    from io import BytesIO
    from django.http import HttpResponse
    from django.utils import timezone
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter, title='System Log History')
    styles = getSampleStyleSheet()
    elements = [
        Paragraph('CompuLab — System Log History', styles['Title']),
        Spacer(1, 6),
        Paragraph(f'Generated {timezone.now().strftime("%B %d, %Y %H:%M")}', styles['Normal']),
        Spacer(1, 16),
    ]

    data = [['Date/Time', 'Actor', 'Action', 'Target', 'Details']]
    for log in logs:
        data.append([
            log.created_at.strftime('%Y-%m-%d %H:%M'),
            log.actor.username if log.actor else '—',
            log.get_action_display(),
            log.target_username,
            log.details[:80],
        ])

    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#12171d')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(table)
    doc.build(elements)

    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="log_history.pdf"'
    return response


@login_required
def search_requesters(request):
    """Powers the requester search/auto-fill picker on 'Log a reservation'.
    Only returns active, registered accounts — matches the policy that only
    admin-registered accounts may hold a reservation. Restricted to the same
    roles allowed to log a reservation (admin/incharge).
    """
    if request.user.role not in ('admin', 'incharge'):
        raise PermissionDenied

    role = request.GET.get('role', '')
    query = (request.GET.get('q') or '').strip()

    # 'group' requester_type maps to student accounts (see scheduling forms) —
    # the group is filed under a student's own account, there's no distinct role.
    account_role = 'student' if role == 'group' else role
    if account_role not in ('student', 'instructor'):
        return JsonResponse({'results': []})

    qs = User.objects.filter(role=account_role, is_active=True)
    if query:
        # Match against first/last individually AND the combined full name,
        # so a two-word search like "Juan Cruz" or "Cruz Juan" also matches —
        # searching by only first_name/last_name separately missed these.
        qs = qs.annotate(
            full_name_concat=Concat('first_name', Value(' '), 'last_name'),
            full_name_concat_rev=Concat('last_name', Value(' '), 'first_name'),
        ).filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(id_number__icontains=query) |
            Q(username__icontains=query) |
            Q(full_name_concat__icontains=query) |
            Q(full_name_concat_rev__icontains=query)
        )
    qs = qs.order_by('last_name', 'first_name')[:200]

    def _rank(u):
        # Exact ID match first, then "starts with" name matches, so the
        # most likely intended person isn't buried among loose partial
        # matches — avoids an ambiguous, hard-to-scan result list.
        q = query.lower()
        if not q:
            return 0
        if u.id_number.lower() == q:
            return 0
        full_name = (u.get_full_name() or u.username).lower()
        if full_name.startswith(q) or u.last_name.lower().startswith(q):
            return 1
        return 2

    results = sorted(qs, key=_rank)[:15]
    results = [{
        'id': u.pk,
        'full_name': u.get_full_name() or u.username,
        'id_number': u.id_number,
        'course_year_section': u.course_year_section,
        'department': u.department,
    } for u in results]
    return JsonResponse({'results': results})