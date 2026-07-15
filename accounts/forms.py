from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

User = get_user_model()


class ProfileUpdateForm(forms.ModelForm):
    """Lets a logged-in user update their own display info and avatar.
    Deliberately excludes username/role/permissions — those stay
    admin-controlled via AdminUserCreateForm/UserUpdateView."""

    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'avatar']


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
        fields = ['first_name', 'last_name', 'username', 'id_number', 'email', 'role',
                  'course_year_section', 'department', 'assigned_lab']

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
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data['password1'])
        if commit:
            user.save()
        return user


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
