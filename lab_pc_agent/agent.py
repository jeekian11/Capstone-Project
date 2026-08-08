"""
CompuLab Lab PC Agent (with built-in lock screen)
===================================================

Runs on EVERY lab PC (not on the server). Two jobs in one small program:

  1. Shows a fullscreen "PC Locked" overlay the moment it starts, blocking
     the desktop underneath — no separate kiosk software needed.
  2. Listens on a local port for signed requests from the CompuLab server.
     When a student is verified, the server sends "/unlock" and this agent
     hides the overlay. The server can also send "/lock" to re-lock the PC
     remotely (e.g. when a reservation/session ends).

Honesty check, please read: this is an APPLICATION-level lock (a window
sitting on top of everything), not real OS-level security. Someone who
knows to press Ctrl+Alt+Delete and open Task Manager can still end the
`python.exe`/`agent.exe` process and get to the desktop. This is the same
limitation basic kiosk lock tools have out of the box — if you need it to
be harder to bypass, that requires additional Windows admin/group-policy
lockdown (disabling Task Manager, restricting Ctrl+Alt+Del options, running
as a locked-down standard user account, etc.), which is a separate,
site-specific hardening step beyond what a script alone can do.

Requires only the Python standard library (tkinter ships with the normal
Windows Python installer) — nothing to `pip install`, EXCEPT for one
optional feature: reading the browser's address bar (so the PC Activity
Log can identify sites like ChatGPT/Claude that never put their own name
in the window title). That needs:
     pip install uiautomation
If you skip this, the agent still runs fine — window-title tracking keeps
working exactly as before, just without the extra URL-based accuracy.

--------------------------------------------------------------------------
SETUP ON EACH LAB PC
--------------------------------------------------------------------------
1. Copy this whole `lab_pc_agent` folder to the PC (e.g. C:\\CompuLabAgent).
2. (Optional, for accurate site detection in the PC Activity Log)
   Run:  pip install uiautomation
3. Edit `agent_config.json`:
     - "secret"  -> must be EXACTLY the same string as PC_AGENT_SHARED_SECRET
                    in the server's settings.py
     - "port"    -> must match PC_AGENT_PORT in settings.py (default 5555)
     - "lock_title" / "lock_subtitle" -> the text shown on the lock screen
4. Test manually first:  python agent.py
   A fullscreen "PC Locked" screen should appear immediately.
5. Once it works, set it to run automatically at every startup — see
   "RUNNING AS A STARTUP SERVICE" below.
6. Allow inbound connections on the chosen port (default 5555) for
   python.exe through Windows Firewall, at least on the lab's network.
--------------------------------------------------------------------------
RUNNING AS A STARTUP SERVICE (pick one)
--------------------------------------------------------------------------
Easiest — Task Scheduler:
  1. Task Scheduler -> Create Task
  2. Triggers tab -> New -> "At startup" (or "At log on")
  3. Actions tab -> New -> Program: pythonw.exe   (pythonw = no console window)
     Arguments: C:\\CompuLabAgent\\agent.py
  4. General tab -> check "Run whether user is logged on or not"

More robust — NSSM (Non-Sucking Service Manager, nssm.cc):
  nssm install CompuLabAgent "C:\\Path\\To\\pythonw.exe" "C:\\CompuLabAgent\\agent.py"
  nssm start CompuLabAgent
--------------------------------------------------------------------------
"""
import datetime
import json
import os
import queue
import random
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox
import urllib.request
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import hardening

try:
    from PIL import Image, ImageTk, ImageEnhance, ImageDraw, ImageChops
    _PIL_AVAILABLE = True
except Exception:
    _PIL_AVAILABLE = False

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agent_config.json')

# Commands passed between threads and the Tk main thread — Tk widgets can
# only safely be touched from the thread that created them, so neither the
# HTTP handler nor the login-submission thread ever calls into the UI
# directly; they drop a message here and the UI thread picks it up on its
# own polling loop. Kinds of messages:
#   'unlock' / 'lock'            -> from the HTTP server (remote lock/unlock)
#   ('login_result', dict)       -> from the on-screen login form's network call
#   ('pc_info', dict)            -> from fetch_pc_info_in_background (server-assigned pc_id/lab_name)
#   ('network_status', bool)     -> from check_network_in_background (can we reach the CompuLab server right now?)
#   ('override_warning', int, str) -> from the HTTP server (Admin/In-Charge Override); (seconds, admin_name)
_command_queue = queue.Queue()


def load_config():
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


class UnlockHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if os.environ.get('COMPULAB_AGENT_VERBOSE'):
            super().log_message(fmt, *args)

    def _send(self, status, body):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()
        self.wfile.write(json.dumps(body).encode('utf-8'))

    def do_OPTIONS(self):
        # CORS preflight, needed so a browser-based control page (e.g. opened
        # from a phone) is allowed to call this agent's API.
        #
        # Access-Control-Allow-Private-Network is required on top of the
        # normal CORS headers because of Chrome's newer "Private Network
        # Access" (PNA) check: when a page (especially one opened from
        # file://, like manual_unlock.html) tries to fetch() a private/
        # local-network IP (like a lab PC's 192.168.x.x address), Chrome
        # sends this same OPTIONS preflight but ALSO requires this specific
        # header in the response before it'll let the real request through.
        # Without it, Chrome silently aborts the connection right after
        # connecting — which shows up here as the client resetting the
        # socket before sending any request line, and in the browser as a
        # generic "Failed to fetch" with no other explanation.
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()

    def _handle_signal(self, action):
        try:
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length) if length else b''
            data = json.loads(raw or b'{}')
        except Exception:
            self._send(400, {'ok': False, 'error': 'bad request body'})
            return

        config = load_config()
        if data.get('secret') != config.get('secret'):
            self._send(403, {'ok': False, 'error': 'invalid secret'})
            return

        _command_queue.put(action)
        self._send(200, {'ok': True})

    def _handle_override_warning(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length) if length else b''
            data = json.loads(raw or b'{}')
        except Exception:
            self._send(400, {'ok': False, 'error': 'bad request body'})
            return

        config = load_config()
        if data.get('secret') != config.get('secret'):
            self._send(403, {'ok': False, 'error': 'invalid secret'})
            return

        try:
            seconds = int(data.get('seconds', 15))
        except (TypeError, ValueError):
            seconds = 15
        admin_name = str(data.get('admin_name') or 'An admin')

        _command_queue.put(('override_warning', seconds, admin_name))
        self._send(200, {'ok': True})

    def do_POST(self):
        if self.path == '/unlock':
            self._handle_signal('unlock')
        elif self.path == '/lock':
            self._handle_signal('lock')
        elif self.path == '/override-warning':
            self._handle_override_warning()
        else:
            self._send(404, {'ok': False, 'error': 'not found'})

    def do_GET(self):
        if self.path == '/ping':
            self._send(200, {'ok': True, 'status': 'agent running'})
        else:
            self._send(404, {'ok': False, 'error': 'not found'})


def start_http_server(port):
    server = ThreadingHTTPServer(('0.0.0.0', port), UnlockHandler)
    thread = threading.Thread(target=server.serve_forever, name='compulab-agent-http', daemon=True)
    thread.start()
    return server


def notify_end_session_sync(server_url, secret, reason, timeout=3):
    """
    Blocking version of notify_end_session_in_background — used right
    before triggering an actual Windows logoff, since the whole process
    (including any background thread) is about to be torn down by Windows.
    Best-effort: any failure here still lets the logoff proceed.
    """
    url = server_url.rstrip('/') + '/labs/api/pc-agent-end-session/'
    payload = json.dumps({'secret': secret, 'reason': reason}).encode('utf-8')
    request = urllib.request.Request(
        url, data=payload, headers={'Content-Type': 'application/json'}, method='POST',
    )
    try:
        urllib.request.urlopen(request, timeout=timeout)
    except Exception as e:
        print(f'[agent] Could not notify server of session end before logoff: {e}')


