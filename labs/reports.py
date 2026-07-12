"""
Reporting & Analytics — Lab Utilization, Instructor Usage, Attendance, and
Maintenance reports. Each report is: a filter form (GET params) -> computed
data -> an on-screen table -> optional PDF/Excel export using the same
filtered queryset/computation.
"""
from datetime import datetime, timedelta, date as date_cls

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.utils import timezone
from django.views.generic import TemplateView

from accounts.mixins import RoleRequiredMixin
from accounts.models import ActivityLog
from labs.models import Lab, InventoryItem, MaintenanceLog, EquipmentIssue

User = get_user_model()


def _require_report_access(request):
    if not request.user.is_authenticated:
        raise PermissionDenied
    if request.user.role not in ('admin', 'incharge'):
        raise PermissionDenied


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------

def _parse_date(value, fallback):
    if not value:
        return fallback
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return fallback


def _default_range():
    today = timezone.now().date()
    return today - timedelta(days=30), today


def _session_hours(session):
    """Duration of a Session in hours (float), guarding against bad data."""
    start = datetime.combine(session.date, session.start_time)
    end = datetime.combine(session.date, session.end_time)
    seconds = (end - start).total_seconds()
    return max(seconds, 0) / 3600


def _peak_hours(sessions):
    """Returns a list of (hour, session_count) sorted by count desc, for the
    hour-of-day buckets that at least one session overlaps."""
    buckets = {}
    for s in sessions:
        start_hour = s.start_time.hour
        end_hour = s.end_time.hour if s.end_time.minute or s.end_time.second else s.end_time.hour - 1
        end_hour = max(end_hour, start_hour)
        for h in range(start_hour, end_hour + 1):
            buckets[h] = buckets.get(h, 0) + 1
    return sorted(buckets.items(), key=lambda pair: (-pair[1], pair[0]))


def _fmt_hour(h):
    suffix = 'AM' if h < 12 else 'PM'
    display = h % 12
    if display == 0:
        display = 12
    return f'{display}:00 {suffix}'


# ---------------------------------------------------------------------------
# Report 1 — Lab Utilization
# ---------------------------------------------------------------------------

def _lab_utilization_data(request):
    from scheduling.models import Session

    default_from, default_to = _default_range()
    date_from = _parse_date(request.GET.get('date_from'), default_from)
    date_to = _parse_date(request.GET.get('date_to'), default_to)
    lab_id = request.GET.get('lab', '')

    sessions = Session.objects.filter(date__gte=date_from, date__lte=date_to)
    if lab_id:
        sessions = sessions.filter(lab_id=lab_id)
    sessions = sessions.select_related('lab').order_by('lab__name', 'date', 'start_time')

    num_days = (date_to - date_from).days + 1
    labs = Lab.objects.filter(pk=lab_id) if lab_id else Lab.objects.all()

    rows = []
    for lab in labs.order_by('name'):
        lab_sessions = [s for s in sessions if s.lab_id == lab.id]
        total_hours = sum(_session_hours(s) for s in lab_sessions)
        open_hours = datetime.combine(date_cls.min, lab.closing_time) - datetime.combine(date_cls.min, lab.opening_time)
        capacity_hours = (open_hours.total_seconds() / 3600) * num_days
        utilization_pct = (total_hours / capacity_hours * 100) if capacity_hours > 0 else 0
        peak = _peak_hours(lab_sessions)
        rows.append({
            'lab': lab,
            'total_sessions': len(lab_sessions),
            'total_hours': round(total_hours, 1),
            'capacity_hours': round(capacity_hours, 1),
            'utilization_pct': round(utilization_pct, 1),
            'peak_hours': [(_fmt_hour(h), c) for h, c in peak[:3]],
        })

    return {
        'date_from': date_from, 'date_to': date_to, 'selected_lab': lab_id,
        'all_labs': Lab.objects.order_by('name'), 'rows': rows,
    }


class LabUtilizationReportView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/reports/lab_utilization.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_lab_utilization_data(self.request))
        return ctx


