from django.db import models
from django.conf import settings

class ClassRoster(models.Model):
    """A named list of enrolled students (ID + name) for a class/subject,
    used to compute real present/absent attendance instead of estimates."""
    name = models.CharField(max_length=200, help_text='e.g. "BSCS 2A — CS101 Programming Logic"')
    subject = models.CharField(max_length=200, blank=True)
    lab = models.ForeignKey('labs.Lab', on_delete=models.SET_NULL, null=True, blank=True, related_name='class_rosters')
    instructor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='class_rosters'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class RosterStudent(models.Model):
    roster = models.ForeignKey(ClassRoster, on_delete=models.CASCADE, related_name='students')
    id_number = models.CharField(max_length=50)
    full_name = models.CharField(max_length=150)

    class Meta:
        ordering = ['full_name']
        unique_together = [('roster', 'id_number')]

    def __str__(self):
        return f'{self.full_name} ({self.id_number})'


class Session(models.Model):
    REQUESTER_TYPE = [
        ('instructor', 'Instructor'),
        ('student', 'Student'),
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


class SessionRequest(models.Model):
    REQUESTER_TYPE = [
        ('instructor', 'Instructor'),
        ('student', 'Student'),
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
