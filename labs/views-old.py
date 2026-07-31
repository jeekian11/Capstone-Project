import json
from datetime import timedelta
from django.views.generic import TemplateView, ListView, UpdateView, CreateView, DeleteView, FormView
from django.http import JsonResponse
from django.shortcuts import redirect, get_object_or_404, render
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.urls import reverse_lazy, reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from accounts.mixins import RoleRequiredMixin
from labs.models import PC, Lab, InventoryItem, MaintenanceLog, EquipmentIssue, PCActivityLog
from labs.network import refresh_pc_statuses
from labs.privacy import resolve_site_label
from labs.forms import ReservationPCLoginForm, MaintenanceScheduleForm, LabForm, InventoryItemForm, PCForm, PCImportForm
from notifications import services as notify_service


# admin main dashboard
class AdminDashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin']
    template_name = 'dashboard/admin.html'

    def get_context_data(self, **kwargs):
        from datetime import timedelta
        from django.contrib.auth import get_user_model
        from django.db.models import Q
        from issues.models import Issue
        from scheduling.models import Session, SessionRequest
        from notifications.models import Notification
        from accounts.models import ActivityLog
        from labs.reports import _session_hours
        User = get_user_model()

        ctx = super().get_context_data(**kwargs)
        now = timezone.localtime(timezone.now())
        today = now.date()
        week_start = today - timedelta(days=today.weekday())  # Monday
        week_days = [week_start + timedelta(days=i) for i in range(7)]

        # --- top metric cards ---
        pcs = PC.objects.select_related('lab').all()
        ctx['pcs'] = pcs
        total_students = User.objects.filter(role='student').count()
        total_instructors = User.objects.filter(role='instructor').count()
        total_pcs = pcs.count()
        online_count = pcs.filter(status='online').count()
        offline_count = pcs.filter(status='offline').count()
        in_use_count = pcs.filter(status='in_use').count()
        maintenance_count = pcs.filter(status='maintenance').count()
        issue_count = pcs.filter(status='issue').count()

        ctx['total_students'] = total_students
        ctx['new_students_this_week'] = User.objects.filter(
            role='student', date_joined__date__gte=week_start
        ).count()
        ctx['total_instructors'] = total_instructors
        ctx['new_instructors_this_week'] = User.objects.filter(
            role='instructor', date_joined__date__gte=week_start
        ).count()
        ctx['total_labs'] = Lab.objects.count()
        ctx['total_pcs'] = total_pcs
        ctx['online_count'] = online_count
        ctx['offline_count'] = offline_count
        ctx['in_use_count'] = in_use_count
        ctx['maintenance_count'] = maintenance_count
        ctx['issue_count'] = issue_count
        ctx['open_issues_count'] = Issue.objects.filter(status='open').count()

        def pct(part):
            return round(part / total_pcs * 100) if total_pcs else 0

        ctx['in_use_pct'] = pct(in_use_count)
        ctx['online_pct'] = pct(online_count)
        ctx['maintenance_pct'] = pct(maintenance_count)
        ctx['issue_pct'] = pct(issue_count)
        ctx['offline_pct'] = pct(offline_count)

        # --- today's overview ---
        todays_sessions_qs = Session.objects.filter(date=today).select_related('instructor', 'lab')
        ctx['todays_sessions'] = todays_sessions_qs
        ctx['todays_sessions_count'] = todays_sessions_qs.count()
        ctx['active_sessions_count'] = todays_sessions_qs.filter(
            start_time__lte=now.time(), end_time__gte=now.time()
        ).count()
        ctx['pending_reservations_count'] = SessionRequest.objects.filter(status='pending').count()

        todays_attendance = ActivityLog.objects.filter(
            action='pc_unlock', created_at__date=today
        ).values('target_identifier').distinct().count()
        ctx['todays_attendance'] = todays_attendance
        ctx['todays_attendance_pct'] = (
            round(todays_attendance / total_students * 100, 1) if total_students else 0
        )

        # --- daily attendance trend (this week, Mon-Sun) ---
        attendance_trend = []
        for d in week_days:
            count = ActivityLog.objects.filter(
                action='pc_unlock', created_at__date=d
            ).values('target_identifier').distinct().count()
            attendance_trend.append(count)
        ctx['attendance_trend_labels'] = json.dumps(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])
        ctx['attendance_trend_data'] = json.dumps(attendance_trend)

        # --- weekly lab usage (this week, % of total lab capacity booked) ---
        labs_all = list(Lab.objects.all())

        def day_capacity_hours():
            total = 0
            for lab in labs_all:
                from datetime import datetime, date as date_cls
                open_hours = (
                    datetime.combine(date_cls.min, lab.closing_time)
                    - datetime.combine(date_cls.min, lab.opening_time)
                )
                total += open_hours.total_seconds() / 3600
            return total

        capacity = day_capacity_hours()
        lab_usage_trend = []
        for d in week_days:
            day_sessions = Session.objects.filter(date=d)
            booked_hours = sum(_session_hours(s) for s in day_sessions)
            usage_pct = round(booked_hours / capacity * 100) if capacity else 0
            lab_usage_trend.append(min(usage_pct, 100))
        ctx['lab_usage_labels'] = json.dumps(['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'])
        ctx['lab_usage_data'] = json.dumps(lab_usage_trend)

        # --- monthly computer utilization (last 4 weeks, % avg) ---
        monthly_util = []
        for w in range(3, -1, -1):
            w_start = week_start - timedelta(days=7 * w)
            w_end = w_start + timedelta(days=6)
            w_sessions = Session.objects.filter(date__gte=w_start, date__lte=w_end)
            booked_hours = sum(_session_hours(s) for s in w_sessions)
            week_capacity = capacity * 7
            util_pct = round(booked_hours / week_capacity * 100) if week_capacity else 0
            monthly_util.append(min(util_pct, 100))
        ctx['monthly_util_labels'] = json.dumps(['Week 1', 'Week 2', 'Week 3', 'Week 4'])
        ctx['monthly_util_data'] = json.dumps(monthly_util)

        # --- recent activity (merged feed) ---
        activity = []
        for log in ActivityLog.objects.select_related('actor', 'pc').order_by('-created_at')[:8]:
            if log.action == 'pc_unlock':
                activity.append({
                    'icon': 'login', 'title': f'Student {log.target_identifier} logged in on {log.pc.pc_id if log.pc else "a PC"}',
                    'sub': log.pc.lab.name if log.pc else '', 'time': log.created_at,
                })
            elif log.action == 'register':
                activity.append({
                    'icon': 'user', 'title': f'New user registered: {log.target_identifier}',
                    'sub': '', 'time': log.created_at,
                })
        for req in SessionRequest.objects.filter(status='approved').select_related('lab').order_by('-created_at')[:5]:
            activity.append({
                'icon': 'calendar', 'title': f'Reservation approved for {req.lab.name}',
                'sub': req.requester_name, 'time': req.created_at,
            })
        for issue in Issue.objects.select_related('pc').order_by('-created_at')[:5]:
            activity.append({
                'icon': 'tool',
                'title': f'Maintenance request submitted for {issue.pc.pc_id if issue.pc else "equipment"}',
                'sub': issue.title, 'time': issue.created_at,
            })
        activity.sort(key=lambda a: a['time'], reverse=True)
        ctx['recent_activity'] = activity[:5]

        # --- notifications (system alerts + this admin's own) ---
        ctx['recent_alerts'] = Notification.objects.filter(
            Q(is_system_alert=True) | Q(user=self.request.user)
        ).distinct().order_by('-created_at')[:5]

        # --- upcoming schedules (today, with ongoing/upcoming status) ---
        upcoming = []
        for s in todays_sessions_qs.order_by('start_time'):
            if s.start_time <= now.time() <= s.end_time:
                status = 'ongoing'
            elif s.start_time > now.time():
                status = 'upcoming'
            else:
                status = 'done'
            upcoming.append({'session': s, 'status': status})
        ctx['upcoming_schedules'] = upcoming

        return ctx