def submit_login_in_background(server_url, secret, id_number, reservation_code):
    """
    Runs on a throwaway background thread (started by the Submit button) so
    the UI never freezes while waiting on the network. Never touches Tk
    directly — it only ever puts its result on the queue for the main
    thread's poll_queue to pick up.
    """
    def worker():
        url = server_url.rstrip('/') + '/labs/api/pc-agent-login/'
        payload = json.dumps({
            'secret': secret,
            'id_number': id_number,
            'reservation_code': reservation_code,
        }).encode('utf-8')
        request = urllib.request.Request(
            url, data=payload, headers={'Content-Type': 'application/json'}, method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                result = json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            try:
                result = json.loads(e.read().decode('utf-8'))
            except Exception:
                result = {'ok': False, 'error': f'Server error ({e.code}).'}
        except urllib.error.URLError as e:
            result = {'ok': False, 'error': f"Can't reach the CompuLab server right now ({e.reason}). Ask your Lab In-Charge for help."}
        except Exception as e:
            result = {'ok': False, 'error': f'Unexpected error: {e}'}

        _command_queue.put(('login_result', result))

    threading.Thread(target=worker, daemon=True).start()


def submit_logout_in_background(server_url, secret, reason):
    """
    Tells the server this PC is no longer in use (student clicked "Log Out",
    or the reservation's end_time was reached). Fire-and-forget: the agent
    has already decided to re-lock locally by the time this is called, so
    we don't block the UI waiting on the server's reply — this just keeps
    the admin dashboard / PC status view in sync with reality.
    """
    def worker():
        url = server_url.rstrip('/') + '/labs/api/pc-agent-logout/'
        payload = json.dumps({'secret': secret, 'reason': reason}).encode('utf-8')
        request = urllib.request.Request(
            url, data=payload, headers={'Content-Type': 'application/json'}, method='POST',
        )
        try:
            urllib.request.urlopen(request, timeout=8).close()
        except Exception:
            pass  # nothing meaningful to show the person for a background sync call

    threading.Thread(target=worker, daemon=True).start()


def fetch_pc_info_in_background(server_url, secret):
    """Asks the server which pc_id / lab_name are actually assigned to THIS
    machine in the admin panel (Manage PCs -> Add/Edit), instead of trusting
    whatever was hand-typed into agent_config.json's "pc_name"/"lab_name" —
    which can drift out of sync if the admin renames the PC later. The
    server resolves the PC by this machine's LAN IP address, the same way
    it already does for the other pc-agent-* endpoints (see
    labs/views.py:pc_agent_info_api).

    Runs on a background thread and drops the result on the same
    _command_queue the login flow uses, so LockScreen.apply_pc_info() runs
    safely back on the Tk main thread. Best-effort/fire-and-forget: on any
    failure (server unreachable, PC has no IP on file / isn't registered
    yet, wrong secret) the lock screen just keeps showing agent_config.json's
    values — nothing crashes and nothing blocks the UI.
    """
    def worker():
        query = urllib.parse.urlencode({'secret': secret})
        url = server_url.rstrip('/') + '/labs/api/pc-agent-info/?' + query
        request = urllib.request.Request(url, method='GET')
        try:
            with urllib.request.urlopen(request, timeout=6) as response:
                result = json.loads(response.read().decode('utf-8'))
        except Exception:
            result = {'ok': False}
        _command_queue.put(('pc_info', result))

    threading.Thread(target=worker, daemon=True).start()


def check_network_in_background(server_url, interval=10):
    """Runs forever on its own daemon thread (started once from main() at
    startup): every `interval` seconds, tries to reach the CompuLab
    server and drops the result on _command_queue as ('network_status',
    bool), so LockScreen.apply_network_status() can update the NETWORK
    status pill to match reality instead of the old hardcoded 'Connected'
    text that never changed.

    "Connected" here specifically means "can this PC reach the CompuLab
    server" — that's what actually matters for this kiosk (login/unlock
    won't work otherwise), not general internet access. A short timeout
    is used so one slow/unreachable check doesn't leave the status stuck
    for a long time before the next attempt.
    """
    def worker():
        while True:
            try:
                request = urllib.request.Request(server_url.rstrip('/') + '/', method='GET')
                urllib.request.urlopen(request, timeout=4).close()
                connected = True
            except urllib.error.HTTPError:
                # Server answered — even an error page means the network
                # path to it is up, which is what we're actually checking.
                connected = True
            except Exception:
                connected = False
            _command_queue.put(('network_status', connected))
            time.sleep(interval)

    threading.Thread(target=worker, name='compulab-agent-netcheck', daemon=True).start()



def get_foreground_hwnd():
    """The raw Win32 handle of whatever window currently has focus, or None.
    Fetched once per sample and reused for both title and URL capture below
    so they're always describing the exact same window — using two
    different "what's foreground" lookups (GetForegroundWindow vs UI
    Automation's own foreground-control lookup) can disagree, especially
    for browsers, and that mismatch is what caused a real mislabeling bug:
    the URL lookup would silently fail to find the address bar, and the
    server would fall back to guessing the site from title keywords —
    which is wrong whenever the page title just happens to contain another
    site's name (e.g. a ChatGPT conversation titled "Upload Video to
    GitHub" was mislabeled "GitHub" instead of "ChatGPT")."""
    if os.name != 'nt':
        return None
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        return hwnd or None
    except Exception:
        return None


def get_active_window_title(hwnd=None):
    """Returns the foreground window's title bar text, or None if it can't
    be read (non-Windows OS, no window focused, permissions issue, etc).
    Uses only ctypes + the standard library — no pywin32/extra installs.
    Pass an hwnd from get_foreground_hwnd() to describe a specific window;
    if omitted, looks up the current foreground window itself.

    NOTE: this reads whatever text Windows puts in the title bar of the
    currently focused window. For a browser that's normally the page
    title (e.g. "Facebook - Google Chrome"), not the address bar's URL —
    see get_active_window_url() below for that.
    """
    if os.name != 'nt':
        return None  # only meaningful on the actual Windows lab PCs
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = hwnd or user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return None
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return None


# Optional dependency — only needed for get_active_window_url() below. Not
# part of the Python standard library, so it must be installed separately
# on every lab PC: `pip install uiautomation`. If it isn't installed, the
# agent still runs fine and just reports window_title without page_url
# (server falls back to guessing the site from title text, same as before).
try:
    import uiautomation as _uia
except Exception:
    _uia = None

# ClassName of the address bar element. Chromium browsers (Chrome, Edge,
# Brave, Opera) all use 'OmniboxViewViews' internally — confirmed by
# inspecting the live control tree (its AutomationId turned out to be a
# session-dependent value like 'view_1012', not a stable identifier, so
# ClassName is what we match on instead). Firefox's address bar uses the
# AutomationId 'urlbar-input', which IS stable there.
_ADDRESS_BAR_CLASS_NAMES = ('OmniboxViewViews',)
_ADDRESS_BAR_AUTOMATION_IDS = ('urlbar-input',)


def get_active_window_url(hwnd=None):
    """Best-effort read of the given window's (or the foreground window's,
    if hwnd is omitted) address bar text via Windows UI Automation. Returns
    None for any non-browser window, if the `uiautomation` package isn't
    installed, or if the address bar element can't be found (browser UI
    changes, unsupported browser, etc) — callers must treat this as
    "unavailable", not an error.

    Always pass the SAME hwnd used for get_active_window_title() in the
    same sampling cycle — see get_foreground_hwnd()'s docstring for why
    that matters.

    NOTE: requires `pip install uiautomation` on the lab PC (see module
    docstring). This is the only extra, non-standard-library dependency
    this agent uses, and only for this one feature — everything else
    still runs with zero installs.
    """
    if os.name != 'nt' or _uia is None:
        return None
    try:
        hwnd = hwnd or get_foreground_hwnd()
        if not hwnd:
            return None
        window = _uia.ControlFromHandle(hwnd)
        if window is None:
            return None
        for class_name in _ADDRESS_BAR_CLASS_NAMES:
            bar = window.EditControl(ClassName=class_name, searchDepth=20)
            if bar.Exists(1.5, 0.3):
                value = bar.GetValuePattern().Value if bar.GetValuePattern() else bar.Name
                return (value or '').strip() or None
        for automation_id in _ADDRESS_BAR_AUTOMATION_IDS:
            bar = window.EditControl(AutomationId=automation_id, searchDepth=20)
            if bar.Exists(1.5, 0.3):
                value = bar.GetValuePattern().Value if bar.GetValuePattern() else bar.Name
                return (value or '').strip() or None
        return None
    except Exception:
        return None


def report_activity_in_background(server_url, secret, window_title, page_url=''):
    """Fire-and-forget POST of one active-window-title sample. Same pattern
    as submit_logout_in_background — never blocks the UI thread, and a
    failed/slow request here just means one missed sample, not a problem
    worth surfacing to the student."""
    def worker():
        url = server_url.rstrip('/') + '/labs/api/pc-agent-activity/'
        payload = json.dumps({
            'secret': secret,
            'window_title': window_title or '',
            'page_url': page_url or '',
        }).encode('utf-8')
        request = urllib.request.Request(
            url, data=payload, headers={'Content-Type': 'application/json'}, method='POST',
        )
        try:
            urllib.request.urlopen(request, timeout=5).close()
        except Exception:
            pass  # best-effort; next interval will just try again

    threading.Thread(target=worker, daemon=True).start()


class StatusBar:
    """Small floating widget shown only while the PC is unlocked — a
    reminder of when the reservation ends, plus a manual 'Log Out' button
    for when the student finishes early. Sits in a corner, stays on top,
    but is deliberately unobtrusive (no fullscreen, no input blocking)."""

    def __init__(self, root, end_dt, on_logout):
        self.root = root
        self.end_dt = end_dt
        self.on_logout = on_logout
        self._tick_id = None

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg='#0a1522')

        w, h = 300, 64
        x = self.win.winfo_screenwidth() - w - 24
        y = self.win.winfo_screenheight() - h - 24
        self.win.geometry(f'{w}x{h}+{x}+{y}')

        border = tk.Frame(self.win, bg='#22d3f5', bd=0)
        border.pack(fill='both', expand=True, padx=1, pady=1)
        inner = tk.Frame(border, bg='#0a1522')
        inner.pack(fill='both', expand=True, padx=1, pady=1)

        left = tk.Frame(inner, bg='#0a1522')
        left.pack(side='left', fill='both', expand=True, padx=(14, 6))
        tk.Label(left, text='SESSION ACTIVE', font=('Consolas', 9, 'bold'), fg='#5b7a94', bg='#0a1522').pack(anchor='w', pady=(10, 0))
        self.countdown_label = tk.Label(left, text='', font=('Consolas', 13, 'bold'), fg='#22d3f5', bg='#0a1522')
        self.countdown_label.pack(anchor='w')

        logout_btn = tk.Button(
            inner, text='LOG OUT', font=('Consolas', 10, 'bold'),
            bg='#ff3b3b', fg='#2b0007', activebackground='#ff6b6b', activeforeground='#2b0007',
            relief='flat', bd=0, cursor='hand2', command=self._logout_clicked,
        )
        logout_btn.pack(side='right', fill='y', padx=(6, 0))

        self._tick()

    def _logout_clicked(self):
        self.on_logout()

    def _tick(self):
        remaining = self.end_dt - datetime.datetime.now()
        if remaining.total_seconds() <= 0:
            self.countdown_label.config(text='Ending now...')
        else:
            total = int(remaining.total_seconds())
            mins, secs = divmod(total, 60)
            hrs, mins = divmod(mins, 60)
            if hrs:
                text = f'{hrs}h {mins}m left'
            elif mins:
                text = f'{mins}m {secs}s left'
            else:
                text = f'{secs}s left'
            self.countdown_label.config(text=text)
        self._tick_id = self.root.after(1000, self._tick)

    def destroy(self):
        if self._tick_id is not None:
            try:
                self.root.after_cancel(self._tick_id)
            except Exception:
                pass
        try:
            self.win.destroy()
        except Exception:
            pass


