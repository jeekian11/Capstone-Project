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
from datetime import timedelta
from django.conf import settings
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
        update_fields = ['status']
        previous_status = pc.status

        if reachable:
            # don't clobber a manually-flagged "in_use" or "issue" status with
            # "online" — only step in when the machine looks plain online/offline
            if pc.status in ('online', 'offline') or pc.status is None:
                pc.status = new_status
                # Only bump last_active here for idle/plain machines. A
                # successful ping only proves the machine is powered on and
                # reachable on the network — it says nothing about whether
                # anyone is actually using it (e.g. it could be sitting at
                # its own lock screen). If we kept stamping last_active=now
                # for every reachable ping regardless of status, an 'in_use'
                # PC's stale-session clock (last_active + STALE_PC_FALLBACK_
                # HOURS, see release_expired_pc_sessions()) would never
                # advance as long as the machine stayed powered on — exactly
                # how a PC nobody is using can keep showing "in use by
                # <name>" indefinitely. The only thing allowed to refresh
                # last_active while a PC is 'in_use' is a real activity
                # ping from the agent (pc_agent_activity_api), which only
                # fires while the PC is genuinely unlocked and being used.
                pc.last_active = now
                update_fields.append('last_active')
            # 'in_use', 'maintenance', and 'issue' are left alone here —
            # reachability alone doesn't tell us anything new about them.
        else:
            if pc.status == 'in_use':
                # A PC that was signed in by a student just stopped
                # answering — most likely it was shut down, lost power, or
                # crashed, so the session is over. Drop it back to offline
                # and clear everything tying it to that user/guest/
                # reservation, rather than leaving a stale name and session
                # on the status page.
                pc.status = 'offline'
                pc.current_user = None
                pc.current_session = None
                pc.current_guest_name = ''
                update_fields += ['current_user', 'current_session', 'current_guest_name']
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


