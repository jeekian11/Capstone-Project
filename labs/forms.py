from django import forms
from django.contrib.auth import get_user_model
from labs.models import MaintenanceLog, Lab, InventoryItem, PC
import ipaddress


class PCImportForm(forms.Form):
    file = forms.FileField(
        label='CSV or Excel file',
        help_text='Columns: Lab, PC ID, IP Address (optional), Status (optional).'
    )

    def clean_file(self):
        f = self.cleaned_data['file']
        name = f.name.lower()
        if not (name.endswith('.csv') or name.endswith('.xlsx')):
            raise forms.ValidationError('Please upload a .csv or .xlsx file.')
        return f


class LabForm(forms.ModelForm):
    class Meta:
        model = Lab
        fields = ['name', 'location', 'opening_time', 'closing_time']
        widgets = {
            'opening_time': forms.TimeInput(attrs={'type': 'time'}),
            'closing_time': forms.TimeInput(attrs={'type': 'time'}),
        }


class PCForm(forms.ModelForm):
    """
    Same fields as the plain PC ModelForm, plus one guardrail: reject
    loopback/reserved IP addresses (127.0.0.0/8, 0.0.0.0, etc). Those always
    "ping back" the server itself, not the actual lab PC, so the status
    checker would report the PC as online forever regardless of whether it's
    really on — a confusing, hard-to-debug false positive. A blank IP is
    still allowed (that PC is simply excluded from auto-checks, as before).
    """
    class Meta:
        model = PC
        fields = ['lab', 'pc_id', 'ip_address', 'status']

    def clean_ip_address(self):
        ip_address = self.cleaned_data.get('ip_address')
        if not ip_address:
            return ip_address
        try:
            addr = ipaddress.ip_address(ip_address)
        except ValueError:
            return ip_address  # let the model field's own validator handle malformed input
        if addr.is_loopback:
            raise forms.ValidationError(
                'This is a loopback address (127.x.x.x) — it always pings back the '
                'server itself, not the lab PC, so status checks would be meaningless. '
                'Enter the PC\'s real network IP address, or leave this field blank.'
            )
        return ip_address


class MaintenanceScheduleForm(forms.ModelForm):
    class Meta:
        model = MaintenanceLog
        fields = ['equipment', 'maintenance_date', 'assigned_technician', 'notes']
        widgets = {
            'maintenance_date': forms.DateInput(attrs={'type': 'date'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        User = get_user_model()
        self.fields['assigned_technician'].queryset = User.objects.filter(role__in=['admin', 'incharge'])
        self.fields['assigned_technician'].required = False



class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ['name', 'category', 'quantity', 'condition', 'lab', 'last_checked']
        widgets = {
            'last_checked': forms.DateInput(attrs={'type': 'date'}),
        }


class ReservationPCLoginForm(forms.Form):
    """
    Shown on a locked lab computer. The person enters the Student/Instructor
    ID and reservation code that the Admin or Lab In-Charge gave them when
    the reservation was approved. This never signs anyone into the web
    system — labs.views.ReservationPCLoginView matches it against the
    official schedule to decide whether to unlock the machine they're
    sitting at.
    """
    id_number = forms.CharField(label='Student ID / Instructor ID', max_length=50)
    reservation_code = forms.CharField(label='Reservation code', max_length=12)
