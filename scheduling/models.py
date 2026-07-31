from django.db import models
from django.conf import settings

class ClassRoster(models.Model):
    """A class-level record — course/section/schedule info for one class.
    Students are NOT entered here; they're added afterwards (on the roster's
    detail page) by searching the already-registered student accounts, so
    only officially registered students can end up on a roster and there's
    no risk of duplicate/typo'd student records."""

    SEMESTER_CHOICES = [
        ('1st', '1st Semester'),
        ('2nd', '2nd Semester'),
        ('summer', 'Summer'),
    ]
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
    ]
    # Approval workflow — separate from `status` (active/inactive archiving)
    # above. A roster's schedule only ever rolls out into official,
    # reservation-coded Sessions (see generate_sessions()) once it reaches
    # 'approved'; 'pending' and 'rejected' rosters never occupy the
    # official schedule.
    APPROVAL_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    name = models.CharField(
        max_length=200, blank=True,
        help_text='Auto-generated from Course Code + Subject if left blank.'
    )
    course_code = models.CharField(max_length=50, blank=True, help_text='e.g. "BSIS 3A"')
    subject = models.CharField(max_length=200, blank=True, help_text='e.g. "Systems Analysis and Design"')
    section = models.CharField(max_length=50, blank=True, help_text='e.g. "BSIS-3A"')
    lab = models.ForeignKey(
        'labs.Lab', on_delete=models.SET_NULL, null=True, blank=True, related_name='class_rosters',
        help_text='Laboratory room.'
    )
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='class_rosters'
    )
    semester = models.CharField(max_length=10, choices=SEMESTER_CHOICES, blank=True)
    academic_year = models.CharField(max_length=20, blank=True, help_text='e.g. "2026-2027"')
    DAY_CHOICES = [
        ('mon', 'Monday'), ('tue', 'Tuesday'), ('wed', 'Wednesday'),
        ('thu', 'Thursday'), ('fri', 'Friday'), ('sat', 'Saturday'), ('sun', 'Sunday'),
    ]
    DAY_LABELS = dict(DAY_CHOICES)

    schedule_days = models.CharField(
        max_length=50, blank=True,
        help_text='Comma-separated day keys (mon,tue,...) — set via the day checkboxes in the form.'
    )
    schedule_start_time = models.TimeField(null=True, blank=True)
    schedule_end_time = models.TimeField(null=True, blank=True)
    schedule_valid_from = models.DateField(
        null=True, blank=True,
        help_text='First date this weekly schedule applies from (e.g. the start of the semester).'
    )
    schedule_valid_until = models.DateField(
        null=True, blank=True,
        help_text='Last date this weekly schedule applies to (e.g. the end of the semester).'
    )
    schedule = models.CharField(
        max_length=200, blank=True,
        help_text='Auto-generated from the selected day(s), time range, and validity period.'
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
    approval_status = models.CharField(
        max_length=10, choices=APPROVAL_CHOICES, default='pending',
        help_text='Pending until an Admin/Lab In-Charge reviews it. Only an Approved roster\'s '
                   'schedule can be rolled out into the official (Session) calendar.'
    )
    rejection_reason = models.TextField(
        blank=True, default='',
        help_text='Shown to the instructor so they know what to fix before resubmitting.'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['status', 'name']

    def save(self, *args, **kwargs):
        if not self.name:
            label = self.course_code or self.section or 'Class'
            self.name = f'{label} — {self.subject}' if self.subject else label
        if self.schedule_days and self.schedule_start_time and self.schedule_end_time:
            day_labels = [self.DAY_LABELS.get(d, d) for d in self.schedule_days.split(',') if d]
            start = self.schedule_start_time.strftime('%I:%M %p').lstrip('0')
            end = self.schedule_end_time.strftime('%I:%M %p').lstrip('0')
            self.schedule = f'{", ".join(day_labels)} · {start}–{end}'
            if self.schedule_valid_from and self.schedule_valid_until:
                frm = self.schedule_valid_from.strftime('%b %d, %Y')
                until = self.schedule_valid_until.strftime('%b %d, %Y')
                self.schedule += f' · {frm} – {until}'
        else:
            self.schedule = ''
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

    # Monday=0 ... Sunday=6, matching date.weekday()
    WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

    def has_full_schedule(self):
        """True once every field generate_sessions() needs is filled in:
        a lab, the meeting day(s)/time, and a validity period."""
        return bool(
            self.lab_id and self.schedule_days and self.schedule_start_time
            and self.schedule_end_time and self.schedule_valid_from and self.schedule_valid_until
        )

    def prune_stale_future_sessions(self):
        """Deletes this roster's auto-generated sessions that no longer
        match its CURRENT schedule (day/time/validity period) — e.g. after
        editing the roster from Mon/Wed to Tue/Thu, shortening the validity
        period, or changing the meeting time. Without this, editing a
        roster's schedule only ever adds new sessions on top of the old
        ones (generate_sessions() is additive/idempotent by design), so the
        stale schedule silently lingers on the official calendar.

        Only ever considers TODAY-OR-LATER sessions, and only ones with no
        recorded check-in — past dates and anything a student has actually
        used are never touched, so attendance history is never altered or
        lost. Returns the number of sessions deleted.
        """
        from django.utils import timezone
        from scheduling.models import Session

        today = timezone.localdate()
        candidates = Session.objects.filter(roster=self, date__gte=today).prefetch_related('check_ins')

        if self.has_full_schedule():
            target_weekdays = {
                self.WEEKDAY_KEYS.index(d) for d in self.schedule_days.split(',')
                if d in self.WEEKDAY_KEYS
            }

            def still_matches(s):
                return (
                    s.start_time == self.schedule_start_time and s.end_time == self.schedule_end_time
                    and self.schedule_valid_from <= s.date <= self.schedule_valid_until
                    and s.date.weekday() in target_weekdays
                )
        else:
            # Schedule was cleared entirely — nothing should remain scheduled.
            def still_matches(s):
                return False

        stale_pks = [s.pk for s in candidates if not s.check_ins.exists() and not still_matches(s)]
        deleted = len(stale_pks)
        if stale_pks:
            Session.objects.filter(pk__in=stale_pks).delete()
        return deleted

    def generate_sessions(self):
        """Rolls this roster's weekly schedule out into real, individually
        reservation-coded Session rows on the official schedule — one for
        every date between schedule_valid_from and schedule_valid_until
        (inclusive) that falls on one of schedule_days. Students/instructor
        no longer need to file a request for each meeting; each generated
        session already carries its own reservation code. The instructor is
        notified of that code 1 hour before each session starts (see
        notifications.reminders.check_roster_code_reminders), not the
        instant it's generated here — this call itself does not notify.

        Safe to call more than once (e.g. after editing the roster's
        schedule): a date/time that already has a Session generated from
        this roster is skipped rather than duplicated, and a date/time that
        conflicts with an unrelated, already-scheduled session is skipped
        and reported rather than double-booking the lab.

        Returns (created_sessions, skipped) where skipped is a list of
        (date, reason) tuples for anything that couldn't be auto-scheduled.
        """
        from datetime import timedelta
        from scheduling.models import Session
        from scheduling.utils import generate_reservation_code
        from scheduling.views import _slot_error

        created_sessions = []
        skipped = []
        if not self.has_full_schedule() or self.approval_status != 'approved':
            return created_sessions, skipped

        target_weekdays = {
            self.WEEKDAY_KEYS.index(d) for d in self.schedule_days.split(',')
            if d in self.WEEKDAY_KEYS
        }
        requester_name = self.instructor.get_full_name() if self.instructor else ''
        requester_id_number = (
            (getattr(self.instructor, 'id_number', '') or self.instructor.display_name)
            if self.instructor else ''
        )
        student_count = self.students.count()
        subject = self.subject or self.name

        current = self.schedule_valid_from
        while current <= self.schedule_valid_until:
            if current.weekday() in target_weekdays:
                already_generated = Session.objects.filter(
                    roster=self, date=current,
                    start_time=self.schedule_start_time, end_time=self.schedule_end_time,
                ).exists()
                if not already_generated:
                    error = _slot_error(
                        'instructor', self.lab, current,
                        self.schedule_start_time, self.schedule_end_time,
                        pcs_requested=0, student_count=student_count,
                    )
                    if error:
                        skipped.append((current, error))
                    else:
                        code = generate_reservation_code()
                        session = Session.objects.create(
                            lab=self.lab, instructor=self.instructor, roster=self,
                            requester_type='instructor', requester_name=requester_name,
                            requester_id_number=requester_id_number, reservation_code=code,
                            subject=subject, date=current,
                            start_time=self.schedule_start_time, end_time=self.schedule_end_time,
                            student_count=student_count,
                        )
                        created_sessions.append(session)
            current += timedelta(days=1)

        return created_sessions, skipped

    def delete(self, *args, **kwargs):
        """Deleting a roster also deletes the still-pending schedule it
        generated — today-or-later sessions with no recorded check-in —
        rather than leaving them behind as orphaned, un-attributed
        bookings on the official calendar. Uses the same "no check-in yet"
        test as prune_stale_future_sessions().

        Past sessions, and any session (past or future) that already has
        a check-in, are left alone: Session.roster is SET_NULL, so they
        keep their attendance history and just lose the name-lookup link
        — matching what the delete-confirmation page tells the user.
        """
        from django.utils import timezone
        from scheduling.models import Session

        today = timezone.localdate()
        candidates = Session.objects.filter(roster=self, date__gte=today).prefetch_related('check_ins')
        stale_pks = [s.pk for s in candidates if not s.check_ins.exists()]
        if stale_pks:
            Session.objects.filter(pk__in=stale_pks).delete()

        super().delete(*args, **kwargs)


class RosterStudent(models.Model):
    """One enrolled student on a roster. `student` links to the actual
    registered account (role='student') picked via the search-and-add
    workflow — id_number/full_name are a denormalized snapshot copied from
    that account (kept even if the account is later edited/removed, so the
    roster's historical record doesn't silently change or break)."""
    roster = models.ForeignKey(ClassRoster, on_delete=models.CASCADE, related_name='students')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='roster_memberships',
        help_text='The registered student account this entry was added from.'
    )
    id_number = models.CharField(max_length=50)
    full_name = models.CharField(max_length=150)

    class Meta:
        ordering = ['full_name']
        unique_together = [('roster', 'id_number')]

    def save(self, *args, **kwargs):
        if self.student_id and (not self.id_number or not self.full_name):
            self.id_number = self.id_number or self.student.id_number or self.student.display_name
            self.full_name = self.full_name or self.student.get_full_name() or self.student.display_name
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.full_name} ({self.id_number})'


