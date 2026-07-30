from django.db import models


class SyncQueue(models.Model):
    """
    Local outbox: one row per change that still needs to reach the cloud
    server. The edge server is always the source of truth — this table is
    just a to-do list of "things to tell the cloud about once we're online".

    Nothing reads this table to make decisions locally; it exists purely so
    the sync worker (see sync.py) knows what to send, and so nothing gets
    lost if the edge server restarts mid-sync (unsynced rows just stay
    unsynced until the next successful run).
    """
    OBJECT_TYPES = [
        ('reservation', 'Reservation'),
        ('session', 'Session'),
        ('issue', 'Issue'),
        ('activity_log', 'PC Activity Log'),
        ('notification', 'Notification'),
    ]

    object_type = models.CharField(max_length=20, choices=OBJECT_TYPES)
    # The local primary key of the row this entry describes. Combined with
    # object_type + edge_id below, this is what makes re-sending a record
    # safe (idempotent) on the cloud side instead of creating duplicates.
    local_id = models.PositiveIntegerField()
    # A stable, globally-unique id for this specific change, sent to the
    # cloud as the de-dupe key. Using uuid instead of local_id alone because
    # local_id resets per model and per edge server — this doesn't.
    edge_id = models.UUIDField()

    # Snapshot of the record at queue time, as plain JSON — the sync worker
    # sends this as-is, it never has to re-query the original model (which
    # may have changed again by the time sync actually runs).
    payload = models.JSONField()

    created_at = models.DateTimeField(auto_now_add=True)
    synced = models.BooleanField(default=False)
    synced_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default='')

    class Meta:
        indexes = [
            models.Index(fields=['synced', 'created_at']),
        ]

    def __str__(self):
        return f'{self.object_type}:{self.local_id} ({"synced" if self.synced else "pending"})'


def enqueue(object_type, local_id, payload):
    """
    Call this from wherever the record is created/updated — e.g. at the end
    of a reservation-approval view, or inside notifications/services.py.
    One line, doesn't block on the network, never raises past this call.
    """
    import uuid
    try:
        SyncQueue.objects.create(
            object_type=object_type,
            local_id=local_id,
            edge_id=uuid.uuid4(),
            payload=payload,
        )
    except Exception:
        # Queueing must never break the actual operation (the reservation
        # still got approved, the log still got written) even if this fails.
        pass
