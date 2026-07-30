"""
Pushes queued SyncQueue rows to the cloud server whenever a connection is
available. Modeled directly on labs/network.py's background status
checker — same "sleep, try, sleep again, never crash the loop" shape.

The edge server never waits on this. Nothing in the request/response cycle
for a student, instructor, or admin depends on this thread succeeding.
"""
import json
import threading
import time
import urllib.request
import urllib.error
from django.utils import timezone


def _cloud_reachable(base_url, timeout_seconds):
    """Cheap connectivity probe — a lightweight endpoint the cloud side
    exposes just for this (see cloud-side snippet below)."""
    try:
        req = urllib.request.Request(f'{base_url.rstrip("/")}/api/ping/', method='GET')
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _push_batch(base_url, api_key, timeout_seconds, batch_size=50):
    """
    Sends up to `batch_size` unsynced rows in one request, marks the ones
    the cloud accepted as synced. Returns how many were sent successfully.
    """
    from cloud_sync.models import SyncQueue

    pending = list(
        SyncQueue.objects.filter(synced=False).order_by('created_at')[:batch_size]
    )
    if not pending:
        return 0

    records = [
        {
            'edge_id': str(row.edge_id),
            'object_type': row.object_type,
            'local_id': row.local_id,
            'payload': row.payload,
            'created_at': row.created_at.isoformat(),
        }
        for row in pending
    ]

    body = json.dumps({'records': records}).encode('utf-8')
    req = urllib.request.Request(
        f'{base_url.rstrip("/")}/api/sync/',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        },
        method='POST',
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            if not (200 <= resp.status < 300):
                return 0
            result = json.loads(resp.read().decode('utf-8'))
            # Cloud side echoes back which edge_ids it actually stored —
            # only those get marked synced, so a partial failure just means
            # the rest try again next tick.
            accepted = set(result.get('accepted_edge_ids', []))
    except Exception:
        from django.db.models import F
        SyncQueue.objects.filter(id__in=[r.id for r in pending]).update(
            attempts=F('attempts') + 1
        )
        return 0

    now = timezone.now()
    accepted_rows = [row for row in pending if str(row.edge_id) in accepted]
    for row in accepted_rows:
        row.synced = True
        row.synced_at = now
        row.save(update_fields=['synced', 'synced_at'])

    return len(accepted_rows)


_sync_started = False


def start_cloud_sync_worker():
    """
    Call once from labs.apps.LabsConfig.ready() (or cloud_sync.apps), same
    guard pattern as start_background_status_checker(). Safe to call more
    than once — only the first call starts the thread.
    """
    global _sync_started
    if _sync_started:
        return
    _sync_started = True

    from django.conf import settings
    base_url = getattr(settings, 'CLOUD_SERVER_URL', None)
    api_key = getattr(settings, 'CLOUD_SYNC_API_KEY', '')
    interval = getattr(settings, 'CLOUD_SYNC_INTERVAL_SECONDS', 60)
    timeout_seconds = getattr(settings, 'CLOUD_SYNC_TIMEOUT_SECONDS', 5)

    if not base_url:
        # No cloud configured — this edge server just runs standalone.
        # Nothing breaks; the queue keeps filling but nothing reads it.
        return

    def _loop():
        from django.db import connections
        time.sleep(10)
        while True:
            try:
                if _cloud_reachable(base_url, timeout_seconds):
                    sent = 1
                    while sent > 0:
                        sent = _push_batch(base_url, api_key, timeout_seconds)
            except Exception:
                pass
            finally:
                connections.close_all()
            time.sleep(interval)

    thread = threading.Thread(target=_loop, name='cloud-sync-worker', daemon=True)
    thread.start()