class MiniPanel:
    """
    Small always-on-top floating widget shown ONLY while the PC is
    unlocked. Gives the student a way to end their session early (before
    the reservation's end_time) without having to find a Start Menu /
    taskbar shortcut — there isn't one, since the desktop is otherwise a
    completely normal Windows session.

    Draggable (via the top strip) and minimizable (via the "–" button, which
    collapses it down to just that strip so it stays out of the way without
    fully hiding — click "+" to expand it again).
    """

    NORMAL_ACCENT = '#22d3f5'
    WARNING_ACCENT = '#ffb020'
    W = 260
    HEADER_H = 26
    BODY_H = 66  # fallback only — actual height is computed from real content in __init__

    def __init__(self, on_lock_now):
        self.win = tk.Toplevel()
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg=self.NORMAL_ACCENT)
        screen_w = self.win.winfo_screenwidth()
        self._x = screen_w - self.W - 16
        self._y = 16
        self.win.geometry(f'{self.W}x{self.HEADER_H + self.BODY_H}+{self._x}+{self._y}')

        self.border = tk.Frame(self.win, bg=self.NORMAL_ACCENT, bd=0)
        self.border.pack(fill='both', expand=True, padx=1, pady=1)
        content = tk.Frame(self.border, bg='#0a1522')
        content.pack(fill='both', expand=True, padx=1, pady=1)

        # --- Drag handle / header strip ---------------------------------
        self.header = tk.Frame(content, bg='#0a1522', cursor='fleur')
        self.header.pack(fill='x', side='top')

        self.header_label = tk.Label(
            self.header, text='🔒  SESSION', font=('Consolas', 9, 'bold'),
            fg=self.NORMAL_ACCENT, bg='#0a1522', cursor='fleur',
        )
        self.header_label.pack(side='left', padx=(8, 0), pady=4)

        self.minimize_btn = tk.Button(
            self.header, text='–', font=('Consolas', 10, 'bold'), width=2,
            bg='#0a1522', fg='#5b7a94', activebackground='#173049', activeforeground='#eaf6ff',
            relief='flat', bd=0, cursor='hand2', command=self.toggle_minimize,
        )
        self.minimize_btn.pack(side='right', padx=(0, 4), pady=2)

        # Dragging binds to the header strip so it never fights with the
        # "Lock Now" button's own click handler below.
        for widget in (self.header, self.header_label):
            widget.bind('<ButtonPress-1>', self._start_drag)
            widget.bind('<B1-Motion>', self._on_drag)

        # --- Collapsible body ---------------------------------------------
        self.body = tk.Frame(content, bg='#0a1522')
        self.body.pack(fill='both', expand=True, side='top')

        self.status_text = tk.Label(
            self.body, text='SESSION ACTIVE', font=('Consolas', 10, 'bold'),
            fg=self.NORMAL_ACCENT, bg='#0a1522',
        )
        self.status_text.pack(pady=(6, 2))

        self.countdown_text = tk.Label(
            self.body, text='', font=('Consolas', 9), fg='#5b7a94', bg='#0a1522',
        )
        self.countdown_text.pack()

        btn = tk.Button(
            self.body, text='🔒 Lock Now', font=('Segoe UI', 9, 'bold'),
            bg='#173049', fg='#eaf6ff', activebackground='#20496b', activeforeground='#eaf6ff',
            relief='flat', cursor='hand2', bd=0, command=on_lock_now,
        )
        btn.pack(pady=(6, 8), ipady=3, ipadx=6)

        # The BODY_H constant above is only a rough guess — real widget
        # sizes depend on the actual fonts/DPI of the machine this runs on,
        # and if the guess is too small the window clips its own content
        # (the Lock Now button was getting cut off below the window's
        # bottom edge on some machines). Measure the real required size
        # after everything is packed and resize to match, with a small
        # safety margin.
        self.win.update_idletasks()
        real_header_h = self.header.winfo_reqheight()
        real_body_h = self.body.winfo_reqheight() + 4
        self.HEADER_H = real_header_h
        self.BODY_H = real_body_h
        self.win.geometry(f'{self.W}x{real_header_h + real_body_h}+{self._x}+{self._y}')

        self._minimized = False
        self.win.withdraw()

    def _start_drag(self, event):
        self._drag_offset = (event.x, event.y)

    def _on_drag(self, event):
        dx, dy = self._drag_offset
        self._x = self.win.winfo_x() + (event.x - dx)
        self._y = self.win.winfo_y() + (event.y - dy)
        self.win.geometry(f'+{self._x}+{self._y}')

    def toggle_minimize(self):
        if self._minimized:
            self.body.pack(fill='both', expand=True, side='top')
            self.minimize_btn.config(text='–')
            self.win.geometry(f'{self.W}x{self.HEADER_H + self.BODY_H}+{self._x}+{self._y}')
        else:
            self.body.pack_forget()
            self.minimize_btn.config(text='+')
            self.win.geometry(f'{self.W}x{self.HEADER_H}+{self._x}+{self._y}')
        self._minimized = not self._minimized

    def show(self):
        self.win.deiconify()
        self.win.attributes('-topmost', True)

    def hide(self):
        self.win.withdraw()

    def set_countdown(self, text):
        self.countdown_text.config(text=text)

    def set_warning(self, is_warning):
        accent = self.WARNING_ACCENT if is_warning else self.NORMAL_ACCENT
        self.win.configure(bg=accent)
        self.border.configure(bg=accent)
        self.header_label.configure(fg=accent)
        self.status_text.configure(
            fg=accent,
            text='ENDING SOON' if is_warning else 'SESSION ACTIVE',
        )


class WarningBanner:
    """
    Prominent centered "SYSTEM WARNING" banner (black background, red
    warning-triangle icon) that appears 2–3 minutes before a reservation's
    end_time, so the student notices even if they never look at the small
    corner MiniPanel. Has its own 'OK' button so the student can dismiss
    it early if it's blocking their work — dismissing it does NOT cancel
    the countdown, it just hides this banner (the MiniPanel still shows
    the countdown, and the banner will still be force-hidden automatically
    once the session actually ends).
    """

    RED = '#ff3b3b'

    def __init__(self):
        self.win = tk.Toplevel()
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        w, h = 460, 130  # fallback only — real height is measured below
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()
        self.win.geometry(f'{w}x{h}+{(screen_w - w) // 2}+{(screen_h - h) // 2 - 160}')

        self.win.configure(bg=self.RED)
        inner = tk.Frame(self.win, bg='#000000')
        inner.pack(fill='both', expand=True, padx=1, pady=1)

        # Red warning-triangle icon, drawn to match the provided design.
        icon = tk.Canvas(inner, width=64, height=54, bg='#000000', highlightthickness=0)
        icon.pack(pady=(14, 2))
        icon.create_polygon(32, 4, 60, 50, 4, 50, outline=self.RED, fill='#000000', width=3, joinstyle='round')
        icon.create_line(32, 20, 32, 36, fill=self.RED, width=4, capstyle='round')
        icon.create_oval(29, 41, 35, 47, fill=self.RED, outline=self.RED)

        self.title_label = tk.Label(
            inner, text='SYSTEM WARNING', font=('Consolas', 14, 'bold'),
            fg=self.RED, bg='#000000',
        )
        self.title_label.pack(pady=(0, 2))

        self.detail_label = tk.Label(
            inner, text='', font=('Segoe UI', 9), fg='#e0a0a0', bg='#000000',
            justify='center', wraplength=w - 40,
        )
        self.detail_label.pack()

        ok_btn = tk.Button(
            inner, text='OK', font=('Consolas', 9, 'bold'), width=8,
            bg='#1a0000', fg=self.RED, activebackground='#3a0000', activeforeground=self.RED,
            relief='flat', bd=0, cursor='hand2', command=self.hide,
        )
        ok_btn.pack(pady=(8, 10))

        # Same fix as MiniPanel: the 130px guess above doesn't account for
        # the detail_label wrapping to 2 lines, or for font/DPI differences
        # on the actual lab PC — either of which pushed the OK button below
        # the window's fixed bottom edge, clipping it off. Measure the real
        # required height once everything is packed and resize to fit.
        self.win.update_idletasks()
        real_h = inner.winfo_reqheight() + 4
        self.win.geometry(f'{w}x{real_h}+{(screen_w - w) // 2}+{(screen_h - real_h) // 2 - 160}')

        self.win.withdraw()

    def show(self):
        self.win.deiconify()
        self.win.attributes('-topmost', True)

    def hide(self):
        self.win.withdraw()

    def set_detail(self, text):
        self.detail_label.config(text=text)
        # Re-measure: a longer message can wrap to more lines than the one
        # the window was originally sized for, which would clip the OK
        # button again if we didn't resize here too.
        self.win.update_idletasks()
        w = self.win.winfo_width()
        screen_w = self.win.winfo_screenwidth()
        screen_h = self.win.winfo_screenheight()
        real_h = self.win.winfo_reqheight()
        self.win.geometry(f'{w}x{real_h}+{(screen_w - w) // 2}+{(screen_h - real_h) // 2 - 160}')