def export_lab_utilization(request):
    _require_report_access(request)
    data = _lab_utilization_data(request)
    fmt = request.GET.get('format', 'pdf')
    title = 'Lab Utilization Report'
    period = f"{data['date_from']} to {data['date_to']}"
    headers = ['Lab', 'Sessions', 'Total Hours', 'Capacity Hours', 'Utilization %', 'Peak Hours']
    body = [[
        r['lab'].name, r['total_sessions'], r['total_hours'], r['capacity_hours'],
        f"{r['utilization_pct']}%",
        ', '.join(f'{h} ({c})' for h, c in r['peak_hours']) or '—',
    ] for r in data['rows']]
    if fmt == 'excel':
        return _export_excel(title, period, headers, body, 'lab_utilization_report.xlsx')
    return _export_pdf(title, period, headers, body, 'lab_utilization_report.pdf')


# ---------------------------------------------------------------------------
# Report 1b — Equipment Status
# ---------------------------------------------------------------------------

def _equipment_status_data(request):
    default_from, default_to = _default_range()
    date_from = _parse_date(request.GET.get('date_from'), default_from)
    date_to = _parse_date(request.GET.get('date_to'), default_to)
    lab_id = request.GET.get('lab', '')

    items = InventoryItem.objects.select_related('lab').filter(
        last_checked__gte=date_from, last_checked__lte=date_to
    )
    if lab_id:
        items = items.filter(lab_id=lab_id)
    items = items.order_by('lab__name', 'name')

    rows = []
    for item in items:
        rows.append({
            'item': item,
            'open_issues': item.issues.filter(status='open').count(),
        })

    summary = {
        'total': len(rows),
        'operational': sum(1 for r in rows if r['item'].status == 'operational'),
        'under_repair': sum(1 for r in rows if r['item'].status == 'under_repair'),
        'retired': sum(1 for r in rows if r['item'].status == 'retired'),
    }

    return {
        'date_from': date_from, 'date_to': date_to, 'selected_lab': lab_id,
        'all_labs': Lab.objects.order_by('name'), 'rows': rows, 'summary': summary,
    }


class EquipmentStatusReportView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/reports/equipment_status.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_equipment_status_data(self.request))
        return ctx


def export_equipment_status(request):
    _require_report_access(request)
    data = _equipment_status_data(request)
    fmt = request.GET.get('format', 'pdf')
    title = 'Equipment Status Report'
    period = f"{data['date_from']} to {data['date_to']}"
    headers = ['Equipment', 'Category', 'Lab', 'Condition', 'Status', 'Quantity', 'Last Checked', 'Open Issues']
    body = [[
        r['item'].name, r['item'].category, r['item'].lab.name,
        r['item'].get_condition_display(), r['item'].get_status_display(),
        r['item'].quantity, r['item'].last_checked or '—', r['open_issues'],
    ] for r in data['rows']]
    if fmt == 'excel':
        return _export_excel(title, period, headers, body, 'equipment_status_report.xlsx')
    return _export_pdf(title, period, headers, body, 'equipment_status_report.pdf')


# ---------------------------------------------------------------------------
# Report 2 — Instructor Usage
# ---------------------------------------------------------------------------

def _instructor_usage_data(request):
    from scheduling.models import Session

    default_from, default_to = _default_range()
    date_from = _parse_date(request.GET.get('date_from'), default_from)
    date_to = _parse_date(request.GET.get('date_to'), default_to)
    instructor_id = request.GET.get('instructor', '')

    sessions = Session.objects.filter(
        date__gte=date_from, date__lte=date_to, instructor__isnull=False
    ).select_related('lab', 'instructor')
    if instructor_id:
        sessions = sessions.filter(instructor_id=instructor_id)

    by_instructor = {}
    for s in sessions:
        entry = by_instructor.setdefault(s.instructor, {'labs': set(), 'hours': 0.0, 'sessions': 0})
        entry['labs'].add(s.lab.name)
        entry['hours'] += _session_hours(s)
        entry['sessions'] += 1

    rows = [{
        'instructor': instr,
        'labs_used': sorted(v['labs']),
        'total_hours': round(v['hours'], 1),
        'num_sessions': v['sessions'],
    } for instr, v in by_instructor.items()]
    rows.sort(key=lambda r: r['instructor'].get_full_name() or r['instructor'].username)

    return {
        'date_from': date_from, 'date_to': date_to, 'selected_instructor': instructor_id,
        'instructors': User.objects.filter(role='instructor').order_by('first_name', 'last_name'),
        'rows': rows,
    }


class InstructorUsageReportView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/reports/instructor_usage.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_instructor_usage_data(self.request))
        return ctx