# Shared by ReservationPCLoginView (browser form, used when a student logs
# in from a separate reception/kiosk computer) and PCAgentLoginAPIView (JSON
# endpoint, used when the student types their ID/reservation code directly
# into the lock screen on the PC itself). Keeping this logic in one place
# means both entry points enforce identical rules — same reservation checks,
# same PC-unlock call, same activity log — they just differ in how the
# result gets back to the person (rendered form errors vs. a JSON reply).
def verify_reservation_and_check_in(remote_addr, id_number, code):
    """
    Returns a dict:
      {'ok': True,  'pc': PC, 'session': Session, 'unlock_success': bool, 'unlock_detail': str}
      {'ok': False, 'error': str}
    Does NOT raise on "expected" failures (no PC, no matching reservation,
    etc.) — those come back as {'ok': False, 'error': ...} so callers can
    show them however fits their interface.
    """
    from accounts.models import ActivityLog
    from django.contrib.auth import get_user_model
    from labs.network import unlock_pc
    from scheduling.models import Session, SessionCheckIn

    id_number = (id_number or '').strip()
    code = (code or '').strip()

    pc = PC.objects.filter(ip_address=remote_addr).select_related('lab').first()
    if pc is None:
        # Including the detected remote_addr here (instead of just a generic
        # message) is deliberate: this is the ONE place a mismatched/changed
        # PC IP actually surfaces to a human, and it shows up right on the
        # lock screen the student/in-charge is already looking at — no need
        # to go dig through server console/logs on a separate machine to
        # find out what IP this PC is actually being seen as.
        return {'ok': False, 'error': f"This computer isn't registered in the system yet (server sees this PC's IP as {remote_addr}). Ask your Laboratory In-Charge to add it before it can be unlocked."}

    # A reservation code is shared by different sets of people depending on
    # how the booking was made:
    #  - Individual request (requester_type='student', no roster attached):
    #    only the requester's own ID works.
    #  - Instructor request with their class roster attached: any student on
    #    that roster can check in with the shared code — a whole-class booking.
    #  - Group-of-students request (requester_type='group'): the group isn't
    #    enumerated by name anywhere — ANY ID number works with the shared
    #    code, since one person filed it on behalf of the whole group.
    #  - Any request with a roster attached (regardless of type): roster
    #    members can also check in with the shared code, in addition to
    #    whatever the rule above allows.
    session = Session.objects.filter(
        reservation_code__iexact=code,
        lab=pc.lab,
    ).select_related('lab', 'instructor', 'roster').first()

    if session is None:
        return {'ok': False, 'error': 'No matching reservation for this lab. Check your ID and reservation code, or ask your Lab In-Charge.'}

    if not id_number:
        return {'ok': False, 'error': 'Please enter your Student/Instructor ID.'}

    is_group_booking = session.requester_type == 'group'
    # Walk-in and Override reservations now book a specific NUMBER of PCs
    # in advance (see scheduling.utils.capacity_error) rather than a single
    # named person, so — just like Group bookings — their shared code
    # should work for any ID, capped at how many PCs were approved.
    is_capacity_booking = session.requester_type in ('walk_in', 'override')
    is_shared_code_booking = is_group_booking or is_capacity_booking
    is_primary_requester = id_number.lower() == (session.requester_id_number or '').strip().lower()
    roster_student = None
    if not is_primary_requester and session.roster_id:
        roster_student = session.roster.students.filter(id_number__iexact=id_number).first()

    User = get_user_model()
    walk_in_account = None
    if not is_shared_code_booking and not is_primary_requester and roster_student is None:
        # Not the requester, not a group/walk-in/override booking, not on
        # this session's roster — they have no claim on this specific
        # reservation. Only a Class (instructor) reservation has a WALK-IN
        # path: any other registered, active account may still use a PC
        # during that class's slot, but only if the laboratory currently
        # has an available (working, not-already-in-use) PC — a reservation
        # guarantees a slot for the people it was actually made for, not
        # for anyone who happens to be in the room. Individual bookings
        # don't get a walk-in path either: an Individual code is for
        # exactly one person. (Group/Walk-in/Override bookings already
        # accept any ID up to their own cap, handled above.)
        if session.requester_type != 'instructor':
            return {'ok': False, 'error': 'No matching reservation for this lab. Check your ID and reservation code, or ask your Lab In-Charge.'}

        walk_in_account = User.objects.filter(id_number__iexact=id_number).first()
        if walk_in_account is None:
            return {'ok': False, 'error': 'No matching reservation for this lab. Check your ID and reservation code, or ask your Lab In-Charge.'}
        if not walk_in_account.is_active:
            return {'ok': False, 'error': 'Your account has been deactivated. Ask your Admin or Lab In-Charge for assistance.'}

        working_pcs = PC.objects.filter(lab=pc.lab).exclude(status__in=['offline', 'maintenance', 'issue']).count()
        occupied_pcs = PC.objects.filter(lab=pc.lab, status='in_use').count()
        if working_pcs <= 0 or occupied_pcs >= working_pcs:
            return {'ok': False, 'error': (
                'This laboratory is at full capacity right now — no available computers for walk-in access. '
                'Please try again later or ask your Lab In-Charge.'
            )}

    already_checked_in = False
    if is_shared_code_booking:
        already_checked_in = session.check_ins.filter(id_number__iexact=id_number).exists()
        cap = (session.pcs_requested if is_capacity_booking else session.student_count) or 0
        unit = 'PC' if is_capacity_booking else 'student'
        if not already_checked_in and cap > 0 and session.check_ins.count() >= cap:
            return {'ok': False, 'error': (
                f"This reservation's check-in limit ({cap} {unit}{'s' if cap != 1 else ''}) has already been "
                "reached. If that count is wrong, ask your Admin or Lab In-Charge to update the request."
            )}

    if session.instructor is None:
        return {'ok': False, 'error': (
            'Your reservation was found, but this ID isn\u2019t linked to a registered account yet. '
            'Ask your Admin or Lab In-Charge to register your account before you can log in here.'
        )}

    if not session.instructor.is_active:
        return {'ok': False, 'error': 'Your account has been deactivated. Ask your Admin or Lab In-Charge for assistance.'}

    now = timezone.localtime()
    if session.date != now.date():
        return {'ok': False, 'error': f"This reservation is for {session.date.strftime('%B %d, %Y')}, not today."}
    if not (session.start_time <= now.time() <= session.end_time):
        return {'ok': False, 'error': f"This reservation is only valid from {session.start_time.strftime('%H:%M')} to {session.end_time.strftime('%H:%M')}."}

    success, detail = unlock_pc(pc)

    # Every successful check-in — not just group bookings — is now recorded
    # as a SessionCheckIn, tagged with how they got in (requester/roster/
    # group/walk-in) and the actual resolved account. This is the canonical
    # "who really used a PC during this reservation" transaction log, used
    # by the Attendance Report to show walk-ins as their own section instead
    # of folding them into the requester's name.
    if is_primary_requester:
        checkin_type = 'requester'
        checked_in_user = session.instructor
    elif roster_student and roster_student.student_id:
        checkin_type = 'roster'
        checked_in_user = roster_student.student
    elif roster_student:
        checkin_type = 'roster'
        checked_in_user = None  # roster entry isn't linked to a registered account
    elif is_group_booking:
        checkin_type = 'group'
        checked_in_user = User.objects.filter(id_number__iexact=id_number).first() or session.instructor
    elif is_capacity_booking:
        checkin_type = session.requester_type  # 'walk_in' or 'override' — same labels used by the live check-in features
        checked_in_user = User.objects.filter(id_number__iexact=id_number).first() or session.instructor
    else:
        checkin_type = 'walk_in'
        checked_in_user = walk_in_account

    SessionCheckIn.objects.update_or_create(
        session=session, id_number=id_number,
        defaults={'checkin_type': checkin_type, 'student': checked_in_user, 'pc': pc},
    )

    pc.status = 'in_use'
    pc.last_active = timezone.now()
    pc.current_user = checked_in_user
    pc.current_session = session
    pc.save(update_fields=['status', 'last_active', 'current_user', 'current_session'])

    # Human-readable label for messages/logs.
    if checkin_type == 'roster':
        checked_in_name = roster_student.full_name
    elif checkin_type == 'requester':
        checked_in_name = session.requester_name
    elif checkin_type == 'group':
        checked_in_name = f'{session.requester_name} — group member'
    elif is_capacity_booking:
        label = 'Walk-in' if session.requester_type == 'walk_in' else 'Override'
        checked_in_name = f'{id_number} — {label} reservation ({session.requester_name})'
    else:
        checked_in_name = f"{walk_in_account.display_name} — walk-in"

    ActivityLog.objects.create(
        actor=checked_in_user,
        action='pc_unlock',
        target_identifier=id_number,
        pc=pc,
        details=(
            f"{checked_in_name} ({id_number}) checked in with reservation {session.reservation_code} — unlocked {pc.pc_id} ({pc.lab.name})."
            if success else
            f"{checked_in_name} ({id_number}) was verified for {pc.pc_id} ({pc.lab.name}) via reservation {session.reservation_code}, but the unlock command didn't run: {detail}"
        )
    )

    return {
        'ok': True,
        'pc': pc,
        'session': session,
        'checked_in_name': checked_in_name,
        'checkin_type': checkin_type,
        'unlock_success': success,
        'unlock_detail': detail,
    }


# the lock screen shown on a lab computer itself — the person enters the
# Student/Instructor ID and reservation code that the Admin/Lab In-Charge
# gave them when their walk-in request was approved. This is deliberately
# NOT behind RoleRequiredMixin/LoginRequiredMixin: it's the page someone
# sees before they're identified at all, and it never grants access to the
# web management system, only to the physical PC it's running on.
#
# NOTE: this browser-based page is kept for labs that use a separate
# reception/kiosk computer for check-in. If students log in directly on the
# lock screen of the (currently locked) lab PC itself, see
# PCAgentLoginAPIView below instead — the lock screen can't show a normal
# browser page since it deliberately blocks all other access to the PC.
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
        result = verify_reservation_and_check_in(
            self.request.META.get('REMOTE_ADDR'),
            form.cleaned_data['id_number'],
            form.cleaned_data['reservation_code'],
        )

        if not result['ok']:
            form.add_error(None, result['error'])
            return self.form_invalid(form)

        pc = result['pc']
        session = result['session']
        if result['unlock_success']:
            messages.success(self.request, f"Welcome, {result['checked_in_name']}. Unlocking {pc.pc_id}...")
        else:
            messages.warning(self.request, f'You were verified, but {pc.pc_id} could not be unlocked automatically. Ask your Laboratory In-Charge for help.')

        return redirect('pc_login')


# JSON endpoint used by the lab_pc_agent running ON the lab PC itself, for
# labs where students type their ID/reservation code directly into the lock
# screen (instead of a separate reception/kiosk computer running the
# browser-based ReservationPCLoginView above). The request is expected to
# originate from the same machine's agent process, so REMOTE_ADDR here is
# that PC's own IP — the identical IP-based PC lookup used everywhere else,
# just reached over JSON instead of an HTML form. CSRF-exempt because this
# is a machine-to-machine call from a local agent process, not a browser
# session; PC_AGENT_SHARED_SECRET (checked the same way the agent checks
# incoming unlock/lock requests) is what stands in for a login here.
@csrf_exempt
def pc_agent_login_api(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'}, status=405)

    from django.conf import settings

    try:
        payload = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'invalid JSON body'}, status=400)

    expected_secret = getattr(settings, 'PC_AGENT_SHARED_SECRET', '')
    if not expected_secret or payload.get('secret') != expected_secret:
        return JsonResponse({'ok': False, 'error': 'invalid secret'}, status=403)

    result = verify_reservation_and_check_in(
        request.META.get('REMOTE_ADDR'),
        payload.get('id_number', ''),
        payload.get('reservation_code', ''),
    )

    if not result['ok']:
        return JsonResponse({'ok': False, 'error': result['error']}, status=200)

    return JsonResponse({
        'ok': True,
        'pc_id': result['pc'].pc_id,
        'student_name': result['checked_in_name'],
        'unlock_success': result['unlock_success'],
        'unlock_detail': result['unlock_detail'],
        # HH:MM:SS the reservation ends — the agent uses this to schedule an
        # automatic re-lock locally, without needing to keep polling the
        # server. Combined with today's date since Session.date is always
        # "today" by the time verify_reservation_and_check_in accepts it.
        'session_end_time': result['session'].end_time.strftime('%H:%M:%S'),
        # The server's own current wall-clock time (Asia/Manila), sent so the
        # agent can measure its OWN clock's drift/offset against the server
        # instead of trusting the lab PC's local Windows clock outright.
        # Without this, a lab PC with a wrong/drifted system clock schedules
        # the warning + auto-relock against the wrong moment in real time.
        'server_time': timezone.localtime().strftime('%H:%M:%S'),
    })