def release_expired_pc_sessions():
    """
    Safety net for PCs stuck showing "in use" long after they should have
    auto-relocked. Normally a PC's current_user/current_session get cleared
    by the agent calling pc-agent-end-session/pc-agent-logout when its own
    local Tkinter timer fires (see lab_pc_agent/agent.py's _auto_relock).
    That's a client-side, best-effort call with no retry (see
    notify_end_session_sync) — so if the agent crashes, the PC loses power,
    or the network hiccups at exactly the wrong moment, the server never
    hears about it and the stale "in use by <name>" sticks around forever.
    refresh_pc_statuses() only catches the case where the PC stops
    responding to ping entirely; a PC that's still up and reachable but
    whose agent silently failed to report back is NOT caught by that check,
    which is what this function is for.

    Three cases, checked in order, each with its own grace period — the
    first one that applies wins:
      1. The agent's activity heartbeat has gone quiet for
         STALE_HEARTBEAT_MINUTES. While a PC is genuinely unlocked and in
         use, its agent reports real activity roughly every 8 seconds (see
         agent.py's start_activity_reporting / pc_agent_activity_api),
         which bumps PC.last_active every time. If that heartbeat stops
         while the PC is still marked in_use, the machine itself is most
         likely fine (still powered on, still on the network — see
         refresh_pc_statuses() above for the separate "PC itself went
         unreachable" case) but its *agent process* crashed, was killed,
         or otherwise stopped running. This is the fast, general-purpose
         check — it doesn't need a reservation on file and doesn't need to
         wait for a scheduled end time, so it catches an agent dying
         partway through an otherwise-long reservation instead of leaving
         the PC stuck for the rest of it. Depends on
         activity_report_interval_seconds staying enabled (non-zero) in
         each PC's agent_config.json — a lab that deliberately disables
         activity reporting for privacy loses this fast check and falls
         back to cases 2/3 below.
      2. PC has a current_session on file — release once
         session.end datetime + STALE_PC_GRACE_MINUTES has passed. This is
         the common case (a real reservation that should have ended).
      3. PC is in_use/current_user or current_guest_name set but has NO
         current_session (e.g. Manual Unlock, or an Override where no
         active_session existed) — there's no defined end time to check
         against, so fall back to last_active + STALE_PC_FALLBACK_HOURS
         instead, a much longer grace period since we're guessing.

    Each release is logged to ActivityLog (actor=None, since this is the
    system doing it, not a person) so it's visible in the same audit trail
    as a normal lock/unlock, distinguishable by its details text.

    Returns the number of PCs released.
    """
    from labs.models import PC
    from accounts.models import ActivityLog

    heartbeat_minutes = getattr(settings, 'STALE_HEARTBEAT_MINUTES', 5)
    grace_minutes = getattr(settings, 'STALE_PC_GRACE_MINUTES', 15)
    fallback_hours = getattr(settings, 'STALE_PC_FALLBACK_HOURS', 4)

    now = timezone.localtime()
    released = 0

    stuck_pcs = PC.objects.filter(status='in_use').select_related(
        'lab', 'current_user', 'current_session'
    )

    for pc in stuck_pcs:
        session = pc.current_session
        is_expired = False
        expiry_reason = ''

        if pc.last_active and now >= pc.last_active + timedelta(minutes=heartbeat_minutes):
            is_expired = True
            expiry_reason = (
                f"no activity heartbeat from its agent in over {heartbeat_minutes} minutes "
                f"(last heard from at {timezone.localtime(pc.last_active).strftime('%H:%M:%S')}), "
                f"even though the PC itself is still reachable — the agent almost certainly "
                f"crashed or was closed"
            )
        elif session is not None:
            # session.date/end_time are plain date/time fields (no
            # timezone info of their own) — combine them into a naive
            # datetime first, then attach the server's configured
            # timezone so it compares correctly against `now`.
            naive_end = timezone.datetime.combine(session.date, session.end_time)
            session_end = timezone.make_aware(naive_end, timezone.get_current_timezone())
            if now >= session_end + timedelta(minutes=grace_minutes):
                is_expired = True
                expiry_reason = "well past when the reservation should have ended"
        elif pc.current_user_id or pc.current_guest_name:
            # No session on file to check an end time against (Manual
            # Unlock / an Override with no active_session) — fall back to
            # a longer, more conservative grace period off last_active.
            if pc.last_active and now >= pc.last_active + timedelta(hours=fallback_hours):
                is_expired = True
                expiry_reason = f"inactive for over {fallback_hours} hours with no reservation on file to check against"

        if not is_expired:
            continue

        outgoing_name = pc.current_user.display_name if pc.current_user else (pc.current_guest_name or 'someone')

        ActivityLog.objects.create(
            actor=None,
            action='pc_lock',
            target_identifier=pc.current_user.id_number if pc.current_user else '',
            pc=pc,
            details=(
                f"{pc.pc_id} ({pc.lab.name}) auto-released by the system — still showed "
                f"\"in use by {outgoing_name}\", {expiry_reason}, and the agent never reported "
                f"the session as over (likely a crash, power loss, or dropped connection on the "
                f"lab PC). No warning was shown on that PC's screen since this runs entirely on "
                f"the server."
            ),
        )

        pc.status = 'online'
        pc.current_user = None
        pc.current_session = None
        pc.current_guest_name = ''
        pc.save(update_fields=['status', 'current_user', 'current_session', 'current_guest_name'])
        released += 1

    return released


_checker_started = False  # guards against starting the loop twice in one process


def start_background_status_checker():
    """
    Starts a daemon thread that keeps pinging every PC with an IP address on
    file, on a loop, for as long as the server is running — so PC statuses
    stay accurate automatically and nobody has to click "Check status now".
    Also runs release_expired_pc_sessions() on the same loop, so PCs stuck
    showing a stale "in use by <name>" (see that function's docstring) get
    cleared automatically too, without needing a ping/reachability signal.

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
                release_expired_pc_sessions()
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