class LockScreen:
    """Fullscreen overlay window that blocks the desktop until unlocked.

    Includes a built-in login form (ID Number + Reservation Code) so
    students can check in directly on the locked PC — no separate
    reception/kiosk computer required. Styled per the approved lock-screen
    design: top header bar with the college name and a live clock/date,
    chamfered-corner card with a glowing padlock badge, "ACCESS REQUIRED"
    heading, icon-prefixed input fields, a gold "UNLOCK COMPUTER" button, a
    COMPUTER STATUS / NETWORK / LABORATORY status row, and Restart /
    Power Off controls under it.

    Also owns the session timer: once a login succeeds, it schedules an
    automatic re-lock for when that reservation's end_time is reached, and
    shows a small MiniPanel with a "Lock Now" button for ending early.
    """

    BG = '#050b14'
    GRID = '#0a1826'
    CIRCUIT = '#0f2436'
    CIRCUIT_NODE = '#1c4d5e'
    CARD_BG = '#0a1522'
    FIELD_BG = '#0d1e2e'
    FIELD_BORDER = '#173049'
    ACCENT = '#22d3f5'
    ACCENT_BRIGHT = '#7fe8ff'
    ACCENT_DIM = '#0e5a72'
    # Gold/amber accent — used for the PC-name line, the unlock button, and
    # other "call to action" HUD accents, matching the approved lock-screen
    # design (screenshot). Cyan (ACCENT above) is kept for the padlock glow.
    GOLD = '#f5a623'
    GOLD_BRIGHT = '#ffc960'
    GOLD_DIM = '#8a5a10'
    STATUS_GREEN = '#3ddc84'
    TEXT = '#eaf6ff'
    MUTED = '#5b7a94'
    ERROR = '#ff5470'
    SUCCESS = '#39ff8f'
    FONT_MONO = 'Consolas'

    CARD_W = 400
    # Tall enough to fit every element stacked inside the card (padlock
    # badge, heading, form, status row, power buttons, footer) without the
    # inner content overflowing its frame. It previously overflowed by a
    # small amount, and since the content is vertically centered inside a
    # fixed-height frame, that overflow was clipped equally off the TOP and
    # bottom — cutting a few pixels off the padlock badge above "ACCESS
    # REQUIRED" being the most visible symptom. Comfortable headroom now.
    CARD_H = 620
    WARNING_LEAD_SECONDS = 150  # show the "ending soon" warning 2.5 min before end_time

    def __init__(self, root, config):
        self.root = root
        self.config = config
        self.visible = False
        self.submitting = False
        self._pulse_on = True
        self._relock_after_id = None
        self._countdown_after_id = None
        self._warning_after_id = None
        self._warning_shown = False
        self._session_end_dt = None
        self._override_after_id = None
        self._override_tick_after_id = None
        self.mini_panel = None  # created after root exists; see main()
        self.warning_banner = None  # created after root exists; see main()
        self.status_bar = None
        self.relock_after_id = None
        self._activity_stop_event = threading.Event()
        self._activity_thread = None
        self._icons = {}  # keeps PhotoImage refs alive — Tk garbage-collects them otherwise

        root.title('CompuLab')
        try:
            small_logo = tk.PhotoImage(file=os.path.join(
                os.path.dirname(os.path.abspath(__file__)), 'assets', 'icon_college_logo_small.png'))
            root.iconphoto(True, small_logo)
            self._icons['_taskbar_logo_ref'] = small_logo  # keep alive
        except Exception:
            pass
        root.configure(bg=self.BG)
        root.overrideredirect(True)  # no title bar / close button
        # NOTE: we deliberately do NOT use attributes('-fullscreen', True)
        # together with overrideredirect(True) — on some Tk/Windows builds
        # combining both raises "can't set fullscreen attribute: override-
        # redirect flag is set". Sizing the window to the screen manually
        # gets the same visual result without that conflict.
        self.screen_w = root.winfo_screenwidth()
        self.screen_h = root.winfo_screenheight()
        root.geometry(f'{self.screen_w}x{self.screen_h}+0+0')
        root.attributes('-topmost', True)

        # Swallow Alt+F4 so a casual attempt to close the window doesn't work.
        root.protocol('WM_DELETE_WINDOW', lambda: None)
        root.bind('<Alt-F4>', lambda e: 'break')

        self._load_icons()

        self.canvas = tk.Canvas(root, bg=self.BG, highlightthickness=0, width=self.screen_w, height=self.screen_h)
        self.canvas.place(x=0, y=0)
        self._draw_background()
        self._draw_card_border()

        cx, cy = self.screen_w // 2, self.screen_h // 2

        # --- Logo + college name (top-left) + live clock/date with icons
        # and a divider (top-right) — drawn directly on the canvas (no
        # background bar/box) so they float over the photo, matching the
        # approved design. ---
        logo_small = self._icons.get('college_logo_small')
        logo_w = 0
        header_cy = 34  # vertical center of the header row — logo + both text lines align to this
        if logo_small is not None:
            self.canvas.create_image(28, header_cy, anchor='w', image=logo_small)
            logo_w = logo_small.width()

        college_name = config.get('college_name', 'Danao Technological College')
        college_subtitle = config.get('college_subtitle', 'Computer Laboratory System')
        text_x = 28 + logo_w + 14
        self.canvas.create_text(
            text_x, header_cy - 11, anchor='w', text=college_name.upper(),
            font=('Segoe UI', 15, 'bold'), fill=self.TEXT,
        )
        self.canvas.create_text(
            text_x, header_cy + 11, anchor='w', text=college_subtitle,
            font=('Segoe UI', 10), fill=self.MUTED,
        )

        self._header_right_y = header_cy - 6
        self._draw_header_right('', '', '')
        self._tick_clock()

        frame = tk.Frame(root, bg=self.CARD_BG)
        frame.place(x=cx, y=cy, anchor='center', width=self.CARD_W - 44, height=self.CARD_H - 44)

        inner = tk.Frame(frame, bg=self.CARD_BG)
        inner.place(relx=0.5, rely=0.5, anchor='center')

        # --- Header: glowing padlock badge -------------------------------
        badge_canvas = tk.Canvas(inner, width=80, height=80, bg=self.CARD_BG, highlightthickness=0)
        badge_canvas.pack(pady=(0, 3))
        self._draw_glow_rings(badge_canvas, 40, 40, max_r=34)
        badge_canvas.create_image(40, 40, image=self._icons['lock_badge'])

        # "• COMPUTER LABORATORY •" — static eyebrow label.
        self.status_label_hud = tk.Label(
            inner, text='•   C O M P U T E R   L A B O R A T O R Y   •', font=(self.FONT_MONO, 9, 'bold'),
            fg=self.MUTED, bg=self.CARD_BG,
        )
        self.status_label_hud.pack(pady=(0, 3))
        self._pulse_dot()

        # Registered PC name. Starts out from agent_config.json ("pc_name")
        # purely as a fallback for the brief moment before the server
        # answers (or if it can't be reached at all) — as soon as
        # fetch_pc_info_in_background() gets a reply, apply_pc_info() below
        # overwrites this with the real pc_id assigned to this machine in
        # the CompuLab admin panel (Manage PCs -> Add/Edit), so the two can
        # never drift out of sync.
        self.pc_name = (config.get('pc_name') or 'PC').strip()
        self.pc_name_label = tk.Label(
            inner, text=f'—  {self.pc_name}  —', font=(self.FONT_MONO, 10, 'bold'),
            fg=self.GOLD, bg=self.CARD_BG,
        )
        self.pc_name_label.pack(pady=(0, 6))

        # Big heading: an explicit lock_title always wins; otherwise this is
        # the fixed "ACCESS REQUIRED" heading from the approved design.
        title_text = config.get('lock_title') or 'ACCESS REQUIRED'
        tk.Label(
            inner, text=title_text, font=('Segoe UI', 19, 'bold'),
            fg=self.TEXT, bg=self.CARD_BG, wraplength=self.CARD_W - 60, justify='center',
        ).pack(pady=(0, 6))

        tk.Label(
            inner, text=config.get('lock_subtitle', 'Enter your Student ID and Reservation Code to use this PC.'),
            font=('Segoe UI', 9), fg=self.MUTED, bg=self.CARD_BG, wraplength=self.CARD_W - 80, justify='center',
        ).pack(pady=(0, 12))

        form = tk.Frame(inner, bg=self.CARD_BG)
        form.pack()

        field_w = self.CARD_W - 44 - 70
        id_box, self.id_entry = self._build_field(form, 'user', 'Student / Instructor ID')
        id_box.grid(row=0, column=0, sticky='we', pady=(0, 7))

        code_box, self.code_entry = self._build_field(form, 'lock', 'Reservation Code')
        code_box.grid(row=1, column=0, sticky='we', pady=(0, 10))

        form.grid_columnconfigure(0, weight=1)

        self.submit_canvas, self._submit_text_id = self._build_unlock_button(form, width=field_w, height=38)
        self.submit_canvas.grid(row=2, column=0, sticky='we')

        self.status_label = tk.Label(
            inner, text='', font=(self.FONT_MONO, 9), fg=self.ERROR, bg=self.CARD_BG, wraplength=field_w, justify='center',
        )
        self.status_label.pack(pady=(8, 0))

        # --- Status row: STATUS / NETWORK / LABORATORY ------------------
        # Wider than field_w (which is narrowed for the input-field icon
        # gutter) — the status row has no icon gutter, so it can use most
        # of the card's inner width. This plus the shorter "STATUS" label
        # (was "COMPUTER STATUS", which overflowed its column at the
        # larger, more-readable font size) keeps all three columns from
        # clipping against the card edge.
        status_w = self.CARD_W - 60
        status_sep = tk.Canvas(inner, width=status_w, height=1, bg=self.CARD_BG, highlightthickness=0)
        status_sep.pack(pady=(10, 10))
        status_sep.create_line(0, 0, status_w, 0, fill=self.FIELD_BORDER)

        status_row = tk.Frame(inner, bg=self.CARD_BG, width=status_w, height=50)
        status_row.pack()
        status_row.grid_propagate(False)
        for i in range(3):
            status_row.grid_columnconfigure(i, weight=1, uniform='status_col')
        self.computer_status_value = self._build_status_col(
            status_row, 'computer_status', 'STATUS', 'Ready for Login', 0,
        )
        self.network_status_value = self._build_status_col(
            status_row, 'network_status', 'NETWORK', 'Connected', 1,
        )
        self.lab_status_value = self._build_status_col(
            status_row, 'lab_status', 'LABORATORY', config.get('lab_name', 'Computer Lab 1'), 2,
        )

        # --- Restart / Power off, centered under the status row ---------
        power_row = tk.Frame(inner, bg=self.CARD_BG)
        power_row.pack(pady=(12, 0))

        def _power_btn(parent, icon, text, command):
            btn = tk.Button(
                parent, text=f'{icon}  {text}', font=(self.FONT_MONO, 9, 'bold'),
                bg=self.FIELD_BG, fg=self.TEXT, activebackground='#16283b', activeforeground=self.TEXT,
                relief='flat', bd=0, cursor='hand2', padx=13, pady=7, command=command,
                highlightthickness=1, highlightbackground=self.FIELD_BORDER,
            )
            return btn

        _power_btn(power_row, '⟳', 'RESTART', self.restart_clicked).pack(side='left', padx=(0, 8))
        _power_btn(power_row, '⏻', 'POWER OFF', self.power_off_clicked).pack(side='left')

        # --- Footer: authorized-use disclaimer --------------------------
        footer_sep = tk.Canvas(inner, width=field_w, height=1, bg=self.CARD_BG, highlightthickness=0)
        footer_sep.pack(pady=(12, 8))
        footer_sep.create_line(0, 0, field_w, 0, fill=self.FIELD_BORDER)

        footer = tk.Frame(inner, bg=self.CARD_BG)
        footer.pack()
        tk.Label(footer, image=self._icons['shield'], bg=self.CARD_BG).pack(side='left', padx=(0, 6))
        tk.Label(
            footer, justify='center', bg=self.CARD_BG, fg=self.MUTED, font=('Segoe UI', 7),
            text='Authorized users only. Unauthorized access is prohibited.',
        ).pack(side='left')

        self.id_entry.bind('<Return>', lambda e: self.code_entry.focus_set())
        self.code_entry.bind('<Return>', lambda e: self.on_submit())

        self._frame = frame
        self.show()

    # Size (px) of the round header logo — bumped up from the old 32x32
    # square icon so it reads clearly next to the bigger college name text.
    HEADER_LOGO_SIZE = 52

    def _make_circular_logo(self, path, size):
        """Loads the higher-res college seal, resizes it, and crops it into
        a circle (masking to the *intersection* of its own alpha and a
        circle, so it works whether the source already has a transparent
        background or an opaque square one). The mask is drawn at 4x scale
        and downsampled with LANCZOS so the circle edge is smooth/anti-
        aliased instead of blocky. Returns None if Pillow isn't available
        or the file can't be read — caller falls back to the plain square
        icon in that case."""
        if not _PIL_AVAILABLE:
            return None
        try:
            img = Image.open(path).convert('RGBA')
            img = img.resize((size, size), Image.LANCZOS)
            supersample = 4
            big = size * supersample
            circle_mask_big = Image.new('L', (big, big), 0)
            ImageDraw.Draw(circle_mask_big).ellipse((0, 0, big, big), fill=255)
            circle_mask = circle_mask_big.resize((size, size), Image.LANCZOS)
            combined_alpha = ImageChops.darker(img.split()[-1], circle_mask)
            img.putalpha(combined_alpha)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    # Fixed pixel size for the small icons inside the ID/code input fields.
    # icon_user.png (28x28) and icon_lock.png (24x24) come in at different
    # native sizes — resizing both to the same fixed size keeps them
    # visually consistent AND guarantees they're small enough to fit inside
    # the compact field height with room to spare (no clipping/overflow).
    FIELD_ICON_SIZE = 16

    def _resize_icon(self, path, size):
        """Resizes a PNG icon to a fixed size with anti-aliasing. Returns
        None if Pillow isn't available or the file can't be read — caller
        falls back to the icon's native/original size in that case."""
        if not _PIL_AVAILABLE:
            return None
        try:
            img = Image.open(path).convert('RGBA')
            img = img.resize((size, size), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    def _load_icons(self):
        assets_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
        names = {
            'lock_badge': 'icon_lock_badge_sm.png',
            'user': 'icon_user.png',
            'lock': 'icon_lock.png',
            'lock_dark': 'icon_lock_dark.png',
            'shield': 'icon_shield.png',
            'college_logo': 'icon_college_logo.png',
        }
        for key, filename in names.items():
            path = os.path.join(assets_dir, filename)
            try:
                self._icons[key] = tk.PhotoImage(file=path)
            except Exception:
                self._icons[key] = None  # missing/corrupt asset shouldn't crash the lock screen

        # Field icons ('user' / 'lock'): resize both to the same fixed,
        # small size so they fit cleanly inside the compact input fields
        # regardless of their differing native sizes. Falls back to the
        # native-size icon already loaded above if Pillow isn't installed.
        for key, filename in (('user', 'icon_user.png'), ('lock', 'icon_lock.png')):
            resized = self._resize_icon(os.path.join(assets_dir, filename), self.FIELD_ICON_SIZE)
            if resized is not None:
                self._icons[key] = resized

        # Header logo: bigger + round. Built from the full-res
        # icon_college_logo.png (not the small square one) so it stays
        # sharp at the larger size; falls back to the old square small
        # icon if Pillow isn't installed.
        big_logo_path = os.path.join(assets_dir, 'icon_college_logo.png')
        circular = self._make_circular_logo(big_logo_path, self.HEADER_LOGO_SIZE)
        if circular is not None:
            self._icons['college_logo_small'] = circular
        else:
            try:
                self._icons['college_logo_small'] = tk.PhotoImage(
                    file=os.path.join(assets_dir, 'icon_college_logo_small.png'))
            except Exception:
                self._icons['college_logo_small'] = None

    def _draw_glow_rings(self, canvas, cx, cy, max_r=50):
        """Soft concentric rings behind the padlock badge — Tkinter has no
        real blur, so this fakes a glow by fading the ring color toward the
        card background as the radius grows."""
        radii = [max_r, max_r * 0.84, max_r * 0.68]
        for r in radii:
            t = (max_r - r) / max_r  # 0 at outer ring, 1 near the badge
            color = self._lerp_color(self.ACCENT_DIM, self.CARD_BG, 1 - (t * 0.4 + 0.15))
            canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=1)

    # Fixed width for the icon area inside each input field — icon_user.png
    # (28x28) and icon_lock.png (24x24) are different sizes, so without a
    # fixed-width holder the entry text starts a few px later in one field
    # than the other. Pinning both holders to the same width keeps the two
    # fields' text aligned to the same left edge.
    FIELD_ICON_HOLDER_W = 30

    # Fixed overall height for each input field box. Deliberately locked
    # (via box.pack_propagate(False) below) instead of letting the box
    # auto-size to its children — that auto-sizing is what let the field
    # shrink smaller than the icon at one point, clipping it. This value
    # comfortably fits FIELD_ICON_SIZE (16px) plus the entry's text with
    # margin on all sides.
    FIELD_BOX_H = 34

    def _build_field(self, parent, icon_key, placeholder, show=None):
        """One icon-prefixed input box with simulated placeholder text
        (Tkinter Entry has no native placeholder support)."""
        box = tk.Frame(parent, bg=self.FIELD_BG, highlightthickness=1,
                        highlightbackground=self.FIELD_BORDER, highlightcolor=self.ACCENT,
                        height=self.FIELD_BOX_H)
        box.pack_propagate(False)
        icon_holder = tk.Frame(box, bg=self.FIELD_BG, width=self.FIELD_ICON_HOLDER_W)
        icon_holder.pack(side='left', fill='y', padx=(8, 0))
        icon_holder.pack_propagate(False)
        icon_img = self._icons.get(icon_key)
        if icon_img is not None:
            tk.Label(icon_holder, image=icon_img, bg=self.FIELD_BG).place(relx=0.5, rely=0.5, anchor='center')
        entry = tk.Entry(
            box, font=(self.FONT_MONO, 11), bg=self.FIELD_BG, fg=self.MUTED,
            insertbackground=self.ACCENT, relief='flat', bd=0, show=show,
        )
        entry.pack(side='left', fill='both', expand=True, padx=(0, 12), pady=5)
        entry.insert(0, placeholder)
        entry._placeholder = placeholder
        entry._is_placeholder = True
        entry._real_show = show

        def on_focus_in(_e):
            if entry._is_placeholder:
                entry.delete(0, 'end')
                entry.config(fg=self.TEXT, show=entry._real_show or '')
                entry._is_placeholder = False
            box.config(highlightbackground=self.ACCENT, highlightthickness=2)

        def on_focus_out(_e):
            if not entry.get():
                entry.config(show='')
                entry.insert(0, entry._placeholder)
                entry.config(fg=self.MUTED)
                entry._is_placeholder = True
            box.config(highlightbackground=self.FIELD_BORDER, highlightthickness=1)

        entry.bind('<FocusIn>', on_focus_in)
        entry.bind('<FocusOut>', on_focus_out)
        return box, entry

    def _build_status_col(self, parent, key, label, value, col_index):
        """One column of the status row: a small dot + label on top, the
        current value (green when healthy) underneath, gridded into
        column `col_index` of an equal-width 3-column row. Returns the
        value Label so callers can update it later (e.g. COMPUTER STATUS
        flipping between 'Ready for Login' and 'Verifying...')."""
        col = tk.Frame(parent, bg=self.CARD_BG)
        col.grid(row=0, column=col_index, sticky='nsew')

        label_row = tk.Frame(col, bg=self.CARD_BG)
        label_row.pack(anchor='center', pady=(0, 4))
        dot = tk.Canvas(label_row, width=8, height=8, bg=self.CARD_BG, highlightthickness=0)
        dot.pack(side='left', padx=(0, 4))
        dot.create_oval(1, 1, 7, 7, fill=self.STATUS_GREEN, outline='')
        tk.Label(
            label_row, text=label, font=('Segoe UI', 8, 'bold'), fg=self.TEXT, bg=self.CARD_BG,
        ).pack(side='left')

        value_label = tk.Label(
            col, text=value, font=('Segoe UI', 9, 'bold'), fg=self.STATUS_GREEN, bg=self.CARD_BG,
            wraplength=110, justify='center',
        )
        value_label.pack(anchor='center')
        return value_label

    @staticmethod
    def field_value(entry):
        """Real typed value, or '' if the field still shows its placeholder."""
        return '' if getattr(entry, '_is_placeholder', False) else entry.get().strip()

    @staticmethod
    def _lerp_color(c1, c2, t):
        c1, c2 = c1.lstrip('#'), c2.lstrip('#')
        r1, g1, b1 = int(c1[0:2], 16), int(c1[2:4], 16), int(c1[4:6], 16)
        r2, g2, b2 = int(c2[0:2], 16), int(c2[2:4], 16), int(c2[4:6], 16)
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return f'#{r:02x}{g:02x}{b:02x}'

    def _build_unlock_button(self, parent, width, height=52):
        canvas = tk.Canvas(parent, width=width, height=height, bg=self.CARD_BG, highlightthickness=0, cursor='hand2')
        text_id = self._paint_button(canvas, width, height, self.GOLD_DIM, self.GOLD, 'UNLOCK COMPUTER')
        canvas.bind('<Enter>', lambda e: None if self.submitting else self._paint_button(canvas, width, height, self.GOLD, self.GOLD_BRIGHT, 'UNLOCK COMPUTER'))
        canvas.bind('<Leave>', lambda e: None if self.submitting else self._paint_button(canvas, width, height, self.GOLD_DIM, self.GOLD, 'UNLOCK COMPUTER'))
        canvas.bind('<Button-1>', lambda e: self.on_submit())
        return canvas, text_id

    def _paint_button(self, canvas, width, height, left_color, right_color, label):
        canvas.delete('all')
        step = 3
        for x in range(0, width, step):
            t = x / width
            canvas.create_rectangle(x, 0, x + step, height, fill=self._lerp_color(left_color, right_color, t), outline='')
        icon_img = self._icons.get('lock_dark')
        font = ('Segoe UI', 10, 'bold')
        # Measure the label's real rendered width instead of guessing from
        # its character count (len(label) * 2.9) — that guess didn't match
        # actual font metrics on some machines, which made the icon and
        # text overlap. Lay both out as one centered group: icon, a fixed
        # gap, then text, using the real widths so they never collide.
        text_id = canvas.create_text(0, height / 2, anchor='w', text=label, font=font, fill='#241703')
        text_w = canvas.bbox(text_id)[2] - canvas.bbox(text_id)[0]
        icon_w = icon_img.width() if icon_img is not None else 0
        gap = 8 if icon_img is not None else 0
        group_w = icon_w + gap + text_w
        start_x = (width - group_w) / 2
        if icon_img is not None:
            canvas.create_image(start_x + icon_w / 2, height / 2, image=icon_img)
        canvas.coords(text_id, start_x + icon_w + gap, height / 2)
        return text_id

    def _draw_card_border(self):
        """Chamfered (cut-corner) HUD border around the login card, drawn
        as a polygon rather than a plain rectangle."""
        cx, cy = self.screen_w // 2, self.screen_h // 2
        left, right = cx - self.CARD_W // 2, cx + self.CARD_W // 2
        top, bottom = cy - self.CARD_H // 2, cy + self.CARD_H // 2
        c = 20  # chamfer size

        points = [
            left + c, top,       right - c, top,
            right, top + c,      right, bottom - c,
            right - c, bottom,   left + c, bottom,
            left, bottom - c,    left, top + c,
        ]
        self.canvas.create_polygon(points, outline=self.ACCENT, fill=self.CARD_BG, width=2, joinstyle='miter')

        # Small circuit-style tick marks jutting out from each chamfer —
        # echoes the traces in the background, like the card is "plugged in".
        tick = 14
        ticks = [
            (left + c, top, -1, -1), (right - c, top, 1, -1),
            (right, top + c, 1, -1), (right, bottom - c, 1, 1),
            (right - c, bottom, 1, 1), (left + c, bottom, -1, 1),
            (left, bottom - c, -1, 1), (left, top + c, -1, -1),
        ]
        for x, y, dx, dy in ticks:
            self.canvas.create_line(x, y, x + tick * dx, y + tick * dy, fill=self.ACCENT_DIM, width=1)

    def _draw_background(self):
        """Full-screen backdrop: the school building photo
        (assets/bg_building.jpg), darkened so the gold/cyan HUD text and
        the card stay readable over a bright daytime shot. Needs Pillow
        (`pip install Pillow`) to resize/darken the photo — falls back to
        the old procedural circuit-board backdrop if Pillow isn't
        installed or the photo file is missing, so the agent still runs
        with just the standard library if that extra step is skipped."""
        bg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', 'bg_building.jpg')
        if _PIL_AVAILABLE and os.path.exists(bg_path):
            try:
                img = Image.open(bg_path).convert('RGB')
                img = img.resize((self.screen_w, self.screen_h), Image.LANCZOS)
                img = ImageEnhance.Brightness(img).enhance(0.35)  # darken for legibility
                self._bg_photo = ImageTk.PhotoImage(img)  # keep a ref — Tk drops unreferenced images
                self.canvas.create_image(0, 0, anchor='nw', image=self._bg_photo)
                return
            except Exception as e:
                print(f'[agent] Could not load background photo: {e}')
        # No circuit backdrop fallback anymore — canvas is already filled
        # with self.BG, so this is just a plain dark background.

    def _draw_clock_icon(self, cx, cy, r=7, color=None):
        color = color or self.GOLD
        ids = [self.canvas.create_oval(cx - r, cy - r, cx + r, cy + r, outline=color, width=1.4, tags='hdr_right')]
        ids.append(self.canvas.create_line(cx, cy, cx, cy - r * 0.55, fill=color, width=1.4, tags='hdr_right'))
        ids.append(self.canvas.create_line(cx, cy, cx + r * 0.45, cy + r * 0.15, fill=color, width=1.4, tags='hdr_right'))
        return ids

    def _draw_calendar_icon(self, cx, cy, w=14, h=13, color=None):
        color = color or self.GOLD
        left, top = cx - w / 2, cy - h / 2
        right, bottom = cx + w / 2, cy + h / 2
        ids = [self.canvas.create_rectangle(left, top + 2, right, bottom, outline=color, width=1.2, tags='hdr_right')]
        ids.append(self.canvas.create_line(left, top + 5, right, top + 5, fill=color, width=1.2, tags='hdr_right'))
        ids.append(self.canvas.create_line(left + 3, top, left + 3, top + 4, fill=color, width=1.2, tags='hdr_right'))
        ids.append(self.canvas.create_line(right - 3, top, right - 3, top + 4, fill=color, width=1.2, tags='hdr_right'))
        return ids

    def _draw_header_right(self, time_str, date_str, day_str):
        """(Re)draws the top-right clock/date block: a small clock icon +
        bold time, a vertical divider, then a small calendar icon + the
        date (bold) with the weekday underneath (muted) — matching the
        approved lock-screen design. Cheap enough to fully redraw once a
        second in _tick_clock rather than trying to reposition items as
        the text width changes."""
        self.canvas.delete('hdr_right')
        right_edge = self.screen_w - 24
        y = self._header_right_y

        # Date block (rightmost): bold date on top, muted weekday under it.
        date_id = self.canvas.create_text(
            right_edge, y - 8, anchor='e', text=date_str,
            font=('Segoe UI', 10, 'bold'), fill=self.TEXT, tags='hdr_right',
        )
        self.canvas.create_text(
            right_edge, y + 8, anchor='e', text=day_str,
            font=('Segoe UI', 8), fill=self.MUTED, tags='hdr_right',
        )
        date_left = self.canvas.bbox(date_id)[0]

        cal_cx = date_left - 8 - 7
        self._draw_calendar_icon(cal_cx, y, color=self.GOLD)

        sep_x = cal_cx - 7 - 12
        self.canvas.create_line(sep_x, y - 14, sep_x, y + 14, fill=self.FIELD_BORDER, width=1, tags='hdr_right')

        time_id = self.canvas.create_text(
            sep_x - 12, y, anchor='e', text=time_str,
            font=(self.FONT_MONO, 15, 'bold'), fill=self.TEXT, tags='hdr_right',
        )
        time_left = self.canvas.bbox(time_id)[0]
        self._draw_clock_icon(time_left - 8 - 7, y, color=self.GOLD)

    def _tick_clock(self):
        now = datetime.datetime.now()
        time_str = now.strftime('%I:%M %p').lstrip('0')
        date_str = now.strftime('%B %d, %Y')
        day_str = now.strftime('%A')
        self._draw_header_right(time_str, date_str, day_str)
        self.root.after(1000, self._tick_clock)

    def _pulse_dot(self):
        self._pulse_on = not self._pulse_on
        color = self.ACCENT if self._pulse_on else self.MUTED
        self.status_label_hud.config(fg=color)
        self.root.after(650, self._pulse_dot)

    def restart_clicked(self):
        if not messagebox.askyesno(
            'Restart PC',
            'Restart this computer now?\n\nAny unsaved work on this PC will be lost.',
            parent=self.root,
        ):
            return
        try:
            subprocess.run(['shutdown', '/r', '/t', '0'], check=True)
        except Exception as e:
            print(f'[agent] Restart command failed: {e}')
            messagebox.showerror('Restart failed', f"Couldn't restart this PC: {e}", parent=self.root)

    def power_off_clicked(self):
        if not messagebox.askyesno(
            'Power Off PC',
            'Turn off this computer now?\n\nAny unsaved work on this PC will be lost.',
            parent=self.root,
        ):
            return
        try:
            subprocess.run(['shutdown', '/s', '/t', '0'], check=True)
        except Exception as e:
            print(f'[agent] Power off command failed: {e}')
            messagebox.showerror('Power off failed', f"Couldn't power off this PC: {e}", parent=self.root)

    def on_submit(self):
        if self.submitting:
            return
        id_number = self.field_value(self.id_entry)
        code = self.field_value(self.code_entry)

        if not id_number or not code:
            self.status_label.config(fg=self.ERROR, text='Please fill in both ID and Reservation Code.')
            return

        self.submitting = True
        self._paint_button(self.submit_canvas, self.submit_canvas.winfo_width(), 38, self.FIELD_BORDER, self.FIELD_BORDER, 'VERIFYING...')
        self.status_label.config(fg=self.MUTED, text='Confirming your reservation...')
        if getattr(self, 'computer_status_value', None) is not None:
            self.computer_status_value.config(text='Verifying...', fg=self.GOLD)

        submit_login_in_background(
            self.config.get('server_url', 'http://127.0.0.1:8000'),
            self.config.get('secret', ''),
            id_number,
            code,
        )

    def handle_login_result(self, result):
        self.submitting = False
        self._paint_button(self.submit_canvas, self.submit_canvas.winfo_width(), 38, self.GOLD_DIM, self.GOLD, 'UNLOCK COMPUTER')

        if result.get('ok'):
            self.status_label.config(fg=self.SUCCESS, text=f"Welcome, {result.get('student_name', '')}!")
            self.id_entry.delete(0, 'end')
            self.code_entry.delete(0, 'end')
            # Unlock either way — if unlock_success is False that only means
            # this agent's own local unlock step reported a problem, which
            # in practice can't really happen since hiding the screen IS
            # that step; still, don't leave the student stuck on a verified
            # login.
            self.unlock_for_session(result.get('session_end_time'), result.get('server_time'))
        else:
            self.status_label.config(fg=self.ERROR, text=result.get('error', 'Something went wrong, please try again.'))
            self.code_entry.delete(0, 'end')
            self.code_entry.focus_set()

    def unlock_for_session(self, session_end_time_str, server_time_str=None):
        """Hides the lock screen and, if we know when the reservation ends,
        schedules an automatic re-lock for that moment (plus a warning
        shortly before). Also brings up the MiniPanel with its 'Lock Now'
        button for ending the session early.

        `server_time_str` (HH:MM:SS, Asia/Manila) is the server's own clock
        at the moment it handled the login. We use it to compute this PC's
        clock offset/drift and schedule everything against the SERVER's
        idea of "now", not this lab PC's own (possibly wrong) system clock.
        Without this, a lab PC whose Windows clock is off by even a few
        minutes fires the warning/auto-relock at the wrong real-world
        moment — or seemingly "never", if the PC thinks it's earlier than
        it actually is.

        `session_end_time_str` can be None — this is the path used for a
        manual/remote unlock triggered from the CompuLab web control page
        (Lab In-Charge pressing "Unlock PC"), where there's no reservation
        tied to the unlock. In that case no auto-relock timer or warning
        banner is scheduled (there's no end time to count down to), but the
        MiniPanel with its "Lock Now" button still appears — the student is
        the one who decides when to end the session, exactly like the
        normal login flow, just without a countdown clock.
        """
        self.hide()  # login API call already updated server state
        self.start_activity_reporting()

        self._session_end_dt = None
        self._warning_shown = False
        self._clock_offset = datetime.timedelta(0)

        local_now = datetime.datetime.now()

        if server_time_str:
            try:
                server_time = datetime.datetime.strptime(server_time_str, '%H:%M:%S').time()
                server_now = datetime.datetime.combine(datetime.date.today(), server_time)
                # offset = how far ahead the server's clock is vs ours;
                # add this to our own datetime.now() readings from now on.
                self._clock_offset = server_now - local_now
            except ValueError:
                self._clock_offset = datetime.timedelta(0)

        if session_end_time_str:
            try:
                end_time = datetime.datetime.strptime(session_end_time_str, '%H:%M:%S').time()
                self._session_end_dt = datetime.datetime.combine(datetime.date.today(), end_time)
            except ValueError:
                self._session_end_dt = None

        if self.mini_panel:
            self.mini_panel.set_warning(False)
            self.mini_panel.show()
            self._tick_countdown()

        if self._session_end_dt:
            remaining = (self._session_end_dt - self._server_synced_now()).total_seconds()
            ms = max(int(remaining * 1000), 0)
            self._relock_after_id = self.root.after(ms, self._auto_relock)

            warning_delay = remaining - self.WARNING_LEAD_SECONDS
            if warning_delay > 0:
                self._warning_after_id = self.root.after(int(warning_delay * 1000), self._show_warning)
            else:
                # Less than the warning window remains right from login —
                # show the warning immediately instead of skipping it.
                self._show_warning()
        # else: no session_end_time known (manual/remote unlock) — no
        # auto-relock timer, no warning banner. MiniPanel's countdown label
        # just stays blank (see _tick_countdown below) until the student
        # presses "Lock Now" themselves, or the server sends a /lock signal.

    def start_activity_reporting(self):
        """Kicks off a background loop that samples the active window's
        title every `activity_report_interval_seconds` and reports it to
        the server, for as long as the PC stays unlocked. No-op if the
        interval is configured to 0/blank (feature disabled) or a loop is
        already running."""
        interval = self.config.get('activity_report_interval_seconds', 8)
        try:
            interval = float(interval)
        except (TypeError, ValueError):
            interval = 0
        if interval <= 0 or self._activity_thread is not None:
            return

        self._activity_stop_event.clear()
        server_url = self.config.get('server_url', 'http://127.0.0.1:8000')
        secret = self.config.get('secret', '')

        def loop():
            # UI Automation requires each thread that uses it to initialize
            # its own COM apartment first — this thread is not the main
            # thread, so without this, every get_active_window_url() call
            # below fails silently (caught by its own try/except) and we
            # always fall back to guessing the site from title keywords,
            # which is exactly the bug that caused ChatGPT tabs with
            # "GitHub" in the conversation title to be mislabeled.
            uia_ctx = _uia.UIAutomationInitializerInThread() if _uia else None
            if uia_ctx:
                uia_ctx.__enter__()
            try:
                while not self._activity_stop_event.is_set():
                    hwnd = get_foreground_hwnd()
                    title = get_active_window_title(hwnd)
                    if title:
                        page_url = get_active_window_url(hwnd)
                        report_activity_in_background(server_url, secret, title, page_url)
                    self._activity_stop_event.wait(interval)
            finally:
                if uia_ctx:
                    uia_ctx.__exit__(None, None, None)

        self._activity_thread = threading.Thread(target=loop, name='compulab-agent-activity', daemon=True)
        self._activity_thread.start()

    def stop_activity_reporting(self):
        self._activity_stop_event.set()
        self._activity_thread = None

    def _server_synced_now(self):
        """This PC's best estimate of the server's current wall-clock time:
        our own clock reading plus the offset measured at login. Used
        everywhere we'd otherwise call datetime.datetime.now() for session
        timing, so a lab PC with a drifted/wrong system clock still tracks
        the reservation end time correctly."""
        return datetime.datetime.now() + getattr(self, '_clock_offset', datetime.timedelta(0))

    def _show_warning(self):
        self._warning_shown = True
        self._warning_after_id = None
        if self.mini_panel:
            self.mini_panel.set_warning(True)
        if self.warning_banner:
            self.warning_banner.show()

    def _tick_countdown(self):
        if not self.mini_panel:
            return
        # NOTE: previously this returned here (without re-arming the next
        # tick) whenever the panel wasn't viewable, which silently killed
        # the countdown display forever the first time that happened. We
        # still skip the UI update in that case, but always reschedule.
        if self.mini_panel.win.winfo_viewable():
            if self._session_end_dt:
                remaining = self._session_end_dt - self._server_synced_now()
                total_seconds = max(int(remaining.total_seconds()), 0)
                mins, secs = divmod(total_seconds, 60)
                countdown_text = f'Time left: {mins:02d}:{secs:02d}'
                self.mini_panel.set_countdown(countdown_text)
                if self.warning_banner and self._warning_shown:
                    self.warning_banner.set_detail(
                        f'Please save your work — automatic logout in {mins:02d}:{secs:02d}.'
                    )
            else:
                # Manual/remote unlock with no known end time — no
                # countdown to show, just leave the label blank so the
                # panel reads as "SESSION ACTIVE" with only the Lock Now
                # button, no bogus/leftover timer text.
                self.mini_panel.set_countdown('')
        self._countdown_after_id = self.root.after(1000, self._tick_countdown)

    def _auto_relock(self):
        self._relock_after_id = None
        self.show(reason='expired')

    def start_override_countdown(self, seconds, admin_name):
        """
        Triggered by a '/override-warning' signal from the server: an
        Admin/In-Charge has confirmed taking this PC over for a different
        student. Shows the same red WarningBanner used for the normal
        "session ending soon" notice, ticking down each second, then
        auto-locks (reason='override') once time is up — same as if the
        current student had pressed 'Lock Now' themselves, except this one
        can't be dismissed/cancelled from this side.
        """
        # Cancel any prior override countdown so a second override signal
        # (shouldn't normally happen, but be safe) restarts cleanly instead
        # of stacking timers.
        if self._override_after_id is not None:
            try:
                self.root.after_cancel(self._override_after_id)
            except Exception:
                pass
            self._override_after_id = None
        if self._override_tick_after_id is not None:
            try:
                self.root.after_cancel(self._override_tick_after_id)
            except Exception:
                pass
            self._override_tick_after_id = None

        if not self.warning_banner:
            self._override_after_id = self.root.after(max(0, int(seconds)) * 1000, self._override_auto_lock)
            return

        self._override_seconds_left = max(0, int(seconds))
        self._override_admin_name = admin_name

        def _tick():
            self.warning_banner.set_detail(
                f'{self._override_admin_name} is taking over this PC. '
                f'Save your work now — locking in {self._override_seconds_left} second'
                f'{"s" if self._override_seconds_left != 1 else ""}.'
            )
            if self._override_seconds_left <= 0:
                self._override_tick_after_id = None
                return
            self._override_seconds_left -= 1
            self._override_tick_after_id = self.root.after(1000, _tick)

        self.warning_banner.title_label.config(text='ADMIN OVERRIDE')
        self.warning_banner.show()
        _tick()
        self._override_after_id = self.root.after(max(0, int(seconds)) * 1000, self._override_auto_lock)

    def _override_auto_lock(self):
        self._override_after_id = None
        if self._override_tick_after_id is not None:
            try:
                self.root.after_cancel(self._override_tick_after_id)
            except Exception:
                pass
            self._override_tick_after_id = None
        if self.warning_banner:
            self.warning_banner.hide()
            # Reset the title back to the normal wording for the next time
            # this banner is used for an ordinary reservation-ending warning.
            self.warning_banner.title_label.config(text='SYSTEM WARNING')
        self.show(reason='override')

    def lock_now_clicked(self):
        """The student pressed 'Lock Now' on the MiniPanel — end the session early."""
        self.show(reason='manual')

    def _cancel_timers(self):
        if self._relock_after_id is not None:
            try:
                self.root.after_cancel(self._relock_after_id)
            except Exception:
                pass
            self._relock_after_id = None
        if self._countdown_after_id is not None:
            try:
                self.root.after_cancel(self._countdown_after_id)
            except Exception:
                pass
            self._countdown_after_id = None
        if self._warning_after_id is not None:
            try:
                self.root.after_cancel(self._warning_after_id)
            except Exception:
                pass
            self._warning_after_id = None
        self._warning_shown = False
        if self.warning_banner:
            self.warning_banner.hide()

    def show(self, reason=None):
        """Ends the session. `reason` is 'expired' (auto timer), 'manual'
        (student clicked Lock Now), 'remote_lock' (Lab In-Charge sent a
        /lock signal, e.g. via the offline Manual PC Control tool),
        'override' (Admin/In-Charge Override countdown ran out — see
        start_override_countdown()), or None (startup, before any session
        has ever started).

        None of these sign the student out of Windows anymore — all of
        them just re-show this overlay over the SAME still-signed-in
        Windows session. This matches how the PC behaves before the very
        first login of the day (locked overlay, desktop untouched
        underneath). If a clean desktop is needed for the next student,
        use the Restart button on this screen."""
        self.stop_activity_reporting()
        self._cancel_timers()
        self._session_end_dt = None
        if self.mini_panel:
            self.mini_panel.hide()

        self.root.deiconify()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.root.geometry(f'{screen_w}x{screen_h}+0+0')
        self.root.attributes('-topmost', True)
        self.status_label.config(text='')
        if getattr(self, 'computer_status_value', None) is not None:
            self.computer_status_value.config(text='Ready for Login', fg=self.STATUS_GREEN)
        self.id_entry.delete(0, 'end')
        self.code_entry.delete(0, 'end')
        self.root.focus_force()
        self.id_entry.focus_set()
        self.visible = True
        hardening.apply_lock_hardening()

        # Re-check the server for this machine's real pc_id/lab_name every
        # time the lock screen (re)appears — including at startup — so a
        # rename made later in the admin panel shows up here on the very
        # next lock, with no need to re-run the agent or hand-edit
        # agent_config.json on the PC itself.
        fetch_pc_info_in_background(
            self.config.get('server_url', 'http://127.0.0.1:8000'),
            self.config.get('secret', ''),
        )

        if reason in ('expired', 'manual', 'remote_lock', 'override'):
            notify_end_session_sync(
                self.config.get('server_url', 'http://127.0.0.1:8000'),
                self.config.get('secret', ''),
                reason,
            )
            # None of 'expired', 'manual', or 'remote_lock' sign the student
            # out of Windows anymore — all three just re-lock this overlay
            # over the same still-signed-in session. The next student is
            # expected to use the Restart button on this screen (see the
            # Power/Restart buttons below) if a clean desktop is needed
            # before their turn.

    def hide(self):
        self.root.withdraw()
        self.visible = False
        hardening.remove_lock_hardening()

    def apply_pc_info(self, result):
        """Applies the pc_id / lab_name the server says are actually
        registered for this machine (see fetch_pc_info_in_background),
        replacing whatever agent_config.json's fallback "pc_name"/
        "lab_name" values were showing.

        Silently does nothing on failure — e.g. no network yet, or this
        PC hasn't been given an IP address / added in the admin panel —
        so the config.json fallback stays on screen instead of the label
        going blank or showing an error."""
        if not result or not result.get('ok'):
            return

        pc_id = (result.get('pc_id') or '').strip()
        if pc_id:
            self.pc_name = pc_id
            self.pc_name_label.config(text=f'—  {self.pc_name}  —')

        lab_name = (result.get('lab_name') or '').strip()
        if lab_name and getattr(self, 'lab_status_value', None) is not None:
            self.lab_status_value.config(text=lab_name)

    def apply_network_status(self, is_connected):
        """Updates the NETWORK status pill with the result of the latest
        check_network_in_background probe. Was previously never called at
        all — the pill's text was set once at build time to a hardcoded
        'Connected' and simply never touched again, so it kept showing
        'Connected' even with no network path to the server."""
        if getattr(self, 'network_status_value', None) is None:
            return
        if is_connected:
            self.network_status_value.config(text='Connected', fg=self.STATUS_GREEN)
        else:
            self.network_status_value.config(text='Offline', fg=self.ERROR)


