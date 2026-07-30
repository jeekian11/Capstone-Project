"""
Central place that creates AUTO-GENERATED notifications for every system
event listed in the Notifications & Alerts spec (new/approved/rejected
reservations, session reminders, PC online/offline, maintenance, walk-in/
override approvals, failed logins, reservation conflicts).

Views/signals should call the small `notify_*` functions below instead of
calling Notification.objects.create() directly, so that:
  1. every auto notification respects the Admin's Alert Settings toggles
  2. every auto notification lands on the right people (Admins always get
     critical/system-wide events; a Lab In-Charge only gets events for
     their OWN assigned lab; an Instructor only gets events tied to their
     own reservations/sessions)
"""
from django.contrib.auth import get_user_model
from notifications.models import Notification, AlertSettings

User = get_user_model()

# Which AlertSettings toggle gates each auto notification_type.
SETTING_FIELD_BY_TYPE = {
    'reservation_submitted': 'reservation_notifications',
    'reservation_approved': 'reservation_notifications',
    'reservation_declined': 'reservation_notifications',
    'reservation_conflict': 'reservation_notifications',
    'walkin_override_approved': 'reservation_notifications',
    'roster_session_scheduled': 'reservation_notifications',
    'roster_submitted': 'reservation_notifications',
    'roster_approved': 'reservation_notifications',
    'roster_rejected': 'reservation_notifications',
    'session_reminder': 'session_reminders',
    'pc_offline': 'pc_status_alerts',
    'pc_online': 'pc_status_alerts',
    'pc_maintenance': 'maintenance_alerts',
    'maintenance_submitted': 'maintenance_alerts',
    'maintenance_completed': 'maintenance_alerts',
    'login_security_alert': 'login_security_alerts',
}


def _event_enabled(notification_type):
    field = SETTING_FIELD_BY_TYPE.get(notification_type)
    if not field:
        return True  # manual/composed types are never gated
    return getattr(AlertSettings.get_solo(), field)


def admins():
    return User.objects.filter(is_active=True, role='admin')


def incharge_for_lab(lab):
    if lab is None:
        return User.objects.none()
    return User.objects.filter(is_active=True, role='incharge', assigned_lab=lab)


def notify(users, title, message, notification_type, lab=None):
    """Create one Notification per user in `users` for an auto-generated
    event, skipping entirely (for everyone) if the Admin has turned that
    event category off in Alert Settings. De-duplicates the recipient list."""
    if not _event_enabled(notification_type):
        return []
    seen = set()
    created = []
    for user in users:
        if user is None or user.pk in seen:
            continue
        seen.add(user.pk)
        notification = Notification.objects.create(
            user=user, title=title, message=message,
            notification_type=notification_type, lab=lab, is_system_alert=True,
        )
        created.append(notification)

        from cloud_sync.models import enqueue
        enqueue('notification', notification.id, {
            'user_id': notification.user_id,
            'title': notification.title,
            'message': notification.message,
            'notification_type': notification.notification_type,
            'lab_id': notification.lab_id,
            'created_at': notification.created_at.isoformat(),
        })
    return created


def _lab_recipients(lab, include_admins=True):
    recipients = list(incharge_for_lab(lab))
    if include_admins:
        recipients += list(admins())
    return recipients


# ---------------------------------------------------------------- #
# Reservations
# ---------------------------------------------------------------- #

def notify_reservation_submitted(session_request):
    req = session_request
    notify(
        _lab_recipients(req.lab),
        'New reservation request',
        f'{req.requester_name or "A requester"} submitted a request for {req.subject} '
        f'in {req.lab.name} on {req.date} ({req.start_time.strftime("%H:%M")}–{req.end_time.strftime("%H:%M")}).',
        'reservation_submitted', lab=req.lab,
    )


def notify_reservation_approved(session_request, code):
    req = session_request
    recipients = []
    if req.instructor:
        recipients.append(req.instructor)
    notify(
        recipients,
        'Lab request approved',
        f'Your request for {req.subject} on {req.date} has been approved. Reservation code: {code}',
        'reservation_approved', lab=req.lab,
    )


def notify_reservation_declined(session_request):
    req = session_request
    recipients = []
    if req.instructor:
        recipients.append(req.instructor)
    notify(
        recipients,
        'Lab request declined',
        f'Your request for {req.subject} on {req.date} was declined.',
        'reservation_declined', lab=req.lab,
    )


def notify_walkin_override_approved(session_request, code):
    req = session_request
    label = 'Walk-in' if req.requester_type == 'walk_in' else 'Override'
    notify(
        _lab_recipients(req.lab),
        f'{label} request approved',
        f'{label} access for {req.requester_name or "a requester"} in {req.lab.name} on {req.date} '
        f'({req.start_time.strftime("%H:%M")}–{req.end_time.strftime("%H:%M")}) was approved. Code: {code}',
        'walkin_override_approved', lab=req.lab,
    )


