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
    Runs the OS command that releases this lab computer's lock/kiosk screen
    once a student's Student ID and password have been verified.

    How a lab PC is actually "locked" down (a kiosk browser, a custom lock
    screen service, etc.) is a deployment decision this project can't assume,
    so the command itself is configured in settings.PC_UNLOCK_COMMAND rather
    than hardcoded here — same idea as ip_address being something each site
    has to fill in for their own machines.

    Returns (success: bool, message: str). A False success still means the
    student was verified; it only means the unlock command itself didn't run
    (e.g. not configured yet, or the local command failed).
    """
    from django.conf import settings

    command = getattr(settings, 'PC_UNLOCK_COMMAND', None)
    if not command:
        return False, 'No PC_UNLOCK_COMMAND is configured in settings — the student was verified but no unlock command was run.'

    system = platform.system().lower()
    try:
        subprocess.run(
            command,
            shell=(system == 'windows'),
            timeout=10,
            check=True,
        )
        return True, 'Unlock command executed.'
    except Exception as e:
        return False, f'Unlock command failed: {e}'


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