def export_instructor_usage(request):
    _require_report_access(request)
    data = _instructor_usage_data(request)
    fmt = request.GET.get('format', 'pdf')
    title = 'Instructor Usage Report'
    period = f"{data['date_from']} to {data['date_to']}"
    headers = ['Instructor', 'Laboratories Used', 'Total Hours', 'Number of Sessions']
    body = [[
        r['instructor'].get_full_name() or r['instructor'].username,
        ', '.join(r['labs_used']) or '—',
        r['total_hours'], r['num_sessions'],
    ] for r in data['rows']]
    if fmt == 'excel':
        return _export_excel(title, period, headers, body, 'instructor_usage_report.xlsx')
    return _export_pdf(title, period, headers, body, 'instructor_usage_report.pdf')


# ---------------------------------------------------------------------------
# Report 3 — Attendance
# ---------------------------------------------------------------------------

def _attendance_data(request):
    from scheduling.models import Session

    lab_id = request.GET.get('lab', '')
    day = _parse_date(request.GET.get('day'), timezone.now().date())

    sessions = Session.objects.filter(date=day).select_related('lab', 'roster')
    if lab_id:
        sessions = sessions.filter(lab_id=lab_id)
    sessions = sessions.order_by('lab__name', 'start_time')

    rows = []
    for s in sessions:
        start_dt = datetime.combine(day, s.start_time)
        end_dt = datetime.combine(day, s.end_time)
        logs = ActivityLog.objects.filter(
            action='pc_unlock', pc__lab_id=s.lab_id,
            created_at__gte=timezone.make_aware(start_dt) if timezone.is_naive(start_dt) else start_dt,
            created_at__lte=timezone.make_aware(end_dt) if timezone.is_naive(end_dt) else end_dt,
        ).order_by('created_at')

        roster_by_id = {}
        if s.roster_id:
            roster_by_id = {rs.id_number: rs.full_name for rs in s.roster.students.all()}

        present = []
        seen_ids = set()
        for log in logs:
            if log.target_username in seen_ids:
                continue
            seen_ids.add(log.target_username)
            name = roster_by_id.get(log.target_username) or s.requester_name or '—'
            present.append({
                'id_number': log.target_username,
                'name': name,
                'pc': log.pc.pc_id if log.pc else '—',
                'time': log.created_at,
            })

        if s.roster_id:
            # roster attached — absent list has real names, not just a count
            absent = [
                {'id_number': id_number, 'full_name': full_name}
                for id_number, full_name in roster_by_id.items()
                if id_number not in seen_ids
            ]
            expected = len(roster_by_id)
        else:
            absent = None  # no roster — names aren't knowable, only a count
            expected = s.student_count or 0

        present_count = len(present)
        absent_count = len(absent) if absent is not None else max(expected - present_count, 0)
        rows.append({
            'session': s,
            'has_roster': bool(s.roster_id),
            'present': present,
            'absent': absent,
            'present_count': present_count,
            'absent_count': absent_count,
            'expected_count': expected,
        })

    return {
        'day': day, 'selected_lab': lab_id,
        'all_labs': Lab.objects.order_by('name'), 'rows': rows,
    }


class AttendanceReportView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/reports/attendance.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_attendance_data(self.request))
        return ctx


def export_attendance(request):
    _require_report_access(request)
    data = _attendance_data(request)
    fmt = request.GET.get('format', 'pdf')
    title = 'Attendance Report'
    period = f"{data['day']}"
    headers = ['Lab', 'Subject', 'Time', 'Present', 'Absent', 'Time Logs', 'Absent Names (if roster attached)']
    body = []
    for r in data['rows']:
        s = r['session']
        time_logs = '; '.join(f"{p['name']} ({p['id_number']}) @ {timezone.localtime(p['time']).strftime('%H:%M')}" for p in r['present']) or '—'
        if r['has_roster']:
            absent_names = '; '.join(f"{a['full_name']} ({a['id_number']})" for a in r['absent']) or 'None'
        else:
            absent_names = 'No roster attached — estimate only'
        body.append([
            s.lab.name, s.subject, f'{s.start_time.strftime("%H:%M")}-{s.end_time.strftime("%H:%M")}',
            r['present_count'], r['absent_count'], time_logs, absent_names,
        ])
    if fmt == 'excel':
        return _export_excel(title, period, headers, body, 'attendance_report.xlsx')
    return _export_pdf(title, period, headers, body, 'attendance_report.pdf')