def poll_queue(root, lock_screen):
    """Runs on the Tk main thread; applies lock/unlock/login-result actions as they arrive.

    IMPORTANT: the re-scheduling call at the bottom must ALWAYS run, no matter
    what happens above. Previously it sat outside the try/except, so a single
    unexpected exception while handling one item (e.g. inside
    handle_login_result/unlock_for_session) would silently kill this polling
    loop forever — no more mini panel updates from here, warning banner,
    auto-relock, or even future /lock /unlock signals, with nothing printed
    to explain why. Each item is now handled in its own try/except so one bad
    item can't take out the rest of the queue either, and the traceback is
    printed so failures are visible in the console instead of invisible.
    """
    try:
        while True:
            item = _command_queue.get_nowait()
            try:
                if item == 'unlock':
                    # CHANGED: a remote/manual unlock (e.g. Lab In-Charge
                    # pressing "Unlock PC" on the phone/web control page)
                    # now goes through the SAME unlock_for_session() path
                    # as a normal student login, instead of a bare
                    # lock_screen.hide(). This is what actually makes the
                    # floating MiniPanel ("SESSION ACTIVE" + "Lock Now"
                    # button) appear on the PC's screen — hide() alone just
                    # hid the overlay with no way for the student to lock
                    # it again themselves.
                    #
                    # session_end_time_str=None because a manual unlock has
                    # no reservation tied to it, so there's nothing to auto-
                    # relock/count down to — the student (or another /lock
                    # signal) is what ends the session.
                    lock_screen.unlock_for_session(session_end_time_str=None)
                elif item == 'lock':
                    # CHANGED: previously called show() with no reason,
                    # which meant a remote /lock signal (e.g. Lab In-Charge
                    # pressing "Lock PC" on the offline Manual PC Control
                    # tool) never told the CompuLab server the session had
                    # ended — pc.current_user/current_session/
                    # current_guest_name stayed pointed at the previous
                    # student, and no 'pc_lock' entry was written to the
                    # Activity Log. Passing 'remote_lock' here makes this
                    # path notify the server too, same as 'expired'/'manual'.
                    lock_screen.show(reason='remote_lock')
                elif isinstance(item, tuple) and item[0] == 'login_result':
                    lock_screen.handle_login_result(item[1])
                elif isinstance(item, tuple) and item[0] == 'pc_info':
                    lock_screen.apply_pc_info(item[1])
                elif isinstance(item, tuple) and item[0] == 'network_status':
                    lock_screen.apply_network_status(item[1])
                elif isinstance(item, tuple) and item[0] == 'override_warning':
                    lock_screen.start_override_countdown(item[1], item[2])
            except Exception:
                import traceback
                print('[agent] Error while handling queued item — continuing anyway:')
                traceback.print_exc()
    except queue.Empty:
        pass
    finally:
        root.after(200, poll_queue, root, lock_screen)


def main():
    config = load_config()
    port = config.get('port', 5555)

    start_http_server(port)
    hardening.install_keyboard_hook()
    check_network_in_background(config.get('server_url', 'http://127.0.0.1:8000'))
    print(f'CompuLab agent listening on port {port} — lock screen active.')

    root = tk.Tk()
    lock_screen = LockScreen(root, config)
    lock_screen.mini_panel = MiniPanel(on_lock_now=lock_screen.lock_now_clicked)
    lock_screen.warning_banner = WarningBanner()
    root.after(200, poll_queue, root, lock_screen)
    root.mainloop()


if __name__ == '__main__':
    main()