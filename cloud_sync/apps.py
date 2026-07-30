import os
import sys
from django.apps import AppConfig


class CloudSyncConfig(AppConfig):
    name = 'cloud_sync'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        is_dev_server = 'runserver' in sys.argv
        is_reloader_parent = is_dev_server and os.environ.get('RUN_MAIN') != 'true'
        if is_reloader_parent:
            return

        from cloud_sync.sync import start_cloud_sync_worker
        start_cloud_sync_worker()
