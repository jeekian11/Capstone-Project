from django.contrib.auth.models import AbstractUser, UserManager as DjangoUserManager
from django.conf import settings
from django.db import models
from accounts.constants import DEPARTMENT_CHOICES, department_name, YEAR_LEVEL_LABELS


class UserManager(DjangoUserManager):
    """Django's built-in UserManager.create_user()/create_superuser() always
    pass an `email` kwarg through to the model — which no longer exists here
    now that email is removed — so `manage.py createsuperuser` and any code
    calling those methods directly needs this email-free version instead."""

    def _create_user(self, username, password, **extra_fields):
        extra_fields.pop('email', None)
        if not username:
            raise ValueError('The given username must be set')
        user = self.model(username=username, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        extra_fields.pop('email', None)
        return self._create_user(username, password, **extra_fields)

    def create_superuser(self, username, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.pop('email', None)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self._create_user(username, password, **extra_fields)


class User(AbstractUser):
    # CompuLab doesn't collect/use email (Gmail) addresses anywhere in the
    # system — accounts are matched by username/ID number instead — so the
    # inherited AbstractUser.email field is dropped entirely.
    email = None
    # AbstractUser defaults REQUIRED_FIELDS to ['email'] (used by
    # `manage.py createsuperuser`) — with no email field that would break
    # the command, so it's cleared here.
    REQUIRED_FIELDS = []

    objects = UserManager()

    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('incharge', 'Lab In-charge'),
        ('instructor', 'Instructor'),
        ('student', 'Student'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='instructor')
    id_number = models.CharField(
        max_length=50, blank=True, default='',
        help_text='Student/Instructor ID — used to match this account to reservation requests and lab PC check-ins. Separate from the login username.'
    )
    department = models.CharField(
        max_length=100, blank=True, default='', choices=DEPARTMENT_CHOICES,
        help_text='Instructors and students — which department/college this account belongs to. '
                   'Shown wherever this user\'s details are displayed, and determines the year levels '
                   'available below for students.'
    )
    year_level = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Students only — year level within the selected Department. '
                   'The available options depend on which Department is picked, since not every '
                   'department/program has the same number of year levels.'
    )
    assigned_lab = models.ForeignKey(
        'labs.Lab',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='assigned_users'
    )
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    THEME_CHOICES = [
        ('dark', 'Dark'),
        ('light', 'Light'),
    ]
    theme = models.CharField(
        max_length=5, choices=THEME_CHOICES, default='dark',
        help_text='Dark/Light interface preference — saved per account so it follows the user to any PC they log in on.'
    )

    def __str__(self):
        return f'{self.get_full_name()} ({self.role})'

    @property
    def department_display(self):
        """Human-readable department name — falls back to the raw stored
        value for any legacy/free-text department that predates the
        Department dropdown, so old records don't just show blank."""
        return department_name(self.department) or self.department

    @property
    def year_level_display(self):
        if not self.year_level:
            return ''
        return YEAR_LEVEL_LABELS.get(self.year_level, f'Year {self.year_level}')

    @property
    def program_year_display(self):
        """Combined 'Department — Year Level' summary (e.g. 'BSIS — 1st
        Year') used anywhere the old free-text Course & Year/Section field
        used to be shown, now derived from the two structured fields
        instead so it always stays in sync with them."""
        parts = [p for p in [self.department_display, self.year_level_display] if p]
        return ' — '.join(parts)


class ActivityLog(models.Model):
    ACTION_CHOICES = [
        ('register', 'User Registered'),
        ('activate', 'Account Activated'),
        ('deactivate', 'Account Deactivated'),
        ('delete', 'User Deleted'),
        ('reset_password', 'Password Reset'),
        ('pc_unlock', 'Lab PC Unlocked'),
    ]
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='actions_performed'
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    target_username = models.CharField(max_length=150)
    details = models.TextField(blank=True)
    pc = models.ForeignKey(
        'labs.PC',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='usage_logs',
        help_text='The lab PC this event happened on, set for pc_unlock events. Lets us answer "which PC was used on date X" reliably.',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.get_action_display()} — {self.target_username}'

    @property
    def actor_display(self):
        """Safe display name for `actor` — templates should use this instead
        of chaining `log.actor.get_full_name|default:log.actor.username`,
        since actor is null=True/SET_NULL (the account may have since been
        deleted) and that chained lookup blows up with
        VariableDoesNotExist when actor is None."""
        if not self.actor:
            return '—'
        return self.actor.get_full_name() or self.actor.username