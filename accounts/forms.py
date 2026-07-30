from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from accounts.constants import DEPARTMENT_CHOICES, year_level_choices_for

User = get_user_model()


def _validate_department_year_level(cleaned):
    """Shared rule: if a year level is set, it must be one of the year
    levels that actually belong to the selected department (departments
    don't all have the same number of year levels)."""
    department = cleaned.get('department')
    year_level = cleaned.get('year_level')
    if year_level and department:
        valid_values = {v for v, _ in year_level_choices_for(department)}
        if year_level not in valid_values:
            raise ValidationError({'year_level': 'That year level isn\'t offered by the selected department.'})


class ProfileUpdateForm(forms.ModelForm):
    """Lets a logged-in user update their own display info and avatar.
    Deliberately excludes username/role/permissions — those stay
    admin-controlled via AdminUserCreateForm/UserUpdateView."""

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'avatar']


def _clean_id_number_uniqueness(form, cleaned):
    """Shared rule: the Student/Instructor ID doubles as a lab-PC login
    credential and a reservation-matching key, so it must be unique across
    every account — not just among students — or check-ins and PC logins
    could match the wrong person. Excludes the form's own instance when
    editing so saving an unchanged record doesn't trip over itself."""
    id_number = (cleaned.get('id_number') or '').strip()
    if not id_number:
        return
    qs = User.objects.filter(id_number__iexact=id_number)
    if form.instance.pk:
        qs = qs.exclude(pk=form.instance.pk)
    if qs.exists():
        form.add_error('id_number', 'A user with that ID Number already exists.')


class AdminUserCreateForm(forms.ModelForm):
    """Used by admins to create accounts for any role.

    Students are a special case: they never sign in to the SCLAMS
    dashboard, so they don't get (or need) a password — their Student ID
    alone is what they present at a lab PC. Only Admin, Lab In-Charge, and
    Instructor accounts require a password here. Student accounts only
    need Full Name, Department, Year Level, Assigned Lab, and a unique
    Student ID; a username is auto-filled from the Student ID if left
    blank so the record still satisfies the underlying login-username
    field.
    """
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput,
        required=False,
        help_text='Required for Admin, Lab In-Charge, and Instructor accounts. '
                   'Not used for Students — they sign in at the lab PC with their Student ID instead.'
    )
    password2 = forms.CharField(
        label='Confirm password',
        widget=forms.PasswordInput,
        required=False,
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'id_number', 'role',
                  'department', 'year_level', 'assigned_lab']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Username isn't asked for when creating a Student (it's derived
        # from the Student ID), so it can't be required at the field level.
        self.fields['username'].required = False
        # These fields are hidden client-side for Students via CSS, but
        # browsers can still autofill them with saved credentials (e.g. the
        # logged-in admin's own username/password) before the hide runs.
        # autocomplete="off"/"new-password" stops most browsers from doing
        # that in the first place; the clean() override below is the
        # server-side backstop in case a browser ignores this anyway.
        self.fields['username'].widget.attrs['autocomplete'] = 'off'
        self.fields['password1'].widget.attrs['autocomplete'] = 'new-password'
        self.fields['password2'].widget.attrs['autocomplete'] = 'new-password'

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        id_number = (cleaned.get('id_number') or '').strip()
        username = (cleaned.get('username') or '').strip()

        if role == 'student':
            # No password for students — clear anything typed in case the
            # role was switched after filling the fields in on the form.
            cleaned['password1'] = ''
            cleaned['password2'] = ''
            if not id_number:
                self.add_error('id_number', 'Student ID is required for Student accounts.')
            if not cleaned.get('department'):
                self.add_error('department', 'Department is required for Student accounts.')
            if not cleaned.get('year_level'):
                self.add_error('year_level', 'Year Level is required for Student accounts.')
            if not cleaned.get('assigned_lab'):
                self.add_error('assigned_lab', 'Assigned Laboratory Room is required for Student accounts.')
            # Student ID serves as the login credential for PC access, so
            # it also stands in for the dashboard username on the record.
            # The Username field is hidden client-side for Students, but
            # browsers can still silently autofill it (e.g. with the
            # logged-in admin's own saved username) before the field gets
            # hidden — so always derive it from the Student ID here rather
            # than trusting whatever value was posted.
            if id_number:
                username = id_number
                cleaned['username'] = username
        else:
            p1 = cleaned.get('password1')
            p2 = cleaned.get('password2')
            if not p1:
                self.add_error('password1', 'Password is required for this role.')
            if not p2:
                self.add_error('password2', 'Please confirm the password.')
            if p1 and p2 and p1 != p2:
                self.add_error('password2', "Passwords don't match.")
            if p1:
                try:
                    validate_password(p1)
                except ValidationError as e:
                    self.add_error('password1', e)
            if not username:
                self.add_error('username', 'Username is required for this role.')

        _clean_id_number_uniqueness(self, cleaned)
        _validate_department_year_level(cleaned)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        if user.role == 'student':
            # Students never authenticate with a Django password — this
            # also makes the login form reject any guess outright.
            user.set_unusable_password()
        else:
            user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


