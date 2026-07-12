from django import forms
from django.contrib.auth import get_user_model
from labs.models import MaintenanceLog, Lab, InventoryItem


class LabForm(forms.ModelForm):
    class Meta:
        model = Lab
        fields = ['name', 'location', 'opening_time', 'closing_time']
        widgets = {
            'opening_time': forms.TimeInput(attrs={'type': 'time'}),
            'closing_time': forms.TimeInput(attrs={'type': 'time'}),
        }


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