# JSON endpoint the lab_pc_agent calls (typically once at startup, and
# whenever it shows the lock screen) to find out its OWN registered name —
# the "PC 01"-style label shown on the lock screen BEFORE anyone has logged
# in, so it never has to be hand-typed into agent_config.json and drift out
# of sync with what's actually set for this PC in the admin panel (Manage
# PCs -> Edit). Same shared-secret + IP-based PC lookup as the other
# pc-agent-* endpoints; GET is fine since this is a read-only lookup, but
# the secret is still required so a random device on the network can't
# fish for lab PC names.
@csrf_exempt
def pc_agent_info_api(request):
    from django.conf import settings

    expected_secret = getattr(settings, 'PC_AGENT_SHARED_SECRET', '')
    secret = request.GET.get('secret') if request.method == 'GET' else None
    if secret is None:
        try:
            payload = json.loads(request.body or b'{}')
        except (ValueError, TypeError):
            payload = {}
        secret = payload.get('secret')

    if not expected_secret or secret != expected_secret:
        return JsonResponse({'ok': False, 'error': 'invalid secret'}, status=403)

    pc = PC.objects.select_related('lab').filter(ip_address=request.META.get('REMOTE_ADDR')).first()
    if pc is None:
        return JsonResponse({'ok': False, 'error': 'PC not registered'}, status=200)

    return JsonResponse({
        'ok': True,
        'pc_id': pc.pc_id,
        'lab_name': pc.lab.name,
    })


# JSON endpoint used by the lab_pc_agent while a PC is UNLOCKED — it calls
# this every `activity_report_interval_seconds` (agent_config.json) with
# the current foreground window's title bar text. This is the closest
# thing this system does to "what is the student doing" tracking: no
# keystrokes, no screenshots, no page HTML/URLs — just whatever text
# Windows itself puts in the active window's title bar (for a browser
# that's normally "Page Title - Browser Name"). Same shared-secret auth
# and same IP-based PC lookup as the other pc-agent-* endpoints.
@csrf_exempt
def pc_agent_activity_api(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'}, status=405)

    from django.conf import settings

    try:
        payload = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'invalid JSON body'}, status=400)

    expected_secret = getattr(settings, 'PC_AGENT_SHARED_SECRET', '')
    if not expected_secret or payload.get('secret') != expected_secret:
        return JsonResponse({'ok': False, 'error': 'invalid secret'}, status=403)

    pc = PC.objects.filter(ip_address=request.META.get('REMOTE_ADDR')).select_related('current_user').first()
    if pc is None:
        return JsonResponse({'ok': False, 'error': 'PC not registered'}, status=200)

    # Ignore samples that arrive for a PC the server doesn't currently
    # think is in use (e.g. a stray call that lands right as a session
    # ends) — there's no student to attribute it to at that point.
    if pc.current_user is None and pc.current_session is None:
        return JsonResponse({'ok': True, 'skipped': 'no active session'})

    window_title = (payload.get('window_title') or '').strip()[:500]
    page_url = (payload.get('page_url') or '').strip()[:500]

    PCActivityLog.objects.create(
        pc=pc,
        student=pc.current_user,
        session=pc.current_session,
        window_title=window_title,
        page_url=page_url,
    )

    return JsonResponse({'ok': True})


# Called by the agent whenever it re-locks a PC — either because the
# reservation's end_time was reached (automatic) or because the student
# clicked "Lock Now" on the little always-on-visible mini panel (manual,
# for when they're done early). Either way this just clears the PC's
# "currently in use" tracking server-side so status pages/admin reflect
# reality; the actual re-locking of the screen happens locally in the
# agent regardless of whether this call succeeds.
@csrf_exempt
def pc_agent_end_session_api(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'}, status=405)

    from django.conf import settings
    from accounts.models import ActivityLog

    try:
        payload = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'invalid JSON body'}, status=400)

    expected_secret = getattr(settings, 'PC_AGENT_SHARED_SECRET', '')
    if not expected_secret or payload.get('secret') != expected_secret:
        return JsonResponse({'ok': False, 'error': 'invalid secret'}, status=403)

    remote_addr = request.META.get('REMOTE_ADDR')
    pc = PC.objects.filter(ip_address=remote_addr).select_related('lab').first()
    if pc is None:
        return JsonResponse({'ok': False, 'error': 'PC not registered'}, status=200)

    reason = payload.get('reason', 'manual')  # 'manual', 'expired', or 'remote_lock'
    session = pc.current_session
    user = pc.current_user

    if session is not None or user is not None:
        if reason == 'expired':
            detail_msg = f"{pc.pc_id} ({pc.lab.name}) re-locked — reservation time ended."
        elif reason == 'remote_lock':
            detail_msg = (
                f"{pc.pc_id} ({pc.lab.name}) locked remotely by a Lab In-Charge "
                f"(Manual PC Control tool)."
            )
        else:
            detail_msg = f"{pc.pc_id} ({pc.lab.name}) locked by the student (session ended early)."

        ActivityLog.objects.create(
            actor=user,
            action='pc_lock',
            target_identifier=session.requester_id_number if session else '',
            pc=pc,
            details=detail_msg,
        )

    pc.status = 'online'
    pc.current_user = None
    pc.current_session = None
    pc.current_guest_name = ''
    pc.save(update_fields=['status', 'current_user', 'current_session', 'current_guest_name'])

    return JsonResponse({'ok': True})


# Called by the agent when a student clicks "Log Out" on the PC itself, or
# when the agent's own scheduled auto-relock timer fires because the
# reservation's end_time passed. Either way, this just marks the PC as no
# longer in use server-side — the actual re-locking of the screen is a
# purely local decision the agent already made before calling this; this
# endpoint exists so the admin dashboard / PC status view reflects reality.
@csrf_exempt
def pc_agent_logout_api(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'}, status=405)

    from django.conf import settings
    from accounts.models import ActivityLog

    try:
        payload = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'invalid JSON body'}, status=400)

    expected_secret = getattr(settings, 'PC_AGENT_SHARED_SECRET', '')
    if not expected_secret or payload.get('secret') != expected_secret:
        return JsonResponse({'ok': False, 'error': 'invalid secret'}, status=403)

    pc = PC.objects.filter(ip_address=request.META.get('REMOTE_ADDR')).select_related('lab').first()
    if pc is None:
        return JsonResponse({'ok': False, 'error': 'PC not registered'}, status=200)

    previous_user = pc.current_user
    reason = payload.get('reason', 'manual')  # 'manual' or 'expired'

    pc.status = 'locked'
    pc.current_user = None
    pc.current_guest_name = ''
    pc.last_active = timezone.now()
    pc.save(update_fields=['status', 'current_user', 'current_guest_name', 'last_active'])

    ActivityLog.objects.create(
        actor=previous_user,
        action='pc_lock',
        target_identifier=previous_user.display_name if previous_user else '',
        pc=pc,
        details=(
            f"{pc.pc_id} ({pc.lab.name}) re-locked — reservation time ended."
            if reason == 'expired' else
            f"{pc.pc_id} ({pc.lab.name}) locked by student (logged out)."
        )
    )

    return JsonResponse({'ok': True})


# Called by lab_pc_agent/manual_unlock.html — the standalone, no-login HTML
# tool that Lab In-Charges open to unlock a PC *directly* through its agent
# (IP + shared secret) when the CompuLab server itself is unreachable. That
# tool's own "Unlock PC" button already talks straight to the agent and
# does not depend on this endpoint at all — a PC can still be unlocked for
# a guest even while the server is down. This endpoint's only job is the
# separate, best-effort step of recording that guest access in the system's
# access log, tried right after the direct unlock and again whenever the
# tool is later able to reach the server. Because there is no login on that
# offline tool, the "Lab In-Charge name" here is a typed, unverified string
# (not tied to an authenticated account) — logged as such rather than
# attributed to a real actor, so the audit trail stays honest about what it
# actually knows.
@csrf_exempt
def manual_unlock_log_api(request):
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST only'}, status=405)

    from django.conf import settings
    from accounts.models import ActivityLog
    from scheduling.models import Session, SessionCheckIn

    try:
        payload = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'ok': False, 'error': 'invalid JSON body'}, status=400)

    expected_secret = getattr(settings, 'PC_AGENT_SHARED_SECRET', '')
    if not expected_secret or payload.get('secret') != expected_secret:
        return JsonResponse({'ok': False, 'error': 'invalid secret'}, status=403)

    pc_ip = (payload.get('pc_ip') or '').strip()
    guest_name = (payload.get('guest_name') or '').strip()
    incharge_name = (payload.get('incharge_name') or '').strip()

    if not guest_name or not incharge_name:
        return JsonResponse({'ok': False, 'error': 'guest_name and incharge_name are both required'}, status=400)

    pc = PC.objects.select_related('lab').filter(ip_address=pc_ip).first()
    if pc is None:
        return JsonResponse({'ok': False, 'error': 'No PC in the system is registered with that IP address'}, status=200)

    now = timezone.localtime()
    active_session = Session.objects.filter(
        lab=pc.lab, date=now.date(), start_time__lte=now.time(), end_time__gte=now.time(),
    ).order_by('start_time').first()

    guest_id_number = f"GUEST-{pc.pc_id}-{int(timezone.now().timestamp())}"
    SessionCheckIn.objects.update_or_create(
        session=active_session, id_number=guest_id_number,
        defaults={'checkin_type': 'guest', 'student': None, 'guest_name': guest_name, 'pc': pc},
    )

    pc.status = 'in_use'
    pc.last_active = timezone.now()
    pc.current_user = None
    pc.current_guest_name = guest_name
    pc.current_session = active_session
    pc.save(update_fields=['status', 'last_active', 'current_user', 'current_guest_name', 'current_session'])

    ActivityLog.objects.create(
        actor=None,
        action='pc_unlock',
        target_identifier=guest_id_number,
        pc=pc,
        details=(
            f'Emergency Manual Unlock (offline tool — used because the CompuLab server could not be reached '
            f'at the time of unlock): walk-in guest "{guest_name}" was granted access to {pc.pc_id} ({pc.lab.name}). '
            f'Lab In-Charge name entered on the offline tool (typed, not verified via login): "{incharge_name}". '
            + (f'Overlapping reservation {active_session.reservation_code}.' if active_session else 'No active reservation.')
        )
    )

    return JsonResponse({'ok': True})