def notify_roster_session_scheduled(session, code):
    """Sent 1 hour before a roster-auto-generated Session starts (see
    notifications.reminders.check_roster_code_reminders) — this is how the
    instructor learns the reservation code for that specific date, since
    they never filed an individual request for it."""
    recipients = []
    if session.instructor:
        recipients.append(session.instructor)
    notify(
        recipients,
        'Upcoming class session — reservation code',
        f'{session.subject} in {session.lab.name} starts at {session.start_time.strftime("%H:%M")} '
        f'today ({session.date}), from your class roster schedule. Reservation code: {code}',
        'roster_session_scheduled', lab=session.lab,
    )


def notify_roster_submitted(roster):
    """Sent to the relevant Lab In-Charge (if the roster already names a
    lab) plus all Admins whenever a class roster reaches/re-reaches
    'pending' — i.e. a new roster from an Instructor, or a Rejected one
    that's just been edited and resubmitted."""
    notify(
        _lab_recipients(roster.lab),
        'Class roster pending approval',
        f'{roster.instructor.get_full_name() if roster.instructor else "An instructor"} submitted '
        f'"{roster.name}" for approval.',
        'roster_submitted', lab=roster.lab,
    )


def notify_roster_approved(roster):
    recipients = []
    if roster.instructor:
        recipients.append(roster.instructor)
    notify(
        recipients,
        'Class roster approved',
        f'Your roster "{roster.name}" has been approved. Its schedule has been added to the official calendar.',
        'roster_approved', lab=roster.lab,
    )


def notify_roster_rejected(roster):
    recipients = []
    if roster.instructor:
        recipients.append(roster.instructor)
    message = f'Your roster "{roster.name}" was rejected.'
    if roster.rejection_reason:
        message += f' Reason: {roster.rejection_reason}'
    message += ' You can edit and resubmit it.'
    notify(
        recipients,
        'Class roster rejected',
        message,
        'roster_rejected', lab=roster.lab,
    )


def notify_reservation_conflict(lab, description):
    notify(
        _lab_recipients(lab),
        'Reservation conflict detected',
        description,
        'reservation_conflict', lab=lab,
    )


def notify_session_starting_soon(session):
    recipients = list(incharge_for_lab(session.lab))
    if session.instructor:
        recipients.append(session.instructor)
    notify(
        recipients,
        'Lab session starting soon',
        f'{session.subject} in {session.lab.name} starts at {session.start_time.strftime("%H:%M")} today.',
        'session_reminder', lab=session.lab,
    )


# ---------------------------------------------------------------- #
# PCs
# ---------------------------------------------------------------- #

def notify_pc_offline(pc):
    notify(
        _lab_recipients(pc.lab),
        f'{pc.pc_id} is offline',
        f'The computer {pc.pc_id} in {pc.lab.name} has gone offline unexpectedly.',
        'pc_offline', lab=pc.lab,
    )


def notify_pc_online(pc):
    notify(
        _lab_recipients(pc.lab),
        f'{pc.pc_id} is back online',
        f'The computer {pc.pc_id} in {pc.lab.name} is back online.',
        'pc_online', lab=pc.lab,
    )


def notify_pc_maintenance(pc, reason=''):
    detail = f' ({reason})' if reason else ''
    notify(
        _lab_recipients(pc.lab),
        f'{pc.pc_id} needs maintenance',
        f'{pc.pc_id} in {pc.lab.name} was flagged for maintenance{detail}.',
        'pc_maintenance', lab=pc.lab,
    )


# ---------------------------------------------------------------- #
# Maintenance / equipment
# ---------------------------------------------------------------- #

def notify_maintenance_submitted(log):
    notify(
        _lab_recipients(log.equipment.lab),
        'Maintenance request submitted',
        f'A maintenance task for {log.equipment.name} was scheduled for {log.maintenance_date}.',
        'maintenance_submitted', lab=log.equipment.lab,
    )


def notify_maintenance_completed(log):
    notify(
        _lab_recipients(log.equipment.lab),
        'Maintenance completed',
        f'Maintenance for {log.equipment.name} was marked as completed.',
        'maintenance_completed', lab=log.equipment.lab,
    )


def notify_equipment_issue_reported(issue):
    notify(
        _lab_recipients(issue.equipment.lab),
        'Equipment issue reported',
        f'{issue.equipment.name} in {issue.equipment.lab.name} was reported faulty/needs maintenance: {issue.description[:120]}',
        'pc_maintenance', lab=issue.equipment.lab,
    )


def notify_equipment_issue_resolved(issue):
    notify(
        _lab_recipients(issue.equipment.lab),
        'Equipment issue resolved',
        f'{issue.equipment.name} in {issue.equipment.lab.name} has been repaired/resolved.',
        'maintenance_completed', lab=issue.equipment.lab,
    )


# ---------------------------------------------------------------- #
# Security
# ---------------------------------------------------------------- #

def notify_failed_login(username, attempt_count):
    notify(
        admins(),
        'Multiple failed login attempts',
        f'{attempt_count} failed login attempts were detected for account "{username}".',
        'login_security_alert',
    )
