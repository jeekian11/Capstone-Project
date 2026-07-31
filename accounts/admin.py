from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from .models import User


class CustomUserCreationForm(UserCreationForm):
    """Django's built-in UserCreationForm hardcodes Meta.fields = ('username',)
    — since this User model has no username field, it needs to point at
    email instead for the /admin/ 'add user' form to work at all."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('email',)


class CustomUserChangeForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User
        fields = '__all__'


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = User
    list_display = ['email', 'first_name', 'last_name', 'department', 'role', 'is_active']
    list_filter = ['role', 'is_active', 'department']
    ordering = ['email']
    search_fields = ['email', 'first_name', 'last_name', 'id_number']
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'avatar')}),
        ('Role, Department & Lab', {
            'fields': ('role', 'id_number', 'department', 'year_level', 'assigned_lab'),
        }),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2', 'role'),
        }),
    )