# ---------------------------------------------------------------------------
# Report 4 — Maintenance
# ---------------------------------------------------------------------------

def _maintenance_data(request):
    default_from, default_to = _default_range()
    date_from = _parse_date(request.GET.get('date_from'), default_from)
    date_to = _parse_date(request.GET.get('date_to'), default_to)
    equipment_id = request.GET.get('equipment', '')

    logs = MaintenanceLog.objects.filter(
        maintenance_date__gte=date_from, maintenance_date__lte=date_to
    ).select_related('equipment', 'assigned_technician')
    issues = EquipmentIssue.objects.filter(
        date_reported__date__gte=date_from, date_reported__date__lte=date_to
    ).select_related('equipment')
    damaged = InventoryItem.objects.filter(condition__in=['faulty', 'needs_check']) | \
        InventoryItem.objects.filter(status='retired')
    damaged = damaged.distinct().select_related('lab')

    if equipment_id:
        logs = logs.filter(equipment_id=equipment_id)
        issues = issues.filter(equipment_id=equipment_id)
        damaged = damaged.filter(pk=equipment_id)

    scheduled = logs.filter(completed=False).order_by('maintenance_date')
    performed = logs.filter(completed=True).order_by('-maintenance_date')

    return {
        'date_from': date_from, 'date_to': date_to, 'selected_equipment': equipment_id,
        'all_equipment': InventoryItem.objects.order_by('name'),
        'scheduled': scheduled, 'performed': performed, 'issues': issues, 'damaged': damaged,
    }


class MaintenanceReportView(RoleRequiredMixin, TemplateView):
    allowed_roles = ['admin', 'incharge']
    template_name = 'labs/reports/maintenance.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_maintenance_data(self.request))
        return ctx


def export_maintenance(request):
    _require_report_access(request)
    data = _maintenance_data(request)
    fmt = request.GET.get('format', 'pdf')
    title = 'Maintenance Report'
    period = f"{data['date_from']} to {data['date_to']}"

    headers = ['Section', 'Equipment', 'Date', 'Technician / Notes', 'Status']
    body = []
    for log in data['scheduled']:
        body.append(['Scheduled', log.equipment.name, str(log.maintenance_date),
                      log.assigned_technician.get_full_name() if log.assigned_technician else '—', 'Pending'])
    for log in data['performed']:
        body.append(['Repair performed', log.equipment.name, str(log.maintenance_date),
                      (log.completion_notes or log.notes)[:80], 'Completed'])
    for item in data['damaged']:
        body.append(['Lost/Damaged', item.name, str(item.last_checked or '—'),
                      f'{item.get_condition_display()} — {item.lab.name}', item.get_status_display()])

    if fmt == 'excel':
        return _export_excel(title, period, headers, body, 'maintenance_report.xlsx')
    return _export_pdf(title, period, headers, body, 'maintenance_report.pdf')


# ---------------------------------------------------------------------------
# generic PDF / Excel export helpers
# ---------------------------------------------------------------------------

def _export_pdf(title, period, headers, rows, filename):
    from io import BytesIO
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter), title=title)
    styles = getSampleStyleSheet()
    elements = [
        Paragraph(f'CompuLab — {title}', styles['Title']),
        Spacer(1, 4),
        Paragraph(f'Period: {period}', styles['Normal']),
        Paragraph(f'Generated {timezone.now().strftime("%B %d, %Y %H:%M")}', styles['Normal']),
        Spacer(1, 16),
    ]
    data = [headers] + [[str(cell) for cell in row] for row in rows] if rows else [headers, ['No data for this filter.'] + [''] * (len(headers) - 1)]
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
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


def _export_excel(title, period, headers, rows, filename):
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]

    ws.append([f'CompuLab — {title}'])
    ws.append([f'Period: {period}'])
    ws.append([f'Generated {timezone.now().strftime("%B %d, %Y %H:%M")}'])
    ws.append([])
    header_row = ws.max_row + 1
    ws.append(headers)
    for cell in ws[header_row]:
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='12171D')

    if rows:
        for row in rows:
            ws.append(list(row))
    else:
        ws.append(['No data for this filter.'])

    for col in ws.columns:
        length = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max(length + 2, 10), 50)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
