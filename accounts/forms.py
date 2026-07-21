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


class AdminUserCreateForm(forms.ModelForm):
    """Used by admins to create accounts for lab in-charges, instructors, etc.
    Includes a password so the new user can actually log in right away."""
    password1 = forms.CharField(
        label='Password',
        widget=forms.PasswordInput,
        help_text='The new user will log in with this password.'
    )
    password2 = forms.CharField(
        label='Confirm password',
        widget=forms.PasswordInput,
    )

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'username', 'id_number', 'role',
                  'course_year_section', 'department', 'year_level', 'assigned_lab']

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise ValidationError('A user with that username already exists.')
        return username

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1')
        p2 = cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', "Passwords don't match.")
        if p1:
            try:
                validate_password(p1)
            except ValidationError as e:
                self.add_error('password1', e)
        _validate_department_year_level(cleaned)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
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
        help_text='Columns: ID Number, First Name, Last Name, Year Level, Section (optional), Username (optional).'
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