class Session(models.Model):
    REQUESTER_TYPE = [
        ('instructor', 'Instructor'),
        ('student', 'Student'),
        ('group', 'Group of students'),
        ('walk_in', 'Walk-in'),
        ('override', 'Override'),
    ]
    lab = models.ForeignKey('labs.Lab', on_delete=models.CASCADE, related_name='sessions')
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='sessions',
        help_text='Optional — only set when the requester has a matching CompuLab account.'
    )
    roster = models.ForeignKey(
        ClassRoster, on_delete=models.SET_NULL, null=True, blank=True, related_name='sessions',
        help_text='Optional — attach a class roster so attendance reports can show real present/absent names.'
    )
    requester_type = models.CharField(max_length=10, choices=REQUESTER_TYPE, default='instructor')
    requester_name = models.CharField(max_length=150, default='')
    requester_id_number = models.CharField(
        max_length=50, default='', help_text='Student ID or Instructor ID presented at the lab PC.'
    )
    reservation_code = models.CharField(max_length=12, unique=True, null=True, blank=True)
    subject = models.CharField(max_length=200)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    student_count = models.PositiveIntegerField(default=0)
    pcs_requested = models.PositiveIntegerField(
        default=0,
        help_text=(
            'Required for Walk-in and Override requests — how many PCs are needed. Ignored for '
            'Instructor/Student/Group requests, which still reserve the whole lab.'
        ),
    )
    reminder_sent = models.BooleanField(
        default=False,
        help_text='Set once the "session starting soon" auto-notification has been sent for this session, so it is not sent twice.'
    )
    roster_code_reminder_sent = models.BooleanField(
        default=False,
        help_text=(
            'Set once the "here\'s your reservation code" reminder (sent 1 hour before start, for '
            'sessions auto-generated from a Class Roster schedule) has gone out, so it is not sent twice.'
        )
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.subject} — {self.date}'


