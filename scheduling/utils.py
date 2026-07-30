import random
import string

from django.db.models import Q

CODE_CHARS = string.ascii_uppercase.replace('O', '').replace('I', '') + string.digits.replace('0', '').replace('1', '')

# Requester types that reserve a specific NUMBER of PCs against the lab's
# remaining capacity, instead of exclusively blocking the whole lab for
# their time slot the way Instructor/Student/Group requests do.
CAPACITY_BASED_TYPES = ('walk_in', 'override')


def generate_reservation_code(length=6):
    """
    Generates a short, human-typeable reservation code (avoids ambiguous
    characters like O/0 and I/1) that's unique across both pending requests
    and the official schedule.
    """
    from scheduling.models import Session, SessionRequest

    while True:
        code = ''.join(random.choices(CODE_CHARS, k=length))
        if not SessionRequest.objects.filter(reservation_code=code).exists() and \
           not Session.objects.filter(reservation_code=code).exists():
            return code


def lab_pc_capacity(lab, date, start_time, end_time, exclude_pk=None):
    """Returns (total_pcs, pcs_used, pcs_available) for this lab/date/time
    slot, based on already-APPROVED sessions on the official schedule
    (scheduling.models.Session).

    Instructor/Student/Group sessions still reserve the ENTIRE lab
    exclusively — unchanged legacy behavior, see _official_schedule_conflict
    in views.py — so any one of them overlapping this slot counts as the
    whole lab being used. Walk-in/Override sessions instead only reserve
    the specific number of PCs (`pcs_requested`) they were approved for, so
    several of them can be evaluated against how many PCs are actually free.
    """
    from scheduling.models import Session

    total_pcs = lab.pcs.count()
    qs = Session.objects.filter(
        lab=lab, date=date, start_time__lt=end_time, end_time__gt=start_time,
    )
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    used = 0
    for s in qs:
        if s.requester_type in CAPACITY_BASED_TYPES:
            used += s.pcs_requested
        else:
            used = total_pcs  # exclusive booking fills the whole lab
            break
    used = min(used, total_pcs)
    return total_pcs, used, max(total_pcs - used, 0)


def student_count_error(lab, student_count):
    """Validates an Instructor/Student/Group (whole-lab, exclusive-booking)
    request's `student_count` against the lab's total physical PC count.

    These requester types block the entire lab for their time slot rather
    than reserving a specific number of PCs (see lab_pc_capacity above), but
    the number of students who'll actually need a PC still can't exceed how
    many PCs the lab physically has — otherwise some students would have
    nowhere to sit. Returns an error message string if the request can't be
    granted, or None if it's fine.
    """
    total_pcs = lab.pcs.count()
    if student_count > total_pcs:
        return (
            f'{lab.name} only has {total_pcs} PC(s) — this reservation is for '
            f'{student_count} student(s), which exceeds the lab\'s capacity.'
        )
    return None


def roster_schedule_conflicts(lab, instructor, section, schedule_days, start_time, end_time,
                               valid_from, valid_until, exclude_pk=None):
    """Checks a Class Roster's proposed weekly schedule (day(s) + time
    range, repeating over [valid_from, valid_until]) for conflicts against
    every OTHER active, still-relevant roster (approval_status is 'pending'
    or 'approved' — a Rejected or Archived roster's old schedule no longer
    holds a slot) on three fronts, per the roster approval rules:

      - the INSTRUCTOR's schedule (same instructor, another class at an
        overlapping day/time)
      - the CLASSROOM's schedule (same lab, another class at an
        overlapping day/time)
      - the SECTION's schedule (same section — a section can't be in two
        classes at once)

    Returns a list of dicts, one per conflicting roster/front:
      {'kind': 'Instructor' | 'Classroom' | 'Section', 'roster': <ClassRoster>,
       'shared_days': ['mon', ...]}
    Empty list means the slot is free on all three fronts.
    """
    from scheduling.models import ClassRoster

    days = set(d for d in (schedule_days or '').split(',') if d)
    if not (days and start_time and end_time and valid_from and valid_until):
        return []

    candidates = ClassRoster.objects.filter(
        status='active', approval_status__in=('pending', 'approved'),
    ).exclude(schedule_days='')
    if exclude_pk:
        candidates = candidates.exclude(pk=exclude_pk)

    lookups = [('Classroom', Q(lab_id=lab.pk) if lab else None)]
    if instructor is not None:
        lookups.append(('Instructor', Q(instructor_id=instructor.pk)))
    if section:
        lookups.append(('Section', Q(section=section)))

    conflicts = []
    for kind, filt in lookups:
        if filt is None:
            continue
        for other in candidates.filter(filt):
            other_days = set(d for d in other.schedule_days.split(',') if d)
            shared_days = days & other_days
            if not shared_days:
                continue
            if not (other.schedule_start_time and other.schedule_end_time and
                    other.schedule_valid_from and other.schedule_valid_until):
                continue
            time_overlap = start_time < other.schedule_end_time and end_time > other.schedule_start_time
            date_overlap = valid_from <= other.schedule_valid_until and valid_until >= other.schedule_valid_from
            if time_overlap and date_overlap:
                conflicts.append({'kind': kind, 'roster': other, 'shared_days': sorted(shared_days)})
    return conflicts


def capacity_error(requester_type, lab, date, start_time, end_time, pcs_requested, exclude_pk=None):
    """Validates a Walk-in or Override request's `pcs_requested` against the
    lab's capacity for that date/time. Returns an error message string if
    the request can't be granted, or None if it's fine.

    - Walk-in: only allowed if enough PCs are actually free in that slot
      (not already reserved/in-use by other approved sessions).
    - Override: bypasses the availability check entirely (can be approved
      even if the lab is fully booked/in-use) but still can't ask for more
      PCs than the laboratory physically has.
    """
    total_pcs, used, available = lab_pc_capacity(lab, date, start_time, end_time, exclude_pk=exclude_pk)

    if requester_type == 'walk_in':
        if pcs_requested > available:
            return (
                f'Not enough available computers for this time slot in {lab.name} — '
                f'{available} of {total_pcs} PC(s) available, but {pcs_requested} requested.'
            )
    elif requester_type == 'override':
        if pcs_requested > total_pcs:
            return (
                f'{lab.name} only has {total_pcs} PC(s) in total — an Override reservation '
                f'cannot request more than that ({pcs_requested} requested).'
            )
    return None
