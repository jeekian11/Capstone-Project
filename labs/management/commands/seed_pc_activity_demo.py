"""
Seeds one realistic PCActivityLog session so the PC Activity Log page has
something demo-able to look at — a student logging in, switching between a
couple of apps, and (as the last thing they do) opening SCLAMS's own PC
Activity Log page in Chrome. Since the browser tab title is
"SCLAMS — {page title}" (see templates/base.html) and Chrome appends
" - Google Chrome" to whatever the page title is, that sample ends up
looking exactly like:

    SCLAMS — PC Activity Log - Google Chrome

...which is a genuine, unfaked example of what the tracker actually
records — it's just reading the PC's real foreground window title, so if a
student really does browse SCLAMS itself in Chrome, this is what shows up.

Usage:
    python manage.py seed_pc_activity_demo
    python manage.py seed_pc_activity_demo --pc PC01 --student STU-2024-1001
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from labs.models import PC, PCActivityLog


class Command(BaseCommand):
    help = "Seeds one realistic demo PC Activity Log session, ending on a 'SCLAMS — PC Activity Log - Google Chrome' sample."

    def add_arguments(self, parser):
        parser.add_argument('--pc', help="PC ID to use (e.g. PC01). Defaults to any existing PC.")
        parser.add_argument('--student', help="Student/Instructor ID number to use. Defaults to any existing student.")
        parser.add_argument('--minutes-ago', type=int, default=25, help="How many minutes ago the session started (default 25).")

    def handle(self, *args, **options):
        User = get_user_model()

        pc = None
        if options['pc']:
            pc = PC.objects.filter(pc_id__iexact=options['pc']).select_related('lab').first()
            if not pc:
                raise CommandError(f"No PC found with ID '{options['pc']}'.")
        else:
            pc = PC.objects.select_related('lab').first()
            if not pc:
                raise CommandError("No PCs exist yet — add one under Manage Labs first.")

        student = None
        if options['student']:
            student = User.objects.filter(id_number__iexact=options['student']).first()
            if not student:
                raise CommandError(f"No user found with ID number '{options['student']}'.")
        else:
            student = User.objects.filter(role='student').first() or User.objects.filter(role__in=['instructor', 'incharge']).first()
            if not student:
                raise CommandError("No student/instructor accounts exist yet — add one under User & Role Management first.")

        start = timezone.now() - timedelta(minutes=options['minutes_ago'])

        # A believable little browsing session: log in, do some coursework,
        # then check their own activity on SCLAMS itself right before this
        # command's data ends (so that last sample is the realistic example).
        titles = [
            'Windows Login',
            'Untitled Document - Google Docs - Google Chrome',
            'Untitled Document - Google Docs - Google Chrome',
            'main.py - Visual Studio Code',
            'main.py - Visual Studio Code',
            'SCLAMS — PC Activity Log - Google Chrome',
        ]

        created = []
        for i, title in enumerate(titles):
            log = PCActivityLog.objects.create(pc=pc, student=student, window_title=title)
            PCActivityLog.objects.filter(pk=log.pk).update(captured_at=start + timedelta(minutes=i * 4))
            created.append(log)

        pc.status = 'in_use'
        pc.current_user = student
        pc.last_active = timezone.now()
        pc.save(update_fields=['status', 'current_user', 'last_active'])

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(created)} PCActivityLog samples for {student.display_name} on "
            f"{pc.pc_id} ({pc.lab.name}), starting {options['minutes_ago']} minute(s) ago. "
            f"Check PC Activity Log — the most recent sample is 'SCLAMS — PC Activity Log - Google Chrome'."
        ))
