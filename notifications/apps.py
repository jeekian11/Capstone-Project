import os
import sys
from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = 'notifications'

    def ready(self):
        # Same reloader guard as labs.apps.LabsConfig.ready() — avoids
        # starting two copies of the loop under `runserver`'s autoreloader.
        is_dev_server = 'runserver' in sys.argv
        is_reloader_parent = is_dev_server and os.environ.get('RUN_MAIN') != 'true'
        if is_reloader_parent:
            return

        from notifications.reminders import start_background_session_reminder_checker
        start_background_session_reminder_checker()
