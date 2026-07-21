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
from accounts.forms import AdminUserCreateForm, AdminSetPasswordForm, ProfileUpdateForm, StudentImportForm
from accounts.constants import DEPARTMENT_CHOICES, department_year_levels_json, year_level_choices_for, department_name


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
        from scheduling.models import Session, SessionCheckIn
        from labs.models import PC, InventoryItem, MaintenanceLog
        from notifications.models import Notification
        from django.utils import timezone
        ctx = super().get_context_data(**kwargs)

        lab = self.request.user.assigned_lab
        ctx['lab'] = lab

        now = timezone.localtime(timezone.now())
        today = now.date()
        current_time = now.time()
        ctx['today'] = today

        pcs = PC.objects.filter(lab=lab) if lab else PC.objects.none()
        total_pcs = pcs.count()
        online_count = pcs.filter(status='online').count()
        in_use_count = pcs.filter(status='in_use').count()
        offline_count = pcs.filter(status='offline').count()
        ctx['online_count'] = online_count
        ctx['offline_count'] = offline_count
        ctx['in_use_count'] = in_use_count
        ctx['total_pcs'] = total_pcs

        def pct(part):
            return round(part / total_pcs * 100) if total_pcs else 0
        ctx['online_pct'] = pct(online_count)
        ctx['in_use_pct'] = pct(in_use_count)

        issues_qs = Issue.objects.filter(lab=lab) if lab else Issue.objects.none()
        ctx['open_issues'] = issues_qs.filter(status='open').select_related('pc').order_by('-created_at')
        ctx['open_issues_count'] = ctx['open_issues'].count()

        # --- today's schedule, with a computed ongoing/completed/upcoming status ---
        todays_sessions = []
        if lab:
            todays_sessions = list(
                Session.objects.filter(lab=lab, date=today)
                .select_related('instructor', 'roster').order_by('start_time')
            )
        for s in todays_sessions:
            if s.start_time <= current_time <= s.end_time:
                s.computed_status = 'ongoing'
            elif current_time > s.end_time:
                s.computed_status = 'completed'
            else:
                s.computed_status = 'upcoming'
        ctx['todays_sessions'] = todays_sessions
        ctx['todays_sessions_count'] = len(todays_sessions)
        ctx['reservations_today_count'] = len(todays_sessions)
        ctx['reservations_upcoming_count'] = sum(1 for s in todays_sessions if s.computed_status == 'upcoming')

        # --- derive a "reserved" display status for the PC map ---
        # CompuLab reservations book the whole lab for a time slot, not a
        # specific PC number — so an individual PC has no "reserved" field
        # of its own. What we CAN say for certain: while a session is
        # ongoing in this lab, any PC that's online but not yet logged
        # into (status='online') is effectively held for that class, not
        # free for a walk-in. We surface that as "reserved" on the map
        # without touching the PC's real status in the database — the
        # underlying online/offline/in_use/maintenance/issue value used
        # for counts and reports above is untouched.
        ongoing_session_now = any(s.computed_status == 'ongoing' for s in todays_sessions)
        ctx['ongoing_session_now'] = ongoing_session_now

        pcs_display = list(pcs)
        reserved_count = 0
        for pc in pcs_display:
            if pc.status == 'online' and ongoing_session_now:
                pc.display_status = 'reserved'
                pc.status_label = 'Reserved — online, held for the ongoing session'
                reserved_count += 1
            else:
                pc.display_status = pc.status
                pc.status_label = pc.get_status_display()
        ctx['pcs'] = pcs_display
        ctx['reserved_count'] = reserved_count
        ctx['has_attention_pcs'] = any(pc.status in ('maintenance', 'issue') for pc in pcs_display)

        # "PCs Available" on the metric card should match what the map shows as
        # green (online AND not currently reserved by an ongoing session)
        ctx['available_count'] = max(online_count - reserved_count, 0)
        ctx['available_pct'] = pct(ctx['available_count'])

        # active users — students currently signed into a PC in this lab right now
        ctx['active_users_count'] = pcs.filter(status='in_use').exclude(
            current_user__isnull=True
        ).values('current_user').distinct().count() if lab else 0

        # walk-ins checked in today (SessionCheckIn.checkin_type == 'walk_in')
        ctx['walk_ins_today_count'] = SessionCheckIn.objects.filter(
            checkin_type='walk_in', checked_in_at__date=today, session__lab=lab
        ).count() if lab else 0

        # attendance rate — distinct check-ins today vs. total students expected
        # across today's sessions
        expected = sum(s.student_count for s in todays_sessions)
        checked_in = SessionCheckIn.objects.filter(
            session__in=todays_sessions
        ).values('id_number').distinct().count() if todays_sessions else 0
        ctx['attendance_rate'] = round(checked_in / expected * 100) if expected else 0

        # equipment maintenance scheduled for today and not yet completed
        ctx['equipment_due_today_count'] = MaintenanceLog.objects.filter(
            equipment__lab=lab, maintenance_date=today, completed=False
        ).count() if lab else 0

        items = InventoryItem.objects.filter(lab=lab) if lab else InventoryItem.objects.none()
        ctx['needs_attention_count'] = items.filter(condition__in=['needs_check', 'faulty']).count()

        ctx['recent_alerts'] = Notification.objects.filter(
            is_system_alert=True
        ).order_by('-created_at')[:5] if lab else Notification.objects.none()

        # upcoming reservations — today's remaining sessions plus future days, capped at 5
        upcoming_reservations = []
        if lab:
            for s in Session.objects.filter(lab=lab, date__gte=today).select_related(
                'instructor', 'roster'
            ).order_by('date', 'start_time'):
                if s.date == today and current_time > s.end_time:
                    continue  # already finished today, don't show as "upcoming"
                s.computed_status = (
                    'ongoing' if (s.date == today and s.start_time <= current_time <= s.end_time)
                    else 'upcoming'
                )
                upcoming_reservations.append(s)
                if len(upcoming_reservations) >= 5:
                    break
        ctx['upcoming_reservations'] = upcoming_reservations

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

    def get_paginate_by(self, queryset):
        try:
            per_page = int(self.request.GET.get('per_page', 10))
        except (TypeError, ValueError):
            per_page = 10
        return per_page if per_page in (10, 25, 50) else 10

    def get_queryset(self):
        qs = User.objects.select_related('assigned_lab').order_by('role', 'last_name', 'first_name')
        role = self.request.GET.get('role', '')
        lab_id = self.request.GET.get('lab', '')
        status = self.request.GET.get('status', '')
        department = self.request.GET.get('department', '')
        q = self.request.GET.get('q', '').strip()
        if role in dict(User.ROLE_CHOICES):
            qs = qs.filter(role=role)
        if lab_id:
            qs = qs.filter(assigned_lab_id=lab_id)
        if status == 'active':
            qs = qs.filter(is_active=True)
        elif status == 'inactive':
            qs = qs.filter(is_active=False)
        if department in dict(DEPARTMENT_CHOICES):
            qs = qs.filter(department=department)
        if q:
            qs = qs.filter(
                Q(first_name__icontains=q) | Q(last_name__icontains=q) |
                Q(username__icontains=q) | Q(id_number__icontains=q)
            )
        return qs

    def get_context_data(self, **kwargs):
        from labs.models import Lab
        ctx = super().get_context_data(**kwargs)

        selected_role = self.request.GET.get('role', '')
        selected_lab = self.request.GET.get('lab', '')
        selected_status = self.request.GET.get('status', '')
        selected_department = self.request.GET.get('department', '')
        search_query = self.request.GET.get('q', '').strip()

        ctx['selected_role'] = selected_role
        ctx['selected_lab'] = selected_lab
        ctx['selected_status'] = selected_status
        ctx['selected_department'] = selected_department
        ctx['search_query'] = search_query
        ctx['selected_per_page'] = self.get_paginate_by(None)
        ctx['all_labs'] = Lab.objects.order_by('name')
        ctx['department_choices'] = DEPARTMENT_CHOICES
        ctx['filtered'] = bool(selected_role or selected_lab or selected_status or selected_department or search_query)
        ctx['total_filtered'] = self.get_queryset().count()

        role_counts = {key: User.objects.filter(role=key).count() for key, _ in User.ROLE_CHOICES}
        total_count = User.objects.count()
        ctx['total_count'] = total_count
        ctx['admin_count'] = role_counts.get('admin', 0)
        ctx['incharge_count'] = role_counts.get('incharge', 0)
        ctx['instructor_count'] = role_counts.get('instructor', 0)
        ctx['student_count'] = role_counts.get('student', 0)

        ctx['role_tabs'] = [
            (role_key, role_label, role_counts.get(role_key, 0))
            for role_key, role_label in User.ROLE_CHOICES
        ]

        def pct(part):
            return round(part / total_count * 100, 1) if total_count else 0

        ctx['role_distribution'] = [
            {'key': key, 'label': label, 'count': role_counts.get(key, 0), 'pct': pct(role_counts.get(key, 0))}
            for key, label in User.ROLE_CHOICES
        ]

        ctx['recent_activities'] = ActivityLog.objects.filter(
            action__in=['register', 'activate', 'deactivate', 'delete', 'reset_password']
        ).select_related('actor').order_by('-created_at')[:6]

        return ctx