class PCStatusView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/pc_status.html'
    PAGE_SIZE = 10

    def get_context_data(self, **kwargs):
        from django.conf import settings
        from django.core.paginator import Paginator
        from django.db.models import Q
        from accounts.models import ActivityLog
        ctx = super().get_context_data(**kwargs)

        user = self.request.user
        lab_id = self.request.GET.get('lab', '')
        status_filter = self.request.GET.get('status', '')
        day = self.request.GET.get('day', '')
        q = self.request.GET.get('q', '').strip()

        labs_qs = Lab.objects.order_by('name')
        pcs_qs = PC.objects.select_related('lab', 'current_user', 'current_session')

        # Lab In-charge only ever sees their own assigned lab — same scoping
        # rule used by refresh_pc_status_view.
        if user.role == 'incharge' and user.assigned_lab_id:
            labs_qs = labs_qs.filter(pk=user.assigned_lab_id)
            pcs_qs = pcs_qs.filter(lab_id=user.assigned_lab_id)
            if not lab_id:
                lab_id = str(user.assigned_lab_id)

        ctx['all_labs'] = labs_qs
        ctx['status_choices'] = PC.STATUS
        ctx['selected_lab'] = lab_id
        ctx['selected_status'] = status_filter
        ctx['selected_day'] = day
        ctx['search_query'] = q
        ctx['filtered'] = bool(lab_id or status_filter or day or q)
        ctx['day_mode'] = bool(day)
        ctx['auto_check_interval_seconds'] = getattr(settings, 'PC_STATUS_CHECK_INTERVAL_SECONDS', 60)

        # ---- top summary metrics — scoped by lab (if chosen) but not by the
        # status filter, so the cards stay meaningful while the table below
        # is narrowed down to one status. ----
        metric_base = pcs_qs
        if lab_id:
            metric_base = metric_base.filter(lab_id=lab_id)
        total_pcs = metric_base.count()
        available_count = metric_base.filter(status='online').count()
        in_use_count = metric_base.filter(status='in_use').count()
        offline_count = metric_base.filter(status='offline').count()
        maintenance_count = metric_base.filter(status='maintenance').count()
        issue_count = metric_base.filter(status='issue').count()
        online_pcs_count = total_pcs - offline_count  # "reachable" = anything not offline

        def pct(part):
            return round(part / total_pcs * 100) if total_pcs else 0

        ctx.update({
            'total_pcs': total_pcs,
            'online_pcs_count': online_pcs_count, 'online_pcs_pct': pct(online_pcs_count),
            'available_count': available_count, 'available_pct': pct(available_count),
            'in_use_count': in_use_count, 'in_use_pct': pct(in_use_count),
            'offline_count': offline_count, 'offline_pct': pct(offline_count),
            'maintenance_count': maintenance_count, 'maintenance_pct': pct(maintenance_count),
            'issue_count': issue_count, 'issue_pct': pct(issue_count),
        })

        # ---- "Day" lookup mode — which PCs were actually checked in to
        # (unlocked) on a past date, from the login history. PC.last_active
        # gets overwritten by later checks, so it can't answer this. ----
        if day:
            logs = ActivityLog.objects.filter(
                action='pc_unlock', pc__isnull=False, created_at__date=day
            ).select_related('pc', 'pc__lab', 'actor').order_by('pc__lab__name', 'pc__pc_id', 'created_at')
            if lab_id:
                logs = logs.filter(pc__lab_id=lab_id)
            if q:
                logs = logs.filter(
                    Q(pc__pc_id__icontains=q) |
                    Q(target_identifier__icontains=q) |
                    Q(actor__first_name__icontains=q) |
                    Q(actor__last_name__icontains=q) |
                    Q(actor__email__icontains=q)
                )
            labs_usage = {}
            for log in logs:
                labs_usage.setdefault(log.pc.lab, []).append(log)
            ctx['labs_usage'] = [{'lab': lab, 'logs': entries} for lab, entries in labs_usage.items()]
            ctx['total_filtered'] = logs.count()
            return ctx

        # ---- live PC list — filterable + paginated ----
        list_qs = pcs_qs
        if lab_id:
            list_qs = list_qs.filter(lab_id=lab_id)
        if status_filter:
            list_qs = list_qs.filter(status=status_filter)
        if q:
            list_qs = list_qs.filter(
                Q(pc_id__icontains=q) |
                Q(current_user__first_name__icontains=q) |
                Q(current_user__last_name__icontains=q) |
                Q(current_user__id_number__icontains=q) |
                Q(current_user__email__icontains=q)
            )
        list_qs = list_qs.order_by('lab__name', 'pc_id')
        ctx['total_filtered'] = list_qs.count()

        paginator = Paginator(list_qs, self.PAGE_SIZE)
        page_obj = paginator.get_page(self.request.GET.get('page', 1))
        ctx['page_obj'] = page_obj

        # Real "login time" per in-use PC — taken from its most recent
        # pc_unlock activity log entry (PC.last_active alone doesn't tell us
        # when the *current* session started).
        in_use_ids = [pc.pk for pc in page_obj.object_list if pc.status == 'in_use']
        latest_unlocks = {}
        if in_use_ids:
            for log in ActivityLog.objects.filter(action='pc_unlock', pc_id__in=in_use_ids).order_by('pc_id', '-created_at'):
                latest_unlocks.setdefault(log.pc_id, log.created_at)
        for pc in page_obj.object_list:
            pc.login_time = latest_unlocks.get(pc.pk)

        # ---- live lab usage bars (in-use / total per lab) ----
        usage_labs = labs_qs.filter(pk=lab_id) if lab_id else labs_qs
        lab_usage = []
        for lab in usage_labs:
            lab_total = pcs_qs.filter(lab=lab).count()
            lab_in_use = pcs_qs.filter(lab=lab, status='in_use').count()
            lab_usage.append({
                'lab': lab, 'total': lab_total, 'in_use': lab_in_use,
                'pct': round(lab_in_use / lab_total * 100) if lab_total else 0,
            })
        ctx['lab_usage'] = lab_usage

        # ---- recent activity feed ----
        recent_qs = ActivityLog.objects.filter(action='pc_unlock', pc__isnull=False).select_related('pc', 'actor')
        if lab_id:
            recent_qs = recent_qs.filter(pc__lab_id=lab_id)
        ctx['recent_activities'] = recent_qs.order_by('-created_at')[:8]

        # ---- lab information side panel (only meaningful for one lab) ----
        if lab_id:
            selected_lab_obj = labs_qs.filter(pk=lab_id).first()
            if selected_lab_obj:
                ctx['selected_lab_obj'] = selected_lab_obj
                ctx['selected_lab_total_pcs'] = pcs_qs.filter(lab=selected_lab_obj).count()

        return ctx


# Admin/Lab In-Charge action: manually check a registered account into a
# specific, currently-available PC — the "Override" counterpart to the
# self-service walk-in path in verify_reservation_and_check_in. Used for
# cases the self-service kiosk can't handle on its own (no reservation code
# on hand, PC agent lock-screen unreachable, etc.), while still respecting
# the same "only if the lab has room" rule and leaving the same kind of
# transaction trail (SessionCheckIn + ActivityLog) walk-ins do.
class OverrideCheckInView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/override_checkin.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        labs_qs = Lab.objects.order_by('name')
        if user.role == 'incharge' and user.assigned_lab_id:
            labs_qs = labs_qs.filter(pk=user.assigned_lab_id)
        ctx['all_labs'] = labs_qs
        ctx['selected_lab'] = self.request.GET.get('lab', '')
        if ctx['selected_lab']:
            ctx['available_pcs'] = PC.objects.filter(lab_id=ctx['selected_lab'], status='online').order_by('pc_id')
        return ctx

    def post(self, request, *args, **kwargs):
        from accounts.models import ActivityLog
        from django.contrib.auth import get_user_model
        from labs.network import unlock_pc
        from scheduling.models import Session, SessionCheckIn

        pc_id = request.POST.get('pc', '')
        student_pk = request.POST.get('student_id', '')

        pc = PC.objects.select_related('lab').filter(pk=pc_id).first() if pc_id.isdigit() else None
        if pc is None:
            messages.error(request, 'Select a computer to override into.')
            return redirect(f"{reverse('override_checkin')}?lab={request.POST.get('lab', '')}")

        User = get_user_model()
        account = User.objects.filter(pk=student_pk, role__in=('student', 'instructor'), is_active=True).first() if student_pk.isdigit() else None
        if account is None:
            messages.error(request, 'Select a registered, active student or instructor account.')
            return redirect(f"{reverse('override_checkin')}?lab={pc.lab_id}")

        # Re-check occupancy/availability at submit time too — the picker
        # above is just a convenience, this is the real gate (protects
        # against the PC being taken by someone else between page load and
        # submit, or against a stale/forged pc id in the POST body).
        pc.refresh_from_db()
        if pc.status != 'online':
            messages.error(request, f'{pc.pc_id} is no longer available — someone else may have just taken it.')
            return redirect(f"{reverse('override_checkin')}?lab={pc.lab_id}")

        now = timezone.localtime()
        active_session = Session.objects.filter(
            lab=pc.lab, date=now.date(), start_time__lte=now.time(), end_time__gte=now.time(),
        ).order_by('start_time').first()

        success, detail = unlock_pc(pc)

        id_number = account.id_number or account.display_name
        SessionCheckIn.objects.update_or_create(
            session=active_session, id_number=id_number,
            defaults={'checkin_type': 'override', 'student': account, 'pc': pc},
        )

        pc.status = 'in_use'
        pc.last_active = timezone.now()
        pc.current_user = account
        pc.current_session = active_session
        pc.save(update_fields=['status', 'last_active', 'current_user', 'current_session'])

        account_name = account.display_name
        ActivityLog.objects.create(
            actor=request.user,
            action='pc_unlock',
            target_identifier=id_number,
            pc=pc,
            details=(
                f"Override by {request.user.display_name}: granted {account_name} "
                f"({id_number}) access to {pc.pc_id} ({pc.lab.name})"
                + (f", overlapping reservation {active_session.reservation_code}" if active_session else ", no active reservation")
                + ('.' if success else f' — unlock command did not run: {detail}.')
            )
        )

        if success:
            messages.success(request, f'{account_name} was checked into {pc.pc_id} ({pc.lab.name}).')
        else:
            messages.warning(request, f'{account_name} was recorded as checked into {pc.pc_id}, but the unlock command did not run: {detail}')
        return redirect(f"{reverse('override_checkin')}?lab={pc.lab_id}")


