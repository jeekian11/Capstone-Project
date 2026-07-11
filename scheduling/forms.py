from django import forms
from django.contrib.auth import get_user_model
from scheduling.models import ClassRoster, RosterStudent

User = get_user_model()


class ClassRosterForm(forms.ModelForm):
    """Create/edit a class roster. On creation, students can be bulk-pasted
    one per line as 'ID Number, Full Name' instead of adding them one by one."""
    students_bulk = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'rows': 8, 'placeholder': '2023-00123, Juan Dela Cruz\n2023-00124, Maria Santos'}),
        label='Students (one per line: ID number, Full name)',
        help_text='Optional — you can also add students one at a time after creating the roster.'
    )

    class Meta:
        model = ClassRoster
        fields = ['name', 'subject', 'lab', 'instructor']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['instructor'].queryset = User.objects.filter(role='instructor').order_by('first_name', 'last_name')
        self.fields['instructor'].required = False
        self.fields['lab'].required = False

    def clean_students_bulk(self):
        raw = self.cleaned_data.get('students_bulk', '')
        parsed = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(',', 1)]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise forms.ValidationError(
                    f'Could not read this line — expected "ID number, Full name": "{line}"'
                )
            parsed.append((parts[0], parts[1]))
        return parsed

    def save_students(self, roster):
        parsed = self.cleaned_data.get('students_bulk') or []
        existing_ids = set(roster.students.values_list('id_number', flat=True))
        created = 0
        for id_number, full_name in parsed:
            if id_number in existing_ids:
                continue
            RosterStudent.objects.create(roster=roster, id_number=id_number, full_name=full_name)
            existing_ids.add(id_number)
            created += 1
        return created


class RosterStudentForm(forms.ModelForm):
    class Meta:
        model = RosterStudent
        fields = ['id_number', 'full_name']
