"""
Actually talks to the PCs on the network to find out if they're really on/off —
instead of just trusting whatever status was last typed in manually.

Uses the OS's own `ping` command (works on Windows and Linux without needing
admin/root rights), run in parallel across all PCs so checking a whole lab
takes about as long as pinging one machine, not all of them one by one.
"""
import platform
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from django.utils import timezone


def _ping_once(ip_address, timeout_seconds=1):
    """Returns True if the host replies to a single ping, False otherwise."""
    if not ip_address:
        return None  # no IP on file — can't check, caller should leave status alone

    system = platform.system().lower()
    if system == 'windows':
        # -n 1 = one echo request, -w = timeout in milliseconds
        cmd = ['ping', '-n', '1', '-w', str(int(timeout_seconds * 1000)), ip_address]
    else:
        # -c 1 = one echo request, -W = timeout in seconds (Linux); macOS uses -t
        cmd = ['ping', '-c', '1', '-W', str(int(timeout_seconds)), ip_address]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds + 2,
        )
        return result.returncode == 0
    except Exception:
        return False


def check_pc(pc):
    """Ping one PC and return (pc, is_reachable_or_None)."""
    return pc, _ping_once(pc.ip_address)


def unlock_pc(pc):
    """
    Tells this specific lab PC to release its own lock/kiosk screen, once a
    student's Student ID and password have been verified.

    Internet-cafe style: the server doesn't log into the PC or run anything
    on it directly. Instead it sends a small signed HTTP request to the
    agent running locally on that PC (see lab_pc_agent/agent.py), addressed
    with the PC's own ip_address. The agent is the one that actually knows
    how to unlock that machine (Windows lock screen, kiosk browser, etc.)
    and runs the real command itself, locally.

    Falls back to settings.PC_UNLOCK_COMMAND (run on the server itself) only
    if the agent can't be reached — useful for local testing, or a lab where
    the server IS one of the PCs.

    Returns (success: bool, message: str). A False success still means the
    student was verified; it only means the unlock signal didn't get through
    (e.g. agent not running yet, PC offline, wrong IP on file).
    """
    from django.conf import settings
    import json
    import urllib.request
    import urllib.error

    if pc.ip_address:
        port = getattr(settings, 'PC_AGENT_PORT', 5555)
        secret = getattr(settings, 'PC_AGENT_SHARED_SECRET', '')
        timeout = getattr(settings, 'PC_AGENT_TIMEOUT_SECONDS', 4)
        url = f'http://{pc.ip_address}:{port}/unlock'
        payload = json.dumps({'secret': secret}).encode('utf-8')
        request = urllib.request.Request(
            url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if 200 <= response.status < 300:
                    return True, f'Unlock signal sent to agent at {pc.ip_address}.'
                return False, f'Agent at {pc.ip_address} responded with status {response.status}.'
        except urllib.error.HTTPError as e:
            if e.code == 403:
                return False, f'Agent at {pc.ip_address} rejected the request — check PC_AGENT_SHARED_SECRET matches on both sides.'
            return False, f'Agent at {pc.ip_address} returned an error: {e.code} {e.reason}'
        except urllib.error.URLError as e:
            return False, f'Could not reach agent at {pc.ip_address}:{port} — is the agent running on that PC? ({e.reason})'
        except Exception as e:
            return False, f'Unlock request to {pc.ip_address} failed: {e}'

    # No IP on file for this PC — fall back to a server-local command, if any.
    command = getattr(settings, 'PC_UNLOCK_COMMAND', None)
    if not command:
        return False, 'This PC has no IP address on file and no PC_UNLOCK_COMMAND fallback is configured — the student was verified but nothing was unlocked.'

    system = platform.system().lower()
    try:
        subprocess.run(
            command,
            shell=(system == 'windows'),
            timeout=10,
            check=True,
        )
        return True, 'Unlock command executed on the server (fallback — no IP on file for this PC).'
    except Exception as e:
        return False, f'Fallback unlock command failed: {e}'


def send_override_warning(pc, admin_name, seconds):
    """
    Tells the agent on this PC to show an on-screen countdown warning
    before an Admin/In-Charge Override forcibly ends the current user's
    session — gives them `seconds` to notice and save their work before
    the agent auto-locks the PC on its own (see agent.py's WarningBanner
    and LockScreen.start_override_countdown()).

    Fire-and-forget in spirit: if this fails (agent offline, wrong IP,
    etc.) the override still proceeds on schedule from the server's side
    — the PC just won't have shown a warning first. Returns
    (success: bool, message: str).
    """
    from django.conf import settings
    import json
    import urllib.request
    import urllib.error

    if not pc.ip_address:
        return False, 'This PC has no IP address on file — cannot send a warning.'

    port = getattr(settings, 'PC_AGENT_PORT', 5555)
    secret = getattr(settings, 'PC_AGENT_SHARED_SECRET', '')
    timeout = getattr(settings, 'PC_AGENT_TIMEOUT_SECONDS', 4)
    url = f'http://{pc.ip_address}:{port}/override-warning'
    payload = json.dumps({'secret': secret, 'seconds': seconds, 'admin_name': admin_name}).encode('utf-8')
    request = urllib.request.Request(
        url, data=payload, headers={'Content-Type': 'application/json'}, method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if 200 <= response.status < 300:
                return True, f'Warning sent to agent at {pc.ip_address}.'
            return False, f'Agent at {pc.ip_address} responded with status {response.status}.'
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return False, f'Agent at {pc.ip_address} rejected the request — check PC_AGENT_SHARED_SECRET matches on both sides.'
        return False, f'Agent at {pc.ip_address} returned an error: {e.code} {e.reason}'
    except urllib.error.URLError as e:
        return False, f'Could not reach agent at {pc.ip_address}:{port} — is the agent running on that PC? ({e.reason})'
    except Exception as e:
        return False, f'Warning request to {pc.ip_address} failed: {e}'


def refresh_pc_statuses(pcs):
    """
    Pings every PC in `pcs` that has an IP address on file, in parallel,
    and updates their status/last_active based on the real reply.
    PCs without an IP address are left untouched (can't be checked).

    Returns a summary dict: {'checked': n, 'online': n, 'offline': n, 'skipped': n}
    """
    pcs = list(pcs)
    summary = {'checked': 0, 'online': 0, 'offline': 0, 'skipped': 0}
    if not pcs:
        return summary

    with ThreadPoolExecutor(max_workers=min(32, max(4, len(pcs)))) as pool:
        results = list(pool.map(lambda pc: check_pc(pc), pcs))

    now = timezone.now()
    for pc, reachable in results:
        if reachable is None:
            summary['skipped'] += 1
            continue
        summary['checked'] += 1
        new_status = 'online' if reachable else 'offline'
        update_fields = ['status', 'last_active']
        previous_status = pc.status

        if reachable:
            # don't clobber a manually-flagged "in_use" or "issue" status with
            # "online" — only step in when the machine looks plain online/offline
            if pc.status in ('online', 'offline') or pc.status is None:
                pc.status = new_status
            pc.last_active = now
        else:
            if pc.status == 'in_use':
                # A PC that was signed in by a student just stopped
                # answering — most likely it was shut down or restarted, so
                # the student's session is over. Drop it back to offline and
                # clear who was using it, rather than leaving a stale name
                # on the status page.
                pc.status = 'offline'
                pc.current_user = None
                update_fields.append('current_user')
            elif pc.status in ('online', 'offline') or pc.status is None:
                pc.status = new_status
            # an 'issue'-flagged PC is left alone either way

        pc.save(update_fields=update_fields)
        summary['online' if reachable else 'offline'] += 1

        # Auto-notify only on a genuine online<->offline transition — not
        # on every refresh tick, and not for PCs left alone because they're
        # flagged 'issue'/'maintenance'/'in_use' above.
        if pc.status != previous_status:
            from notifications import services as notify_service
            if pc.status == 'offline' and previous_status in ('online', 'in_use'):
                notify_service.notify_pc_offline(pc)
            elif pc.status == 'online' and previous_status == 'offline':
                notify_service.notify_pc_online(pc)

    return summary


_checker_started = False  # guards against starting the loop twice in one process


def start_background_status_checker():
    """
    Starts a daemon thread that keeps pinging every PC with an IP address on
    file, on a loop, for as long as the server is running — so PC statuses
    stay accurate automatically and nobody has to click "Check status now".

    Safe to call more than once: only the first call actually starts a
    thread. Meant to be called once from labs.apps.LabsConfig.ready().
    """
    global _checker_started
    if _checker_started:
        return
    _checker_started = True

    from django.conf import settings
    interval = getattr(settings, 'PC_STATUS_CHECK_INTERVAL_SECONDS', 60)

    def _loop():
        # Import here (not at module load time) so this only touches the DB
        # once Django is fully set up.
        from django.db import connections
        from labs.models import PC

        # Do an initial check shortly after startup, then settle into the
        # regular interval, so the dashboard isn't stale for the first
        # minute after the server (re)starts.
        time.sleep(5)
        while True:
            try:
                pcs = PC.objects.exclude(ip_address__isnull=True)
                refresh_pc_statuses(pcs)
            except Exception:
                # A transient DB hiccup or ping failure shouldn't kill the
                # background loop — just try again next interval.
                pass
            finally:
                # Each Django DB connection is only meant to be used by the
                # thread/request that opened it; close it out here so this
                # long-lived thread doesn't hold a stale/broken connection
                # between checks.
                connections.close_all()
            time.sleep(interval)

    thread = threading.Thread(target=_loop, name='pc-status-auto-checker', daemon=True)
    thread.start()