# Lab In-Charge / Admin action: unlock a PC for a first-time walk-in Guest —
# someone with NO registered account in the system at all (as opposed to
# Override Check-in above, which is only for already-registered student/
# instructor accounts). Since there's no account to identify the person by,
# the Lab In-Charge must type in the guest's Full Name before the unlock is
# allowed to go through; that name is what gets recorded in the access log
# (SessionCheckIn + ActivityLog) instead of a student ID.
class ManualUnlockView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/manual_unlock.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        labs_qs = Lab.objects.order_by('name')
        if user.role == 'incharge' and user.assigned_lab_id:
            labs_qs = labs_qs.filter(pk=user.assigned_lab_id)
        ctx['all_labs'] = labs_qs
        ctx['selected_lab'] = self.request.GET.get('lab', '')
        if ctx['selected_lab']:
            ctx['available_pcs'] = PC.objects.filter(lab_id=ctx['selected_lab'], status='online').order_by('pc_id')
        return ctx

    def post(self, request, *args, **kwargs):
        from accounts.models import ActivityLog
        from labs.network import unlock_pc
        from scheduling.models import Session, SessionCheckIn

        pc_id = request.POST.get('pc', '')
        guest_name = (request.POST.get('guest_name') or '').strip()

        pc = PC.objects.select_related('lab').filter(pk=pc_id).first() if pc_id.isdigit() else None
        if pc is None:
            messages.error(request, 'Select a computer to unlock.')
            return redirect(f"{reverse('manual_unlock')}?lab={request.POST.get('lab', '')}")

        if not guest_name:
            messages.error(
                request,
                "Enter the guest's Full Name before unlocking — this is required so the access log can identify who used the computer."
            )
            return redirect(f"{reverse('manual_unlock')}?lab={pc.lab_id}")

        # Re-check occupancy at submit time too, same reasoning as Override
        # Check-in — the picker above is just a convenience.
        pc.refresh_from_db()
        if pc.status != 'online':
            messages.error(request, f'{pc.pc_id} is no longer available — someone else may have just taken it.')
            return redirect(f"{reverse('manual_unlock')}?lab={pc.lab_id}")

        now = timezone.localtime()
        active_session = Session.objects.filter(
            lab=pc.lab, date=now.date(), start_time__lte=now.time(), end_time__gte=now.time(),
        ).order_by('start_time').first()

        success, detail = unlock_pc(pc)

        # Guests have no account/ID number, so a stable synthetic one is
        # generated for the SessionCheckIn transaction record — the real,
        # human-readable identifier is guest_name.
        guest_id_number = f"GUEST-{pc.pc_id}-{int(timezone.now().timestamp())}"
        SessionCheckIn.objects.update_or_create(
            session=active_session, id_number=guest_id_number,
            defaults={'checkin_type': 'guest', 'student': None, 'guest_name': guest_name, 'pc': pc},
        )

        pc.status = 'in_use'
        pc.last_active = timezone.now()
        pc.current_user = None
        pc.current_guest_name = guest_name
        pc.current_session = active_session
        pc.save(update_fields=['status', 'last_active', 'current_user', 'current_guest_name', 'current_session'])

        ActivityLog.objects.create(
            actor=request.user,
            action='pc_unlock',
            target_identifier=guest_id_number,
            pc=pc,
            details=(
                f"Manual Unlock by {request.user.display_name}: "
                f"walk-in guest \"{guest_name}\" (no registered account) granted access to {pc.pc_id} ({pc.lab.name})"
                + (f", overlapping reservation {active_session.reservation_code}" if active_session else ", no active reservation")
                + ('.' if success else f' — unlock command did not run: {detail}.')
            )
        )

        if success:
            messages.success(request, f'{guest_name} was checked into {pc.pc_id} ({pc.lab.name}) as a guest.')
        else:
            messages.warning(request, f'{guest_name} was recorded as checked into {pc.pc_id}, but the unlock command did not run: {detail}')
        return redirect(f"{reverse('manual_unlock')}?lab={pc.lab_id}")


def _pc_activity_app_name(title):
    """Collapses a raw window-title sample down to just the application name,
    e.g. 'index.html - Visual Studio Code' -> 'Visual Studio Code'. Titles
    that don't follow the '<page> - <app>' convention are used as-is.
    Only a fallback for non-browser windows — for browsers, prefer
    _pc_activity_site_label() below, which can use the captured URL."""
    title = (title or '').strip()
    if not title:
        return 'Unknown'
    if ' - ' in title:
        return title.rsplit(' - ', 1)[-1].strip() or title
    return title


def _pc_activity_site_label(title, url):
    """Best label for a raw activity sample: the resolved site ('Chrome —
    ChatGPT') when it's a browser window, otherwise the plain app name."""
    return resolve_site_label(title, url) or _pc_activity_app_name(title)


def _pc_activity_fmt_duration(td, long_form=False):
    """Formats a timedelta as '1h 29m 37s' (default) or, for the session
    detail panel, '1 Hour 29 Minutes 37 Seconds'."""
    if td is None:
        return '—'
    total_seconds = int(td.total_seconds())
    if total_seconds < 0:
        total_seconds = 0
    hours, rem = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if long_form:
        parts = []
        if hours:
            parts.append(f"{hours} Hour{'s' if hours != 1 else ''}")
        if minutes or hours:
            parts.append(f"{minutes} Minute{'s' if minutes != 1 else ''}")
        parts.append(f"{seconds} Second{'s' if seconds != 1 else ''}")
        return ' '.join(parts)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes} min" if seconds == 0 else f"{minutes}m {seconds}s"
    return f"{seconds} sec"


def _build_pc_sessions(logs, now, gap):
    """Reconstructs login -> app-switch -> logout 'sessions' out of the raw
    window-title samples the lab_pc_agent reports every few seconds. There's
    no explicit login/logout event in this data — a session is inferred as a
    run of consecutive samples for the same PC/student with no gap larger
    than `gap` between them. `logs` must already be ordered by
    ('pc_id', 'captured_at').
    """
    sessions = []
    run = []
    prev_pc_id = prev_student_id = prev_time = None

    def flush(run):
        if not run:
            return
        first, last = run[0], run[-1]
        pc, student = first.pc, first.student

        blocks = []
        bstart = btitle = bend = burl = None
        for log in run:
            if btitle is None:
                bstart = bend = log.captured_at
                btitle = log.window_title
                burl = log.page_url
                continue
            if log.window_title != btitle:
                blocks.append({
                    'title': btitle, 'start': bstart, 'end': bend, 'url': burl,
                    'site_label': _pc_activity_site_label(btitle, burl),
                })
                bstart = log.captured_at
                btitle = log.window_title
                burl = log.page_url
            bend = log.captured_at
        if btitle is not None:
            blocks.append({
                'title': btitle, 'start': bstart, 'end': bend, 'url': burl,
                'site_label': _pc_activity_site_label(btitle, burl),
            })

        is_ongoing = (now - last.captured_at) <= gap
        status = 'active' if is_ongoing else 'completed'
        duration = (now - first.captured_at) if is_ongoing else (last.captured_at - first.captured_at)
        key = f'{pc.pk}-{int(first.captured_at.timestamp())}'

        rows = [{
            'time': first.captured_at, 'student': student, 'pc': pc,
            'activity': 'Logged In', 'application': 'Windows Login',
            'duration': None, 'duration_display': '—', 'status': status, 'session_key': key,
        }]
        for i, b in enumerate(blocks):
            is_last = (i == len(blocks) - 1)
            block_dur = b['end'] - b['start']
            has_dur = block_dur.total_seconds() > 0
            rows.append({
                'time': b['start'], 'student': student, 'pc': pc,
                'activity': 'Opened Application',
                'application': b['title'] or '(Untitled window)',
                'duration': block_dur if has_dur else None,
                'duration_display': _pc_activity_fmt_duration(block_dur) if has_dur else '—',
                'status': 'active' if (is_ongoing and is_last) else 'completed',
                'session_key': key,
            })
        if not is_ongoing:
            rows.append({
                'time': last.captured_at, 'student': student, 'pc': pc,
                'activity': 'Logged Out', 'application': 'Windows Logout',
                'duration': duration, 'duration_display': _pc_activity_fmt_duration(duration),
                'status': 'completed', 'session_key': key,
            })

        sessions.append({
            'key': key, 'pc': pc, 'student': student,
            'login_time': first.captured_at,
            'logout_time': None if is_ongoing else last.captured_at,
            'duration': duration, 'status': status,
            'blocks': blocks, 'rows': rows, 'sample_count': len(run),
            'scheduling_session': first.session,
        })

    for log in logs:
        starts_new_run = (
            prev_pc_id is not None and (
                log.pc_id != prev_pc_id
                or log.student_id != prev_student_id
                or (log.captured_at - prev_time) > gap
            )
        )
        if starts_new_run:
            flush(run)
            run = []
        run.append(log)
        prev_pc_id, prev_student_id, prev_time = log.pc_id, log.student_id, log.captured_at
    flush(run)

    return sessions


def _pc_activity_filter_logs(request, base_qs):
    """Applies the shared lab/pc/student/day/time_range/search filters used
    across the PC activity log page, its export, and its delete action."""
    from django.db.models import Q

    lab_id = request.GET.get('lab') or request.POST.get('lab', '')
    pc_id = request.GET.get('pc') or request.POST.get('pc', '')
    student_id = request.GET.get('student') or request.POST.get('student', '')
    day = request.GET.get('day') or request.POST.get('day', '')
    time_range = request.GET.get('time_range') or request.POST.get('time_range', '')
    q = (request.GET.get('q') or request.POST.get('q', '')).strip()

    logs = base_qs
    if lab_id:
        logs = logs.filter(pc__lab_id=lab_id)
    if pc_id:
        logs = logs.filter(pc_id=pc_id)
    if student_id:
        logs = logs.filter(student_id=student_id)
    if day:
        logs = logs.filter(captured_at__date=day)
    if time_range in ('morning', 'afternoon', 'evening'):
        lo, hi = {'morning': (6, 12), 'afternoon': (12, 18), 'evening': (18, 24)}[time_range]
        logs = logs.filter(captured_at__hour__gte=lo, captured_at__hour__lt=hi)
    if q:
        logs = logs.filter(
            Q(window_title__icontains=q) |
            Q(student__first_name__icontains=q) |
            Q(student__last_name__icontains=q) |
            Q(student__id_number__icontains=q) |
            Q(pc__pc_id__icontains=q)
        )
    filters = {
        'lab': lab_id, 'pc': pc_id, 'student': student_id,
        'day': day, 'time_range': time_range, 'q': q,
    }
    return logs, filters