class SessionCheckIn(models.Model):
    """One row per distinct ID number that has checked in to a Session.
    Originally only used to cap 'Group of students' bookings (which accept
    any ID) at the declared student_count — now records EVERY successful
    check-in (requester, roster member, group member, walk-in, or
    Admin/In-Charge override), so there's a single canonical transaction
    log of who actually used a PC during a reservation, separate from
    who the reservation was filed under. Re-checking in with the same ID
    (e.g. logging into a different PC) does not count again — it's the
    same person.
    """
    CHECKIN_TYPE = [
        ('requester', 'Primary Requester'),
        ('roster', 'Roster Member'),
        ('group', 'Group Member'),
        ('walk_in', 'Walk-in'),
        ('override', 'Override'),
        ('guest', 'Guest (Manual Unlock)'),
    ]
    # Nullable: an Admin/In-Charge Override can grant PC access even when
    # no reservation is currently occupying that lab/time slot at all.
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='check_ins', null=True, blank=True)
    id_number = models.CharField(max_length=50)
    checkin_type = models.CharField(max_length=10, choices=CHECKIN_TYPE, default='requester')
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='session_checkins',
        help_text='The registered account resolved from id_number, if any.',
    )
    guest_name = models.CharField(
        max_length=150, blank=True, default='',
        help_text="Full name of a walk-in guest with no registered account, entered by the Lab In-Charge before a Manual Unlock. Only used when checkin_type='guest' (student stays null for these).",
    )
    pc = models.ForeignKey('labs.PC', on_delete=models.SET_NULL, null=True, blank=True, related_name='checkins')
    checked_in_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('session', 'id_number')]
        ordering = ['checked_in_at']

    def __str__(self):
        return f'{self.id_number} → {self.session}'


