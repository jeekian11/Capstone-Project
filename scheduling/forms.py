from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q
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
            account_role = 'student' if requester_type == 'group' else requester_type
            account = User.objects.filter(
                Q(id_number__iexact=id_number) | Q(username__iexact=id_number),
                role=account_role,
            ).first()
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


class SessionForm(RequiresRegisteredAccountMixin, forms.ModelForm):
    """Used by SessionUpdateView to edit an already-scheduled session."""
    class Meta:
        model = Session
        fields = ['requester_type', 'requester_name', 'requester_id_number',
                  'lab', 'subject', 'date', 'start_time', 'end_time', 'student_count', 'roster']
        widgets = _DATE_TIME_WIDGETS


class SessionRequestForm(RequiresRegisteredAccountMixin, forms.ModelForm):
    """Used by RequestCreateView/RequestUpdateView to log or edit a pending request."""
    class Meta:
        model = SessionRequest
        fields = ['requester_type', 'requester_name', 'requester_id_number', 'roster',
                  'lab', 'subject', 'date', 'start_time', 'end_time', 'student_count', 'notes']
        widgets = _DATE_TIME_WIDGETS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['roster'].required = False
        self.fields['roster'].queryset = ClassRoster.objects.order_by('name')


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
            'semester', 'academic_year', 'schedule_days', 'schedule_start_time', 'schedule_end_time', 'status',
        ]
        widgets = {
            'academic_year': forms.TextInput(attrs={'placeholder': 'e.g. 2026-2027'}),
            'schedule_start_time': forms.TimeInput(attrs={'type': 'time'}),
            'schedule_end_time': forms.TimeInput(attrs={'type': 'time'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['instructor'].queryset = User.objects.filter(role='instructor').order_by('first_name', 'last_name')
        self.fields['instructor'].required = False
        self.fields['lab'].required = False
        self.fields['schedule_start_time'].required = False
        self.fields['schedule_end_time'].required = False
        if self.instance and self.instance.pk and self.instance.schedule_days:
            self.initial['schedule_days'] = self.instance.schedule_days.split(',')

    def clean_schedule_days(self):
        return ','.join(self.cleaned_data.get('schedule_days') or [])

    def clean(self):
        cleaned = super().clean()
        days = cleaned.get('schedule_days')
        start = cleaned.get('schedule_start_time')
        end = cleaned.get('schedule_end_time')
        if days and (not start or not end):
            raise forms.ValidationError('Set both a start and end time for the selected meeting day(s).')
        if start and end and start >= end:
            self.add_error('schedule_end_time', 'End time must be after start time.')
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
            raise forms.ValidationError(f'The account for "{student.get_full_name() or student.username}" is deactivated.')
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
