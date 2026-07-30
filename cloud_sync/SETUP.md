# cloud_sync — setup

## 1. I-drop ang `cloud_sync/` folder sa loob ng `compulab/` (kasabay ng `labs/`, `scheduling/`, atbp.)

## 2. `compulab/settings.py`

```python
INSTALLED_APPS = [
    ...
    'labs',
    'scheduling',
    'issues',
    'notifications',
    'cloud_sync',
]

# --- Cloud sync ---
CLOUD_SERVER_URL = 'https://your-cloud-server.example.com'   # blangko/wag i-set = walang sync, edge lang
CLOUD_SYNC_API_KEY = 'a-long-random-shared-secret'
CLOUD_SYNC_INTERVAL_SECONDS = 60
CLOUD_SYNC_TIMEOUT_SECONDS = 5
```

## 3. `python manage.py makemigrations cloud_sync && python manage.py migrate`

## 4. I-hook ang `enqueue()` sa mga existing na views/services

Halimbawa, sa `notifications/services.py`, sa dulo ng isang `notify_*`
function pagkatapos gumawa ng `Notification`:

```python
from cloud_sync.models import enqueue

enqueue('notification', notification.id, {
    'type': notification.notification_type,
    'message': notification.message,
    'user_id': notification.user_id,
    'created_at': notification.created_at.isoformat(),
})
```

Gawin din ito sa `issues/views.py` (pag-report ng issue) at saanman
gagawa/mag-a-update ng reservation/session/activity log na gusto niyong
makita sa cloud dashboard.

## 5. Sa cloud server (hiwalay na Django project o kahit simpleng Flask app)

Dalawang endpoint lang ang kailangan:

- `GET /api/ping/` — 200 OK lang, walang laman. Ginagamit ng edge para
  malaman kung may connection.
- `POST /api/sync/` — tinatanggap yung `{"records": [...]}`, ise-save sa
  cloud DB (o kahit basic read-only mirror table), tapos sasagot ng
  `{"accepted_edge_ids": [...]}` — yung mga `edge_id` na successfully
  na-save. I-check ang `edge_id` bago mag-save (idempotent) para hindi
  mag-duplicate kung paulit-ulit na-send ang parehong batch.

## 6. Tailscale (remote access papunta sa edge server)

1. I-install ang Tailscale sa edge server mismo (`tailscale up`).
2. I-install din sa laptop/phone na gagamitin niyo pang-remote.
3. Parehong i-connect sa isang Tailscale network (tailnet) — libre para sa
   maliit na bilang ng devices.
4. Makikita niyo na yung edge server sa sarili nitong Tailscale IP
   (hal. `100.x.x.x`) — pwede na niyo itong i-access (Django admin,
   dashboard) kahit saan galing, parang nasa loob kayo ng school network.
5. Walang kailangang buksan sa router/firewall ng school — outbound lang
   ang kailangan, na karaniwang bukas na.
