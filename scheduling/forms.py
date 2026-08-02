import datetime
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone
from scheduling.models import ClassRoster, RosterStudent, Session, SessionRequest
from accounts.constants import DEPARTMENT_CHOICES

User = get_user_model()


# shared date/time widgets so the browser shows a native, selectable
# calendar/clock picker instead of a plain text box
_DATE_TIME_WIDGETS = {
    'date': forms.DateInput(attrs={'type': 'date'}),
    'start_time': forms.TimeInput(attrs={'type': 'time'}),
    'end_time': forms.TimeInput(attrs={'type': 'time'}),
}


class RosterSelect(forms.Select):
    """A <select> for the `roster` field that stamps each <option> with
    data-student-count="N" (how many students are enrolled on that
    roster). Lets the request/session form's JS auto-fill "Number of
    students" the instant a Class Roster is picked, with no extra
    request needed. `roster_student_counts` (roster pk -> count) is
    populated by RosterStudentCountMixin.__init__ below."""
    roster_student_counts = {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value not in (None, ''):
            try:
                pk = int(str(value))
            except (TypeError, ValueError):
                pk = None
            if pk is not None:
                option['attrs']['data-student-count'] = self.roster_student_counts.get(pk, 0)
        return option


class RosterStudentCountMixin:
    """For requests/sessions that carry an (optional) Class Roster:

    - Wires the `roster` widget up with each roster's live enrolled-student
      count, for the "auto-fill Number of students" JS on the form.
    - Server-side, makes the selected roster the single source of truth for
      `student_count` whenever one is attached — the submitted number is
      overwritten with the roster's actual headcount, so it can't drift out
      of sync with the roster (or be tampered with client-side) and the
      capacity check in scheduling.views always checks the real headcount.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        roster_field = self.fields.get('roster')
        if roster_field is not None:
            roster_field.widget.roster_student_counts = {
                r.pk: r.students.count() for r in roster_field.queryset
            }
            self.fields['student_count'].required = False

    def clean(self):
        cleaned_data = super().clean()
        roster = cleaned_data.get('roster')
        if roster is not None:
            cleaned_data['student_count'] = roster.students.count()
        return cleaned_data


class NoPastDateTimeMixin:
    """Rejects a request whose date + start time has already passed —
    logging (or editing) a reservation for a slot that's already over
    isn't useful to anyone; it would just sit there for an Admin/Lab
    In-Charge to approve into a slot that can no longer actually be
    used. Only checked when both fields parsed cleanly (a missing/
    invalid date or time is already reported by the field itself)."""

    def clean(self):
        cleaned_data = super().clean()
        date = cleaned_data.get('date')
        start_time = cleaned_data.get('start_time')
        if date and start_time:
            naive = datetime.datetime.combine(date, start_time)
            aware = timezone.make_aware(naive, timezone.get_current_timezone())
            if aware < timezone.localtime():
                self.add_error('date', "This date and time have already passed — pick one that hasn't started yet.")
        return cleaned_data


class RequiresRegisteredAccountMixin:
    """Enforces the policy that only accounts registered by an Admin/Lab
    In-Charge (under Users) may hold a reservation. The requester's ID
    number + requester type must match an existing, active account —
    otherwise the form is rejected before it ever saves a request/session.
    """

    def clean(self):
        cleaned_data = super().clean()
        id_number = (cleaned_data.get('requester_id_number') or '').strip()
        requester_type = cleaned_data.get('requester_type')
        if id_number and requester_type:
            # "Group of students" isn't a separate account role — the person
            # who files the request still logs in with their own student
            # account, so match against role='student' for that case.
            # "Walk-in" and "Override" aren't tied to one role either — the
            # requester could be a registered student or instructor — so
            # match against either, same as the 'any' option already used
            # by the live Override check-in search.
            qs = User.objects.filter(id_number__iexact=id_number)
            if requester_type == 'group':
                qs = qs.filter(role='student')
            elif requester_type in ('walk_in', 'override'):
                qs = qs.filter(role__in=('student', 'instructor'))
            else:
                qs = qs.filter(role=requester_type)
            account = qs.first()
            if account is None:
                self.add_error(
                    'requester_id_number',
                    'No registered account matches this ID number and requester type. '
                    'Only accounts registered by an Admin (under Users) may request a reservation — '
                    'ask an Admin to register this account first.'
                )
            elif not account.is_active:
                self.add_error(
                    'requester_id_number',
                    f'The account for "{id_number}" has been deactivated. Ask an Admin to reactivate it before requesting a reservation.'
                )
            else:
                cleaned_data['_matched_account'] = account
        return cleaned_data


class PcsRequestedRequiredMixin:
    """Walk-in and Override requests need to say how many PCs they want
    (validated for real against lab capacity in the view — see
    scheduling.utils.capacity_error); Instructor/Student/Group requests
    still reserve the whole lab, so `pcs_requested` doesn't apply to them
    and is reset to 0 to avoid a stale value lingering from a previous
    edit."""

    def clean(self):
        cleaned_data = super().clean()
        requester_type = cleaned_data.get('requester_type')
        pcs_requested = cleaned_data.get('pcs_requested') or 0
        if requester_type in ('walk_in', 'override'):
            if pcs_requested < 1:
                self.add_error('pcs_requested', 'Enter how many PCs are needed (at least 1).')
        else:
            cleaned_data['pcs_requested'] = 0
        return cleaned_data


class SessionForm(RequiresRegisteredAccountMixin, PcsRequestedRequiredMixin, RosterStudentCountMixin, forms.ModelForm):
    """Used by SessionUpdateView to edit an already-scheduled session."""
    class Meta:
        model = Session
        fields = ['requester_type', 'requester_name', 'requester_id_number',
                  'lab', 'subject', 'date', 'start_time', 'end_time', 'student_count',
                  'pcs_requested', 'roster']
        widgets = {**_DATE_TIME_WIDGETS, 'roster': RosterSelect}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pcs_requested'].required = False


class SessionRequestForm(NoPastDateTimeMixin, RequiresRegisteredAccountMixin, PcsRequestedRequiredMixin, RosterStudentCountMixin, forms.ModelForm):
    """Used by RequestCreateView/RequestUpdateView to log or edit a pending request."""
    class Meta:
        model = SessionRequest
        fields = ['requester_type', 'requester_name', 'requester_id_number',
                  'lab', 'subject', 'date', 'start_time', 'end_time', 'student_count',
                  'pcs_requested', 'notes']
        widgets = {**_DATE_TIME_WIDGETS}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pcs_requested'].required = False
        self.fields['date'].widget.attrs['min'] = timezone.localdate().isoformat()


class InstructorSessionRequestForm(NoPastDateTimeMixin, forms.ModelForm):
    """Self-service version of SessionRequestForm for an Instructor booking
    their own class — skips RequiresRegisteredAccountMixin's requester-
    name/ID matching entirely, since the logged-in account IS the
    requester (InstructorRequestCreateView sets requester_type/name/ID and
    .instructor straight from request.user)."""
    class Meta:
        model = SessionRequest
        fields = ['lab', 'subject', 'date', 'start_time', 'end_time', 'student_count', 'notes']
        widgets = {**_DATE_TIME_WIDGETS}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['date'].widget.attrs['min'] = timezone.localdate().isoformat()


class ClassRosterForm(forms.ModelForm):
    """Create/edit a class roster — class-level info only. Students are
    added afterwards, on the roster's detail page, by searching already-
    registered student accounts."""

    schedule_days = forms.MultipleChoiceField(
        choices=ClassRoster.DAY_CHOICES, widget=forms.CheckboxSelectMultiple, required=False,
        label='Meeting day(s)',
    )

    class Meta:
        model = ClassRoster
        fields = [
            'course_code', 'subject', 'section', 'lab', 'instructor',
            'semester', 'academic_year', 'schedule_days', 'schedule_start_time', 'schedule_end_time',
            'schedule_valid_from', 'schedule_valid_until', 'status',
        ]
        widgets = {
            'academic_year': forms.TextInput(attrs={'placeholder': 'e.g. 2026-2027'}),
            'schedule_start_time': forms.TimeInput(attrs={'type': 'time'}),
            'schedule_end_time': forms.TimeInput(attrs={'type': 'time'}),
            'schedule_valid_from': forms.DateInput(attrs={'type': 'date'}),
            'schedule_valid_until': forms.DateInput(attrs={'type': 'date'}),
        }
        labels = {
            'schedule_valid_from': 'Schedule valid from',
            'schedule_valid_until': 'Schedule valid until',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['instructor'].queryset = User.objects.filter(role='instructor').order_by('first_name', 'last_name')
        self.fields['instructor'].required = False
        self.fields['lab'].required = False
        self.fields['schedule_start_time'].required = False
        self.fields['schedule_end_time'].required = False
        self.fields['schedule_valid_from'].required = False
        self.fields['schedule_valid_until'].required = False
        self.fields['schedule_valid_from'].help_text = (
            'Set a full schedule (day(s), time, lab, and this validity period, e.g. January to May) '
            'to have the system automatically create a reservation-coded session for every matching '
            'date — no individual reservations needed.'
        )
        if self.instance and self.instance.pk and self.instance.schedule_days:
            self.initial['schedule_days'] = self.instance.schedule_days.split(',')

    def clean_schedule_days(self):
        return ','.join(self.cleaned_data.get('schedule_days') or [])

    def clean(self):
        cleaned = super().clean()
        days = cleaned.get('schedule_days')
        start = cleaned.get('schedule_start_time')
        end = cleaned.get('schedule_end_time')
        valid_from = cleaned.get('schedule_valid_from')
        valid_until = cleaned.get('schedule_valid_until')
        if days and (not start or not end):
            raise forms.ValidationError('Set both a start and end time for the selected meeting day(s).')
        if start and end and start >= end:
            self.add_error('schedule_end_time', 'End time must be after start time.')
        if days and start and end and (not valid_from or not valid_until):
            raise forms.ValidationError(
                'Set the schedule\'s validity period (from/until) so sessions can be auto-generated for it.'
            )
        if valid_from and valid_until and valid_from > valid_until:
            self.add_error('schedule_valid_until', '"Valid until" must be on or after "valid from".')
        if days and (not cleaned.get('lab')):
            raise forms.ValidationError('Select a laboratory room to auto-generate sessions for this schedule.')

        # Schedule-conflict check — instructor's schedule, the classroom's
        # schedule, and the section's schedule must all be free for this
        # day(s)/time/validity-period combination before the roster can be
        # submitted at all.
        if days and start and end and valid_from and valid_until:
            from scheduling.utils import roster_schedule_conflicts
            conflicts = roster_schedule_conflicts(
                lab=cleaned.get('lab'), instructor=cleaned.get('instructor'),
                section=cleaned.get('section'), schedule_days=','.join(days),
                start_time=start, end_time=end, valid_from=valid_from, valid_until=valid_until,
                exclude_pk=self.instance.pk if self.instance and self.instance.pk else None,
            )
            if conflicts:
                lines = []
                for c in conflicts:
                    other = c['roster']
                    day_labels = ', '.join(ClassRoster.DAY_LABELS.get(d, d) for d in c['shared_days'])
                    start_lbl = other.schedule_start_time.strftime('%I:%M %p').lstrip('0')
                    end_lbl = other.schedule_end_time.strftime('%I:%M %p').lstrip('0')
                    lines.append(
                        f'{c["kind"]} conflict: "{other.name}" already occupies {day_labels} '
                        f'{start_lbl}–{end_lbl}. Choose another available time before submitting.'
                    )
                raise forms.ValidationError(lines)
        return cleaned


class RosterAddStudentForm(forms.Form):
    """Adds one already-registered student account (role='student') to a
    roster, picked via the search-and-select UI on the roster detail page —
    not typed in freehand, so only officially registered students can be
    enrolled and there's no duplicate/typo'd entry risk."""
    student_id = forms.IntegerField()

    def clean_student_id(self):
        pk = self.cleaned_data['student_id']
        try:
            student = User.objects.get(pk=pk, role='student')
        except User.DoesNotExist:
            raise forms.ValidationError('That student account was not found — it may have been removed.')
        if not student.is_active:
            raise forms.ValidationError(f'The account for "{student.display_name}" is deactivated.')
        self.cleaned_data['student'] = student
        return pk



class RosterImportStudentsForm(forms.Form):
    """Step one of the roster's 'Import Students (Excel/CSV)': pick the
    Department to match against, then upload a file of ID Numbers. Only
    already-registered student accounts in that Department get added —
    matching the roster's existing 'only officially registered students'
    rule, just done in bulk instead of one search-and-add at a time."""
    department = forms.ChoiceField(choices=DEPARTMENT_CHOICES, label='Department')
    file = forms.FileField(
        label='Excel or CSV file',
        help_text='Needs an ID Number column at minimum; Name columns are only used for reference.'
    )

    def clean_file(self):
        f = self.cleaned_data['file']
        name = (f.name or '').lower()
        if not (name.endswith('.csv') or name.endswith('.xlsx')):
            raise forms.ValidationError('Please upload a .csv or .xlsx file.')
        return f