# admin create new user
class UserCreateView(RoleRequiredMixin, CreateView):
    allowed_roles = ['admin']
    template_name = 'accounts/user_form.html'
    model = User
    form_class = AdminUserCreateForm
    success_url = reverse_lazy('users')

    def get_context_data(self, **kwargs):
        import json
        ctx = super().get_context_data(**kwargs)
        ctx['department_year_levels_json'] = json.dumps(department_year_levels_json())
        return ctx

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
    fields = ['first_name', 'last_name', 'username', 'id_number',
              'course_year_section', 'department', 'year_level', 'role', 'assigned_lab', 'is_active']
    success_url = reverse_lazy('users')

    def get_context_data(self, **kwargs):
        import json
        ctx = super().get_context_data(**kwargs)
        ctx['department_year_levels_json'] = json.dumps(department_year_levels_json())
        return ctx


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

    from scheduling.models import SessionCheckIn
    walk_in_ids = set(
        SessionCheckIn.objects.filter(session=session, checkin_type__in=('walk_in', 'override'))
        .values_list('id_number', flat=True)
    )

    present, seen_ids = [], set()
    for log in logs:
        if log.target_username in seen_ids or log.target_username in walk_in_ids:
            continue
        seen_ids.add(log.target_username)
        checkin = SessionCheckIn.objects.filter(session=session, id_number=log.target_username).select_related('student').first()
        name = (
            roster_by_id.get(log.target_username)
            or (checkin.student.get_full_name() or checkin.student.username if checkin and checkin.student else None)
            or session.requester_name or '—'
        )
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

    walk_ins = [
        {
            'id_number': c.id_number,
            'name': (c.student.get_full_name() or c.student.username) if c.student else c.id_number,
            'checkin_type': c.get_checkin_type_display(),
            'pc': c.pc.pc_id if c.pc else '—',
            'time': c.checked_in_at,
        }
        for c in SessionCheckIn.objects.filter(session=session, checkin_type__in=('walk_in', 'override')).select_related('student', 'pc')
    ]

    return {
        'session': session, 'has_roster': bool(session.roster_id),
        'present': present, 'absent': absent, 'walk_ins': walk_ins,
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
    from django.db.models import Q
    from scheduling.models import Session, ManualAttendanceRecord
    session = get_object_or_404(
        Session.objects.filter(Q(instructor=request.user) | Q(roster__instructor=request.user)),
        pk=pk,
    )

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


# export the currently filtered log history as PDF or Excel — shares the
# same _export_pdf/_export_excel helpers (and therefore the same design)
# as PC Activity Log and the other reports.
def export_logs_pdf(request):
    _require_admin(request)
    from labs.reports import _export_pdf, _export_excel

    logs = _filter_logs(request)

    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    if date_from and date_to:
        period = f'{date_from} to {date_to}'
    elif date_from:
        period = f'From {date_from}'
    elif date_to:
        period = f'Up to {date_to}'
    else:
        period = 'All time'

    headers = ['Date/Time', 'Actor', 'Action', 'Target', 'Details']
    rows = [
        [
            log.created_at.strftime('%Y-%m-%d %H:%M'),
            log.actor.username if log.actor else '—',
            log.get_action_display(),
            log.target_username,
            log.details[:80],
        ]
        for log in logs
    ]

    fmt = request.GET.get('format', 'pdf')
    if fmt == 'excel':
        return _export_excel('System Log History', period, headers, rows, 'log_history.xlsx')
    return _export_pdf('System Log History', period, headers, rows, 'log_history.pdf')


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
    # 'any' (used by the Override check-in form) searches every registered
    # student/instructor account regardless of role.
    if role == 'any':
        qs = User.objects.filter(role__in=('student', 'instructor'), is_active=True)
    else:
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
        'role': u.role,
        'course_year_section': u.course_year_section,
        'department': u.department_display,
    } for u in results]
    return JsonResponse({'results': results})

