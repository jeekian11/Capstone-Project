from django import forms
from django.contrib.auth import get_user_model
from labs.models import Lab, InventoryItem
from notifications.models import AlertSettings

User = get_user_model()

TARGET_AUDIENCE_CHOICES = [
    ('all', 'All users'),
    ('admin', 'All admins'),
    ('incharge', 'All lab in-charges'),
    ('instructor', 'All instructors'),
    ('specific', 'Specific user(s)'),
]


class NotificationComposeForm(forms.Form):
    notification_type = forms.ChoiceField(choices=[
        ('booking_confirmation', 'Booking Confirmation'),
        ('schedule_reminder', 'Schedule Reminder'),
        ('maintenance_alert', 'Maintenance Alert'),
        ('system_announcement', 'System Announcement'),
    ])
    target_audience = forms.ChoiceField(choices=TARGET_AUDIENCE_CHOICES)
    specific_recipients = forms.ModelMultipleChoiceField(
        queryset=User.objects.exclude(role='student'), required=False
    )

    # Booking Confirmation
    booking_details = forms.CharField(widget=forms.Textarea, required=False)

    # Schedule Reminder
    reminder_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False)
    reminder_time = forms.TimeField(widget=forms.TimeInput(attrs={'type': 'time'}), required=False)
    reminder_lab = forms.ModelChoiceField(queryset=Lab.objects.all(), required=False)
    reminder_event = forms.CharField(required=False)

    # Maintenance Alert
    maintenance_equipment = forms.ModelChoiceField(queryset=InventoryItem.objects.all(), required=False)
    maintenance_issue = forms.CharField(required=False)
    maintenance_date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}), required=False)
    maintenance_time = forms.TimeField(widget=forms.TimeInput(attrs={'type': 'time'}), required=False)

    # System Announcement
    announcement_text = forms.CharField(widget=forms.Textarea, required=False)

    def clean(self):
        cleaned = super().clean()
        n_type = cleaned.get('notification_type')
        audience = cleaned.get('target_audience')
        specific = cleaned.get('specific_recipients')

        if audience == 'specific' and not specific:
            self.add_error('specific_recipients', 'Select at least one recipient.')

        if n_type == 'booking_confirmation' and not cleaned.get('booking_details'):
            self.add_error('booking_details', 'Booking details are required.')
        if n_type == 'schedule_reminder':
            for f in ('reminder_date', 'reminder_time', 'reminder_lab', 'reminder_event'):
                if not cleaned.get(f):
                    self.add_error(f, 'This field is required for a schedule reminder.')
        if n_type == 'maintenance_alert':
            for f in ('maintenance_equipment', 'maintenance_issue', 'maintenance_date', 'maintenance_time'):
                if not cleaned.get(f):
                    self.add_error(f, 'This field is required for a maintenance alert.')
        if n_type == 'system_announcement' and not cleaned.get('announcement_text'):
            self.add_error('announcement_text', 'Announcement text is required.')

        return cleaned

    def build_title_and_message(self):
        n_type = self.cleaned_data['notification_type']
        if n_type == 'booking_confirmation':
            return 'Booking Confirmation', self.cleaned_data['booking_details']
        if n_type == 'schedule_reminder':
            d = self.cleaned_data
            return (
                'Schedule Reminder',
                f"{d['reminder_event']} at {d['reminder_lab'].name} on "
                f"{d['reminder_date'].strftime('%b %d, %Y')} {d['reminder_time'].strftime('%H:%M')}."
            )
        if n_type == 'maintenance_alert':
            d = self.cleaned_data
            return (
                'Maintenance Alert',
                f"{d['maintenance_equipment'].name}: {d['maintenance_issue']} — scheduled "
                f"{d['maintenance_date'].strftime('%b %d, %Y')} {d['maintenance_time'].strftime('%H:%M')}."
            )
        if n_type == 'system_announcement':
            return 'System Announcement', self.cleaned_data['announcement_text']
        return 'Notification', ''

    def resolve_recipients(self):
        audience = self.cleaned_data['target_audience']
        if audience == 'all':
            return User.objects.filter(is_active=True).exclude(role='student')
        if audience == 'specific':
            return self.cleaned_data['specific_recipients']
        return User.objects.filter(is_active=True, role=audience)


class AlertSettingsForm(forms.ModelForm):
    """Admin-only toggles for which categories of AUTO-GENERATED
    notifications the system is allowed to send. Manual "+ Create
    Notification" announcements are never gated by these."""

    class Meta:
        model = AlertSettings
        fields = [
            'reservation_notifications', 'pc_status_alerts', 'maintenance_alerts',
            'login_security_alerts', 'session_reminders', 'system_announcements',
        ]
        widgets = {
            field: forms.CheckboxInput() for field in fields
        }