class SessionRequest(models.Model):
    REQUESTER_TYPE = [
        ('instructor', 'Instructor'),
        ('student', 'Student'),
        ('group', 'Group of students'),
        ('walk_in', 'Walk-in'),
        ('override', 'Override'),
    ]
    STATUS = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('declined', 'Declined'),
    ]
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='requests',
        help_text='Optional — only set when the requester has a matching CompuLab account.'
    )
    requester_type = models.CharField(max_length=10, choices=REQUESTER_TYPE, default='instructor')
    requester_name = models.CharField(max_length=150, default='', help_text='Name of the instructor or student who asked for this reservation in person.')
    requester_id_number = models.CharField(
        max_length=50, default='', help_text='Student ID or Instructor ID — this is what they will present at the lab PC.'
    )
    roster = models.ForeignKey(
        ClassRoster, on_delete=models.SET_NULL, null=True, blank=True, related_name='session_requests',
        help_text=(
            'Optional — link a roster to let everyone on it check in with this same reservation code, '
            'not just the requester. Use this for an Instructor booking their whole class, so attendance '
            'shows real names. Not needed for "Group of students" — any ID works with the shared code for '
            'that type. Leave blank for a single individual (Student) request.'
        )
    )
    lab = models.ForeignKey('labs.Lab', on_delete=models.CASCADE)
    subject = models.CharField(max_length=200)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    student_count = models.PositiveIntegerField(default=0)
    pcs_requested = models.PositiveIntegerField(
        default=0,
        help_text=(
            'Required for Walk-in and Override requests — how many PCs are needed. Ignored for '
            'Instructor/Student/Group requests, which still reserve the whole lab.'
        ),
    )
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='pending')
    reservation_code = models.CharField(max_length=12, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.subject} — {self.status}'

class ManualAttendanceRecord(models.Model):
    """A manual present/absent mark for one student in one session, entered
    by the instructor. Used to correct/supplement the PC-unlock-derived
    attendance for classes that don't require students to log into a lab PC
    (e.g. lecture-only or bring-your-own-device sessions). A manual record
    always takes precedence over the PC-unlock estimate for that student."""
    STATUS = [
        ('present', 'Present'),
        ('absent', 'Absent'),
    ]
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='manual_attendance')
    id_number = models.CharField(max_length=50)
    full_name = models.CharField(max_length=150, blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default='present')
    marked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    marked_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('session', 'id_number')]
        ordering = ['id_number']

    def __str__(self):
        return f'{self.id_number} - {self.get_status_display()} ({self.session})'