# admin — export the currently filtered user list as PDF or Excel, reusing
# the same styled report builders the labs app uses for its exports.
def export_users(request):
    if not request.user.is_authenticated or request.user.role != 'admin':
        raise PermissionDenied

    from labs.reports import _export_pdf, _export_excel

    qs = User.objects.select_related('assigned_lab').order_by('role', 'last_name', 'first_name')
    role = request.GET.get('role', '')
    lab_id = request.GET.get('lab', '')
    status = request.GET.get('status', '')
    department = request.GET.get('department', '')
    q = request.GET.get('q', '').strip()

    if role in dict(User.ROLE_CHOICES):
        qs = qs.filter(role=role)
    if lab_id:
        qs = qs.filter(assigned_lab_id=lab_id)
    if status == 'active':
        qs = qs.filter(is_active=True)
    elif status == 'inactive':
        qs = qs.filter(is_active=False)
    if department in dict(DEPARTMENT_CHOICES):
        qs = qs.filter(department=department)
    if q:
        qs = qs.filter(
            Q(first_name__icontains=q) | Q(last_name__icontains=q) |
            Q(username__icontains=q) | Q(id_number__icontains=q)
        )

    headers = ['Name', 'Username', 'ID Number', 'Department', 'Year Level', 'Role', 'Assigned Lab', 'Status']
    rows = [
        [
            u.get_full_name() or u.username,
            u.username,
            u.id_number or '—',
            u.department_display or '—',
            u.year_level_display or '—',
            u.get_role_display(),
            u.assigned_lab.name if u.assigned_lab else '—',
            'Active' if u.is_active else 'Disabled',
        ]
        for u in qs
    ]

    period = 'Filtered' if (role or lab_id or status or department or q) else 'All users'
    fmt = request.GET.get('format', 'pdf')
    if fmt == 'excel':
        return _export_excel('User & Role Management', period, headers, rows, 'users_report.xlsx')
    return _export_pdf('User & Role Management', period, headers, rows, 'users_report.pdf')


