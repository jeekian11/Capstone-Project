import os
import sys
from django.apps import AppConfig


class LabsConfig(AppConfig):
    name = 'labs'

    def ready(self):
        # Under `runserver`, Django's autoreloader launches a parent "watcher"
        # process plus a child process that actually serves requests — only
        # the child has RUN_MAIN=true. Without this check we'd start two
        # background schedulers. In production (gunicorn/waitress/etc.)
        # RUN_MAIN is never set, so the scheduler starts normally there too.
        is_dev_server = 'runserver' in sys.argv
        is_reloader_parent = is_dev_server and os.environ.get('RUN_MAIN') != 'true'
        if is_reloader_parent:
            return

        from labs.network import start_background_status_checker
        from labs.maintenance import start_daily_activity_log_wipe
        start_background_status_checker()
        start_daily_activity_log_wipe()