class AdminUserUpdateForm(forms.ModelForm):
    """Used by admins to edit an existing account. Mirrors the Student
    special-casing in AdminUserCreateForm (no password field here — that's
    handled separately by AdminSetPasswordForm/UserResetPasswordView)."""

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'id_number',
                  'department', 'year_level', 'role',
                  'assigned_lab', 'is_active']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].required = False

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get('role')
        id_number = (cleaned.get('id_number') or '').strip()
        username = (cleaned.get('username') or '').strip()

        if role == 'student':
            if not id_number:
                self.add_error('id_number', 'Student ID is required for Student accounts.')
            if not cleaned.get('department'):
                self.add_error('department', 'Department is required for Student accounts.')
            if not cleaned.get('year_level'):
                self.add_error('year_level', 'Year Level is required for Student accounts.')
            if not cleaned.get('assigned_lab'):
                self.add_error('assigned_lab', 'Assigned Laboratory Room is required for Student accounts.')
            if id_number:
                cleaned['username'] = id_number
        elif not username:
            self.add_error('username', 'Username is required for this role.')

        _clean_id_number_uniqueness(self, cleaned)
        _validate_department_year_level(cleaned)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        # If an account is switched to (or created as, though that path
        # uses AdminUserCreateForm) Student here, make sure no password
        # carries over from before the role change.
        if user.role == 'student' and user.has_usable_password():
            user.set_unusable_password()
        if commit:
            user.save()
        return user


class StudentImportForm(forms.Form):
    """Step one of 'Import Students (Excel/CSV)': pick the department the
    whole file belongs to, then upload the roster file. Every imported
    student is filed under this one department — matching the requirement
    that import is done by department rather than mixed together — and any
    Year Level column in the file is validated against that department's
    own year levels."""
    department = forms.ChoiceField(choices=DEPARTMENT_CHOICES, label='Department')
    file = forms.FileField(
        label='Excel or CSV file',
        help_text='Columns: ID Number, First Name, Last Name, Year Level, Username (optional).'
    )

    def clean_file(self):
        f = self.cleaned_data['file']
        name = (f.name or '').lower()
        if not (name.endswith('.csv') or name.endswith('.xlsx')):
            raise ValidationError('Please upload a .csv or .xlsx file.')
        return f


class AdminSetPasswordForm(forms.Form):
    """Lets an admin set a brand-new password for an existing user
    (e.g. when the user forgot theirs). Passwords are hashed and can
    never be displayed again once set — this is the only way to help
    a locked-out user."""
    password1 = forms.CharField(label='New password', widget=forms.PasswordInput)
    password2 = forms.CharField(label='Confirm new password', widget=forms.PasswordInput)

    def __init__(self, *args, target_user=None, **kwargs):
        self.target_user = target_user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1')
        p2 = cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', "Passwords don't match.")
        if p1:
            try:
                validate_password(p1, user=self.target_user)
            except ValidationError as e:
                self.add_error('password1', e)
        return cleaned

    def save(self):
        self.target_user.set_password(self.cleaned_data['password1'])
        self.target_user.save()
        return self.target_user