# admin — bulk-create student accounts from an uploaded Excel/CSV file,
# scoped to one Department at a time (chosen on the upload form). Every
# account created here lands under that Department, with its Year Level
# validated against that Department's own set of year levels.
class StudentImportView(RoleRequiredMixin, FormView):
    allowed_roles = ['admin']
    template_name = 'accounts/user_import.html'
    form_class = StudentImportForm
    success_url = reverse_lazy('user_import')

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['department_choices'] = DEPARTMENT_CHOICES
        return ctx

    def form_valid(self, form):
        from django.utils.crypto import get_random_string
        from accounts.imports import read_rows, validate_row, parse_year_level

        department = form.cleaned_data['department']
        uploaded = form.cleaned_data['file']

        try:
            rows = read_rows(uploaded)
        except ValueError as e:
            form.add_error('file', str(e))
            return self.form_invalid(form)

        if not rows:
            form.add_error('file', 'No data rows found in that file.')
            return self.form_invalid(form)

        created, skipped = [], []
        seen_id_numbers = set(
            User.objects.filter(role='student').values_list('id_number', flat=True)
        )
        seen_usernames = set(User.objects.values_list('username', flat=True))

        for i, row in enumerate(rows, start=2):  # row 1 is the header
            errors = validate_row(row, department)
            id_number = row.get('id_number', '')

            if not errors:
                if id_number in seen_id_numbers:
                    errors.append(f'A student with ID Number "{id_number}" already exists.')

            if errors:
                skipped.append({'row': i, 'id_number': id_number or '—', 'errors': errors})
                continue

            username = row.get('username') or id_number
            base_username = username
            n = 1
            while username in seen_usernames:
                n += 1
                username = f'{base_username}{n}'
            seen_usernames.add(username)
            seen_id_numbers.add(id_number)

            section = row.get('section', '')
            year_level = parse_year_level(row.get('year_level'))
            course_year_section = ' '.join(p for p in [
                f'Year {year_level}' if year_level else '', section
            ] if p).strip()

            user = User(
                username=username,
                first_name=row.get('first_name', ''),
                last_name=row.get('last_name', ''),
                id_number=id_number,
                role='student',
                department=department,
                year_level=year_level,
                course_year_section=course_year_section,
            )
            user.set_password(get_random_string(16))
            user.save()

            ActivityLog.objects.create(
                actor=self.request.user,
                action='register',
                target_username=user.username,
                details=f'{user.get_full_name() or user.username} imported as Student '
                        f'(Department: {department_name(department)}) via Excel/CSV import.'
            )
            created.append({'row': i, 'id_number': id_number, 'name': user.get_full_name() or user.username})

        if created:
            messages.success(self.request, f'Imported {len(created)} student{"s" if len(created) != 1 else ""}.')
        if skipped:
            messages.warning(self.request, f'{len(skipped)} row{"s were" if len(skipped) != 1 else " was"} skipped — see details below.')

        ctx = self.get_context_data(form=self.get_form_class()())
        ctx['results'] = {'created': created, 'skipped': skipped, 'department': department_name(department)}
        return self.render_to_response(ctx)


def user_import_template(request):
    """Downloadable starter CSV for the Import Students flow — headers only,
    so admins don't have to guess the expected column names."""
    if not request.user.is_authenticated or request.user.role != 'admin':
        raise PermissionDenied
    from django.http import HttpResponse
    import csv as csv_module

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="student_import_template.csv"'
    writer = csv_module.writer(response)
    writer.writerow(['ID Number', 'First Name', 'Last Name', 'Year Level', 'Section', 'Username'])
    writer.writerow(['2026-00123', 'Juan', 'Dela Cruz', '2', 'A', ''])
    return response