# Dashboard page listing the window-title samples collected by
# pc_agent_activity_api — lets admins/in-charges trace what a student was
# doing (per the window titles reported) on a given PC/session. Raw samples
# are reconstructed into login -> app-switch -> logout "sessions" (see
# _build_pc_sessions) to drive the activity timeline, session detail panel,
# and the Most Used Applications / Most Active PCs summaries.
class PCActivityLogView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/pc_activity_log.html'

    # A gap this long between two samples on the same PC/student means the
    # PC was locked (or the student switched) in between — used to split
    # the raw sample stream into distinct sessions.
    SESSION_GAP = timedelta(minutes=2)
    PAGE_SIZE = 10

    def get_context_data(self, **kwargs):
        from collections import defaultdict
        from django.contrib.auth import get_user_model

        ctx = super().get_context_data(**kwargs)
        request = self.request
        now = timezone.localtime()
        today = now.date()

        logs, filters = _pc_activity_filter_logs(
            request,
            PCActivityLog.objects.select_related('pc', 'pc__lab', 'student', 'session'),
        )
        logs = logs.order_by('pc_id', 'captured_at')
        sessions = _build_pc_sessions(list(logs[:8000]), now, self.SESSION_GAP)
        sessions.sort(key=lambda s: s['login_time'], reverse=True)

        timeline = [{
            'time': s['login_time'],
            'student': s['student'],
            'pc': s['pc'],
            'duration_display': _pc_activity_fmt_duration(s['duration']) if s['duration'] else '—',
            'status': s['status'],
            'session_key': s['key'],
        } for s in sessions]

        try:
            page = int(request.GET.get('page', '1'))
        except ValueError:
            page = 1
        total_entries = len(timeline)
        total_pages = max(1, -(-total_entries // self.PAGE_SIZE))
        page = min(max(page, 1), total_pages)
        page_rows = timeline[(page - 1) * self.PAGE_SIZE: page * self.PAGE_SIZE]

        selected_key = request.GET.get('session', '')
        selected = next((s for s in sessions if s['key'] == selected_key), None) or (sessions[0] if sessions else None)

        # ---- Stat cards: read straight off live system state / today's
        # totals rather than the current filter, so they read like a
        # steady dashboard summary instead of jumping around as filters
        # are applied to the table below. ----
        today_sessions = _build_pc_sessions(
            list(PCActivityLog.objects.select_related('pc', 'student')
                 .filter(captured_at__date=today).order_by('pc_id', 'captured_at')),
            now, self.SESSION_GAP,
        )
        durations_today = [s['duration'] for s in today_sessions if s['duration']]
        avg_duration = (sum(durations_today, timedelta()) / len(durations_today)) if durations_today else None

        total_pcs = PC.objects.count()
        active_pcs_now = PC.objects.filter(status='in_use').count()
        active_students_now = PC.objects.filter(
            status='in_use', current_user__isnull=False
        ).values('current_user').distinct().count()

        ctx['stats'] = {
            'total_sessions_today': len(today_sessions),
            'active_students_now': active_students_now,
            'total_logs_today': PCActivityLog.objects.filter(captured_at__date=today).count(),
            'avg_session_duration': _pc_activity_fmt_duration(avg_duration) if avg_duration else '—',
            'active_pcs_now': active_pcs_now,
            'pc_utilization_pct': round(active_pcs_now / total_pcs * 100) if total_pcs else 0,
        }

        # ---- Most Used Applications (today) ----
        app_totals = defaultdict(timedelta)
        for s in today_sessions:
            for b in s['blocks']:
                dur = b['end'] - b['start']
                if dur.total_seconds() == 0:
                    dur = timedelta(seconds=8)  # ~ one report interval
                app_totals[b['site_label']] += dur
        ranked_apps = sorted(app_totals.items(), key=lambda kv: kv[1], reverse=True)
        total_app_time = sum(app_totals.values(), timedelta()) or timedelta(seconds=1)
        palette = ['#a78bfa', '#f2a93b', '#3ed6c4', '#5b9dff', '#ff5d5d']
        most_used_apps = []
        for i, (name, dur) in enumerate(ranked_apps[:5]):
            most_used_apps.append({
                'name': name, 'duration': _pc_activity_fmt_duration(dur),
                'pct': round(dur / total_app_time * 100), 'color': palette[i % len(palette)],
                'raw_seconds': dur.total_seconds(),
            })
        others = sum((d for _, d in ranked_apps[5:]), timedelta())
        if others.total_seconds() > 0:
            most_used_apps.append({
                'name': 'Others', 'duration': _pc_activity_fmt_duration(others),
                'pct': round(others / total_app_time * 100), 'color': '#4d5761',
                'raw_seconds': others.total_seconds(),
            })
        ctx['most_used_apps'] = most_used_apps

        # ---- Most Active PCs (today) ----
        pc_totals = defaultdict(timedelta)
        for s in today_sessions:
            pc_totals[s['pc']] += s['duration'] or timedelta()
        ranked_pcs = sorted(pc_totals.items(), key=lambda kv: kv[1], reverse=True)[:5]
        max_pc_dur = ranked_pcs[0][1] if ranked_pcs else timedelta(seconds=1)
        ctx['most_active_pcs'] = [
            {'pc': pc, 'duration': _pc_activity_fmt_duration(dur),
             'pct': round(dur / max_pc_dur * 100) if max_pc_dur else 0}
            for pc, dur in ranked_pcs
        ]

        ctx['timeline'] = page_rows
        ctx['selected_session'] = selected
        ctx['selected_session_blocks_recent_first'] = (
            list(reversed(selected['blocks'])) if selected else []
        )
        ctx['selected_session_duration_long'] = (
            _pc_activity_fmt_duration(selected['duration'], long_form=True) if selected else None
        )
        ctx['page'] = page
        ctx['total_pages'] = total_pages
        ctx['total_entries'] = total_entries
        ctx['page_start'] = 0 if not total_entries else (page - 1) * self.PAGE_SIZE + 1
        ctx['page_end'] = min(page * self.PAGE_SIZE, total_entries)
        ctx['page_range'] = range(1, total_pages + 1)

        ctx['all_labs'] = Lab.objects.order_by('name')
        ctx['all_pcs'] = PC.objects.select_related('lab').order_by('lab__name', 'pc_id')
        ctx['all_students'] = get_user_model().objects.order_by('first_name', 'last_name')
        ctx['selected_lab'] = filters['lab']
        ctx['selected_pc'] = filters['pc']
        ctx['selected_student'] = filters['student']
        ctx['selected_day'] = filters['day']
        ctx['selected_time_range'] = filters['time_range']
        ctx['search_query'] = filters['q']
        ctx['filtered'] = any(filters.values())
        ctx['total_count'] = PCActivityLog.objects.count()
        return ctx



def delete_pc_activity_log(request):
    """Manually deletes PCActivityLog rows — same filters (lab/pc/student/
    day/time_range/q) as the on-screen view, so 'delete filtered' matches
    what's actually shown. Leaving every filter blank deletes EVERYTHING
    (confirmed client-side via compulabConfirmSubmit before this is ever
    hit)."""
    from labs.reports import _require_report_access

    _require_report_access(request)

    if request.method != 'POST':
        return redirect('pc_activity_log')

    logs, _filters = _pc_activity_filter_logs(request, PCActivityLog.objects.all())

    count, _ = logs.delete()
    messages.success(request, f'Deleted {count} activity log sample{"s" if count != 1 else ""}.')

    # Bounce back to the same filtered view the delete was triggered from,
    # preserving view=grouped/flat too.
    params = request.POST.get('return_qs', '')
    url = reverse('pc_activity_log')
    return redirect(f'{url}?{params}' if params else url)


def export_pc_activity_log(request):
    """Exports the currently filtered PCActivityLog view as PDF or Excel —
    same filters (lab/pc/student/day/time_range/q) as PCActivityLogView,
    but the full filtered set rather than the on-screen page. Handy for
    keeping a copy before the daily auto-wipe deletes everything."""
    from labs.reports import _require_report_access, _export_pdf, _export_excel

    _require_report_access(request)

    logs, filters = _pc_activity_filter_logs(
        request,
        PCActivityLog.objects.select_related('pc', 'pc__lab', 'student', 'session'),
    )
    logs = logs.order_by('-captured_at')

    headers = ['Time', 'PC', 'User', 'Role', 'Session', 'Active window title']
    rows = [
        [
            log.captured_at.strftime('%Y-%m-%d %H:%M:%S'),
            f'{log.pc.lab.name} — {log.pc.pc_id}',
            log.student.display_name if log.student else '—',
            log.student.get_role_display() if log.student else '—',
            f'{log.session.requester_name} ({log.session.date} {log.session.start_time}-{log.session.end_time})' if log.session else '—',
            log.window_title or '—',
        ]
        for log in logs
    ]

    day = filters['day']
    period = day if day else ('All time' if not filters['pc'] and not filters['q'] else 'Filtered')
    fmt = request.GET.get('format', 'pdf')
    if fmt == 'excel':
        return _export_excel('PC Activity Log', period, headers, rows, 'pc_activity_log.xlsx')
    return _export_pdf('PC Activity Log', period, headers, rows, 'pc_activity_log.pdf')


# API endpoint — returns live PC status as JSON for auto-refresh
def pc_status_api(request):
    pcs = PC.objects.select_related('current_user').values(
        'pc_id', 'status', 'lab__name', 'last_active',
        'current_user__id_number', 'current_user__first_name', 'current_user__last_name',
    )
    return JsonResponse({'pcs': list(pcs)})


# update a single PC's status (admin/incharge action)
class PCUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = ['admin', 'incharge']
    model = PC
    fields = ['status']
    template_name = 'labs/pc_form.html'

    def form_valid(self, form):
        previous_status = PC.objects.get(pk=self.object.pk).status
        response = super().form_valid(form)
        if self.object.status in ('maintenance', 'issue') and previous_status not in ('maintenance', 'issue'):
            notify_service.notify_pc_maintenance(self.object, self.object.get_status_display())
        return response

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
        qs = Lab.objects.prefetch_related('pcs', 'assigned_users').order_by('name')
        lab_id = self.request.GET.get('lab', '')
        status = self.request.GET.get('status', '')
        q = self.request.GET.get('q', '').strip()

        if lab_id:
            qs = qs.filter(pk=lab_id)
        if status:
            qs = qs.filter(pcs__status=status).distinct()
        if q:
            from django.db.models import Q
            qs = qs.filter(
                Q(name__icontains=q) | Q(location__icontains=q) |
                Q(assigned_users__first_name__icontains=q) |
                Q(assigned_users__last_name__icontains=q)
            ).distinct()
        return qs

    def get_context_data(self, **kwargs):
        from accounts.models import ActivityLog
        ctx = super().get_context_data(**kwargs)

        ctx['selected_lab'] = self.request.GET.get('lab', '')
        ctx['selected_status'] = self.request.GET.get('status', '')
        ctx['search_query'] = self.request.GET.get('q', '').strip()
        ctx['filtered'] = bool(ctx['selected_lab'] or ctx['selected_status'] or ctx['search_query'])
        ctx['all_labs'] = Lab.objects.order_by('name')
        ctx['status_choices'] = PC.STATUS

        # ---- per-lab breakdown used by each card ----
        lab_cards = []
        for lab in ctx['labs']:
            pcs = list(lab.pcs.all())
            total = len(pcs)
            available = sum(1 for p in pcs if p.status == 'online')
            in_use = sum(1 for p in pcs if p.status == 'in_use')
            offline = sum(1 for p in pcs if p.status == 'offline')
            maintenance = sum(1 for p in pcs if p.status == 'maintenance')
            online = total - offline
            incharge = next((u for u in lab.assigned_users.all() if u.role == 'incharge'), None)
            lab_cards.append({
                'lab': lab, 'total': total, 'online': online, 'available': available,
                'in_use': in_use, 'offline': offline, 'maintenance': maintenance,
                'incharge': incharge,
                'utilization_pct': round(in_use / total * 100) if total else 0,
            })
        ctx['lab_cards'] = lab_cards

        # ---- system-wide top metric cards (unfiltered, across every lab) ----
        all_pcs = PC.objects.all()
        ctx['total_labs'] = Lab.objects.count()
        ctx['total_computers'] = all_pcs.count()
        ctx['available_count'] = all_pcs.filter(status='online').count()
        ctx['in_use_count'] = all_pcs.filter(status='in_use').count()
        ctx['maintenance_count'] = all_pcs.filter(status='maintenance').count()
        ctx['offline_count'] = all_pcs.filter(status='offline').count()

        def pct(part):
            return round(part / ctx['total_computers'] * 100, 2) if ctx['total_computers'] else 0
        ctx['available_pct'] = pct(ctx['available_count'])
        ctx['in_use_pct'] = pct(ctx['in_use_count'])
        ctx['maintenance_pct'] = pct(ctx['maintenance_count'])
        ctx['offline_pct'] = pct(ctx['offline_count'])

        # ---- today's lab overview — utilization bars, same metric as the
        # per-card one but for every lab regardless of the current filter ----
        overview = []
        for lab in ctx['all_labs']:
            lab_total = lab.pcs.count()
            lab_in_use = lab.pcs.filter(status='in_use').count()
            overview.append({
                'lab': lab, 'pct': round(lab_in_use / lab_total * 100) if lab_total else 0,
            })
        ctx['lab_overview'] = overview

        ctx['recent_activities'] = ActivityLog.objects.select_related('actor', 'pc', 'pc__lab').order_by('-created_at')[:6]

        return ctx


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
    form_class = PCForm
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


class PCImportView(RoleRequiredMixin, FormView):
    """
    Bulk-add PCs from an uploaded CSV or Excel file instead of the one-by-one
    'Add PC' form. Expected columns (header row, any order): Lab, PC ID,
    IP Address (optional), Status (optional — defaults to Offline).
    Rows are validated the same way the single PCForm would validate them
    (loopback IP rejected, lab must already exist, duplicate PC ID within
    the same lab is skipped) and the whole thing runs in one DB transaction
    so a bad file never leaves a half-imported mess behind.
    """
    allowed_roles = ['admin']
    form_class = PCImportForm
    template_name = 'labs/pc_import.html'

    def form_valid(self, form):
        import csv
        import io
        import ipaddress as ipaddress_mod
        from django.db import transaction

        uploaded = form.cleaned_data['file']
        status_labels = {label.lower(): key for key, label in PC.STATUS}
        status_keys = dict(PC.STATUS)

        rows = []
        if uploaded.name.lower().endswith('.csv'):
            text = uploaded.read().decode('utf-8-sig', errors='replace')
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                rows.append({(k or '').strip().lower(): (v or '').strip() for k, v in row.items()})
        else:
            import openpyxl
            wb = openpyxl.load_workbook(uploaded, data_only=True)
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            headers = [str(h or '').strip().lower() for h in next(rows_iter, [])]
            for raw in rows_iter:
                if not any(raw):
                    continue
                rows.append({headers[i]: ('' if raw[i] is None else str(raw[i]).strip())
                             for i in range(len(headers)) if i < len(raw)})

        labs_by_name = {lab.name.strip().lower(): lab for lab in Lab.objects.all()}
        existing_pairs = {(pc.lab_id, pc.pc_id.strip().lower()) for pc in PC.objects.all()}

        created, errors = [], []
        to_create = []
        seen_in_file = set()

        for i, row in enumerate(rows, start=2):  # row 1 is the header
            lab_name = row.get('lab', '')
            pc_id = row.get('pc id') or row.get('pc_id') or row.get('id', '')
            ip_address = row.get('ip address') or row.get('ip_address') or row.get('ip', '')
            status_raw = (row.get('status', '') or '').strip()

            if not lab_name or not pc_id:
                errors.append(f'Row {i}: missing Lab or PC ID — skipped.')
                continue

            lab = labs_by_name.get(lab_name.strip().lower())
            if not lab:
                errors.append(f'Row {i}: lab "{lab_name}" does not exist — skipped.')
                continue

            key = (lab.pk, pc_id.strip().lower())
            if key in existing_pairs or key in seen_in_file:
                errors.append(f'Row {i}: "{pc_id}" already exists in {lab.name} — skipped.')
                continue

            if ip_address:
                try:
                    if ipaddress_mod.ip_address(ip_address).is_loopback:
                        errors.append(f'Row {i}: "{ip_address}" is a loopback address — imported without an IP.')
                        ip_address = ''
                except ValueError:
                    errors.append(f'Row {i}: "{ip_address}" is not a valid IP address — imported without an IP.')
                    ip_address = ''

            status = status_keys.get(status_raw) or status_labels.get(status_raw.lower()) or 'offline'

            to_create.append(PC(lab=lab, pc_id=pc_id, ip_address=ip_address or None, status=status))
            seen_in_file.add(key)
            created.append(f'{pc_id} → {lab.name}')

        with transaction.atomic():
            PC.objects.bulk_create(to_create)

        if created:
            messages.success(self.request, f'Imported {len(created)} PC(s) successfully.')
        if errors:
            messages.warning(self.request, f'{len(errors)} row(s) had issues — see details below.')

        return self.render_to_response(self.get_context_data(form=self.form_class(), results={'created': created, 'errors': errors}))


class PCEditView(RoleRequiredMixin, UpdateView):
    allowed_roles = ['admin']
    model = PC
    form_class = PCForm
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


# inventory list — stat cards, search/filter bar, table, and the bottom
# category/condition breakdown + low stock + recent activity panels.
class InventoryView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/inventory.html'
    context_object_name = 'items'
    paginate_by = 10

    # equipment at or below this quantity shows up in the Low Stock panel
    LOW_STOCK_THRESHOLD = 3

    def get_queryset(self):
        qs = InventoryItem.objects.select_related('lab').order_by('lab__name', 'category', 'name')

        self.search = self.request.GET.get('q', '').strip()
        self.lab_id = self.request.GET.get('lab', '').strip()
        self.category = self.request.GET.get('category', '').strip()
        self.condition = self.request.GET.get('condition', '').strip()
        self.status = self.request.GET.get('status', '').strip()

        if self.search:
            qs = qs.filter(name__icontains=self.search)
        if self.lab_id:
            qs = qs.filter(lab_id=self.lab_id)
        if self.category:
            qs = qs.filter(category=self.category)
        if self.condition:
            qs = qs.filter(condition=self.condition)
        if self.status:
            qs = qs.filter(status=self.status)
        return qs

    def get_context_data(self, **kwargs):
        from django.db.models import Count, F

        ctx = super().get_context_data(**kwargs)
        all_items = InventoryItem.objects.all()

        total_items = all_items.count()
        working = all_items.filter(status='operational').count()
        needs_check = all_items.filter(condition='needs_check').count()
        faulty = all_items.filter(condition='faulty').count()
        under_repair = all_items.filter(status='under_repair').count()

        def pct(n):
            return round(n / total_items * 100, 2) if total_items else 0

        ctx.update({
            'total_items': total_items,
            'working_count': working, 'working_pct': pct(working),
            'needs_check_count': needs_check, 'needs_check_pct': pct(needs_check),
            'faulty_count': faulty, 'faulty_pct': pct(faulty),
            'under_repair_count': under_repair, 'under_repair_pct': pct(under_repair),
        })

        # --- search/filter bar options + currently selected values ---
        ctx['all_labs'] = Lab.objects.order_by('name')
        ctx['all_categories'] = (
            all_items.exclude(category='').values_list('category', flat=True)
            .order_by('category').distinct()
        )
        ctx['condition_choices'] = InventoryItem.CONDITION
        ctx['status_choices'] = InventoryItem.STATUS
        ctx['search_q'] = self.search
        ctx['selected_lab'] = self.lab_id
        ctx['selected_category'] = self.category
        ctx['selected_condition'] = self.condition
        ctx['selected_status'] = self.status
        ctx['filters_active'] = any([self.search, self.lab_id, self.category, self.condition, self.status])

        # --- "Equipment by Category" donut ---
        palette = ['#5b9dff', '#a78bfa', '#3ed6c4', '#f2a93b', '#ff5d5d', '#8b98a3']
        category_breakdown = list(
            all_items.exclude(category='').values('category').annotate(n=Count('id')).order_by('-n')
        )
        for i, row in enumerate(category_breakdown):
            row['pct'] = pct(row['n'])
            row['color'] = palette[i % len(palette)]
        ctx['category_breakdown'] = category_breakdown

        # --- "Equipment Condition" donut (reuses the stat-card numbers) ---
        ctx['condition_breakdown'] = [
            {'label': 'Working', 'n': working, 'pct': pct(working), 'color': '#3ed6c4'},
            {'label': 'Needs Check', 'n': needs_check, 'pct': pct(needs_check), 'color': '#f2a93b'},
            {'label': 'Faulty', 'n': faulty, 'pct': pct(faulty), 'color': '#ff5d5d'},
            {'label': 'Under Repair', 'n': under_repair, 'pct': pct(under_repair), 'color': '#a78bfa'},
        ]

        # --- low stock alert ---
        ctx['low_stock_items'] = (
            all_items.filter(quantity__lte=self.LOW_STOCK_THRESHOLD)
            .select_related('lab').order_by('quantity')[:5]
        )

        # --- recent activity (merged feed: added, updated, repaired, reported) ---
        activity = []
        for item in all_items.select_related('lab').order_by('-created_at')[:5]:
            if item.created_at:
                activity.append({'title': f'{item.name} added to {item.lab.name}', 'time': item.created_at})
        for item in all_items.select_related('lab').filter(updated_at__gt=F('created_at')).order_by('-updated_at')[:5]:
            if item.updated_at:
                activity.append({'title': f'{item.name} updated in {item.lab.name}', 'time': item.updated_at})
        for log in MaintenanceLog.objects.filter(completed=True).select_related('equipment', 'equipment__lab').order_by('-completed_at')[:5]:
            if log.completed_at:
                activity.append({'title': f'{log.equipment.name} repaired in {log.equipment.lab.name}', 'time': log.completed_at})
        for issue in EquipmentIssue.objects.select_related('equipment', 'equipment__lab').order_by('-date_reported')[:5]:
            activity.append({'title': f'{issue.equipment.name} reported as faulty in {issue.equipment.lab.name}', 'time': issue.date_reported})
        activity.sort(key=lambda a: a['time'], reverse=True)
        ctx['recent_activity'] = activity[:6]

        return ctx


# analytics — Reporting & Analytics overview. Shared by admin (sees every
# lab) and lab incharge (forced to their own lab) — same layout, scoped data.
class AnalyticsView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/analytics.html'

    def get_context_data(self, **kwargs):
        from issues.models import Issue
        from labs.reports import _session_hours, _scoped_lab_id, _visible_labs
        from scheduling.models import Session, SessionCheckIn
        from labs.models import ReportLog
        from django.contrib.auth import get_user_model
        from django.db.models import Q
        from django.utils import timezone
        from datetime import timedelta, date as date_cls, datetime
        import calendar

        User = get_user_model()
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        today = timezone.now().date()

        # --- date range filter (preset dropdown, or custom date_from/date_to) ---
        range_key = self.request.GET.get('range', 'this_month')
        if range_key == 'custom' and self.request.GET.get('date_from') and self.request.GET.get('date_to'):
            try:
                date_from = datetime.strptime(self.request.GET['date_from'], '%Y-%m-%d').date()
                date_to = datetime.strptime(self.request.GET['date_to'], '%Y-%m-%d').date()
            except ValueError:
                range_key = 'this_month'
                date_from = date_to = None
        else:
            date_from = date_to = None

        if date_from is None:
            if range_key == 'last_month':
                last_day_prev = today.replace(day=1) - timedelta(days=1)
                date_from, date_to = last_day_prev.replace(day=1), last_day_prev
            elif range_key == 'this_week':
                date_from, date_to = today - timedelta(days=today.weekday()), today
            elif range_key == 'last_7_days':
                date_from, date_to = today - timedelta(days=6), today
            else:
                range_key = 'this_month'
                date_from, date_to = today.replace(day=1), today

        ctx['range_key'] = range_key
        ctx['date_from'] = date_from
        ctx['date_to'] = date_to

        # --- lab / instructor / status filters ---
        lab_id = _scoped_lab_id(self.request, self.request.GET.get('lab', ''))
        instructor_id = self.request.GET.get('instructor', '')
        status_filter = self.request.GET.get('status', '')

        ctx['selected_lab'] = lab_id
        ctx['selected_instructor'] = instructor_id
        ctx['selected_status'] = status_filter
        ctx['is_incharge'] = (user.role == 'incharge')
        ctx['all_labs'] = _visible_labs(self.request)
        ctx['instructors'] = User.objects.filter(role='instructor').order_by('first_name', 'last_name')
        ctx['status_choices'] = Issue.STATUS

        labs_qs = Lab.objects.filter(pk=lab_id) if lab_id else Lab.objects.all()
        labs_qs = labs_qs.order_by('name')

        # --- top metric cards ---
        pcs_qs = PC.objects.filter(lab__in=labs_qs)
        ctx['total_labs'] = labs_qs.count()
        ctx['total_pcs'] = pcs_qs.count()

        sessions_qs = Session.objects.filter(
            lab__in=labs_qs, date__gte=date_from, date__lte=date_to
        ).select_related('lab', 'instructor')
        if instructor_id:
            sessions_qs = sessions_qs.filter(instructor_id=instructor_id)
        sessions_list = list(sessions_qs)
        ctx['total_reservations'] = len(sessions_list)

        expected = sum(s.student_count for s in sessions_list)
        checked_in = SessionCheckIn.objects.filter(session__in=sessions_list).values('id_number').distinct().count() if sessions_list else 0
        ctx['attendance_rate'] = round(checked_in / expected * 100) if expected else 0

        issues_qs = Issue.objects.filter(lab__in=labs_qs)
        ctx['maintenance_issues_pending'] = issues_qs.filter(status='open').count()

        # --- lab utilization (bars + "most used laboratories") ---
        num_days = (date_to - date_from).days + 1
        utilization_rows = []
        for lab in labs_qs:
            lab_sessions = [s for s in sessions_list if s.lab_id == lab.id]
            total_hours = sum(_session_hours(s) for s in lab_sessions)
            open_hours = (
                datetime.combine(date_cls.min, lab.closing_time) -
                datetime.combine(date_cls.min, lab.opening_time)
            ).total_seconds() / 3600
            capacity_hours = open_hours * num_days
            pct = round(total_hours / capacity_hours * 100) if capacity_hours > 0 else 0
            utilization_rows.append({'lab': lab, 'sessions': len(lab_sessions), 'pct': min(pct, 100)})
        utilization_rows.sort(key=lambda r: r['pct'], reverse=True)
        ctx['utilization_rows'] = utilization_rows
        ctx['lab_utilization_avg'] = (
            round(sum(r['pct'] for r in utilization_rows) / len(utilization_rows))
            if utilization_rows else 0
        )

        # --- equipment status donut: PC health, not inventory items, since
        # the "Total PCs" metric above is what this chart's total matches ---
        working = pcs_qs.filter(status__in=['online', 'in_use']).count()
        maintenance = pcs_qs.filter(status='maintenance').count()
        damaged = pcs_qs.filter(status__in=['offline', 'issue']).count()
        total_for_donut = max(working + maintenance + damaged, 1)
        ctx['equipment_status'] = {
            'working': working, 'maintenance': maintenance, 'damaged': damaged,
            'working_pct': round(working / total_for_donut * 100),
            'maintenance_pct': round(maintenance / total_for_donut * 100),
            'damaged_pct': round(damaged / total_for_donut * 100),
        }

        # --- attendance trend: weekly buckets across the selected range ---
        week_buckets = []
        cursor = date_from
        bucket_index = 1
        while cursor <= date_to and bucket_index <= 6:
            bucket_end = min(cursor + timedelta(days=6), date_to)
            bucket_sessions = [s for s in sessions_list if cursor <= s.date <= bucket_end]
            bucket_expected = sum(s.student_count for s in bucket_sessions)
            bucket_checked_in = SessionCheckIn.objects.filter(
                session__in=bucket_sessions
            ).values('id_number').distinct().count() if bucket_sessions else 0
            week_buckets.append({
                'label': f'Week {bucket_index}',
                'pct': round(bucket_checked_in / bucket_expected * 100) if bucket_expected else 0,
            })
            cursor += timedelta(days=7)
            bucket_index += 1
        ctx['attendance_trend'] = week_buckets

        # --- reservations trend: last 6 calendar months, independent of the
        # date-range filter (a short-term filter shouldn't erase the trend) ---
        trend = []
        # build the last 6 (year, month) pairs ending at the current month
        pairs = []
        y, m = today.year, today.month
        for _ in range(6):
            pairs.append((y, m))
            m -= 1
            if m == 0:
                m = 12
                y -= 1
        pairs.reverse()
        for y, m in pairs:
            count = Session.objects.filter(lab__in=labs_qs, date__year=y, date__month=m).count()
            trend.append({'label': calendar.month_abbr[m], 'count': count})
        ctx['reservations_trend'] = trend

        # --- top active instructors (this range) ---
        by_instructor = {}
        for s in sessions_list:
            if not s.instructor_id:
                continue
            entry = by_instructor.setdefault(s.instructor, {'sessions': 0})
            entry['sessions'] += 1
        top_instructors = sorted(
            ({'instructor': i, 'sessions': v['sessions']} for i, v in by_instructor.items()),
            key=lambda r: r['sessions'], reverse=True
        )[:5]
        max_sessions = max((r['sessions'] for r in top_instructors), default=1)
        for r in top_instructors:
            r['bar_pct'] = round(r['sessions'] / max_sessions * 100) if max_sessions else 0
        ctx['top_instructors'] = top_instructors

        # --- maintenance summary ---
        ctx['maintenance_summary'] = {
            'pending': issues_qs.filter(status='open').count(),
            'resolved': issues_qs.filter(status='resolved').count(),
            'under_repair': InventoryItem.objects.filter(lab__in=labs_qs, status='under_repair').count(),
            'total': issues_qs.count(),
        }

        # --- issue breakdown by type, honoring the Status filter ---
        from django.db.models import Count
        breakdown_qs = issues_qs
        if status_filter:
            breakdown_qs = breakdown_qs.filter(status=status_filter)
        ctx['issue_breakdown'] = breakdown_qs.values('issue_type').annotate(
            count=Count('id')
        ).order_by('-count')

        # --- recent generated reports ---
        recent_reports = ReportLog.objects.select_related('generated_by', 'lab')
        if user.role == 'incharge' and user.assigned_lab_id:
            recent_reports = recent_reports.filter(
                Q(lab_id=user.assigned_lab_id) | Q(generated_by=user)
            )
        ctx['recent_reports'] = recent_reports.order_by('-generated_at')[:8]

        return ctx


# Admin sees every notification in the system (per spec: "Admin: View all
# notifications"). Lab In-Charge only sees what notifications/services.py
# actually addressed to them — which is already scoped to their own
# assigned lab, so no extra lab filter is needed here.
class AlertsView(RoleRequiredMixin, ListView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/alerts.html'
    context_object_name = 'alerts'

    def get_base_queryset(self):
        from notifications.models import Notification
        if self.request.user.role == 'admin':
            return Notification.objects.all()
        return Notification.objects.filter(user=self.request.user)

    def get_queryset(self):
        from notifications.filters import apply_filters
        return apply_filters(self.get_base_queryset(), self.request)

    def get_context_data(self, **kwargs):
        from notifications.filters import stat_counts, CATEGORY_CHOICES
        ctx = super().get_context_data(**kwargs)
        ctx['stats'] = stat_counts(self.get_base_queryset())
        ctx['category_choices'] = CATEGORY_CHOICES
        ctx['selected_category'] = self.request.GET.get('category', 'all')
        ctx['q'] = self.request.GET.get('q', '')
        ctx['sort'] = self.request.GET.get('sort', 'latest')
        return ctx


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
        notify_service.notify_maintenance_submitted(self.object)
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
        notify_service.notify_maintenance_completed(log)
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
        notify_service.notify_equipment_issue_reported(self.object)
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
        notify_service.notify_equipment_issue_resolved(equipment_issue)
        messages.success(request, 'Resolution Saved Successfully.')
    return redirect('equipment_issues')