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
    schedule = models.CharField(
        max_length=150, blank=True,
        help_text='Auto-generated from the selected day(s) and time range.'
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')
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
        else:
            self.schedule = ''
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


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
            self.id_number = self.id_number or self.student.id_number or self.student.username
            self.full_name = self.full_name or self.student.get_full_name() or self.student.username
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.full_name} ({self.id_number})'


class Session(models.Model):
    REQUESTER_TYPE = [
        ('instructor', 'Instructor'),
        ('student', 'Student'),
        ('group', 'Group of students'),
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
