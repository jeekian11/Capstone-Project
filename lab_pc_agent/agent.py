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
Windows Python installer) — nothing to `pip install`.

--------------------------------------------------------------------------
SETUP ON EACH LAB PC
--------------------------------------------------------------------------
1. Copy this whole `lab_pc_agent` folder to the PC (e.g. C:\\CompuLabAgent).
2. Edit `agent_config.json`:
     - "secret"  -> must be EXACTLY the same string as PC_AGENT_SHARED_SECRET
                    in the server's settings.py
     - "port"    -> must match PC_AGENT_PORT in settings.py (default 5555)
     - "lock_title" / "lock_subtitle" -> the text shown on the lock screen
3. Test manually first:  python agent.py
   A fullscreen "PC Locked" screen should appear immediately.
4. Once it works, set it to run automatically at every startup — see
   "RUNNING AS A STARTUP SERVICE" below.
5. Allow inbound connections on the chosen port (default 5555) for
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
import subprocess
import threading
import tkinter as tk
from tkinter import messagebox
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import hardening

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agent_config.json')

# Commands passed between threads and the Tk main thread — Tk widgets can
# only safely be touched from the thread that created them, so neither the
# HTTP handler nor the login-submission thread ever calls into the UI
# directly; they drop a message here and the UI thread picks it up on its
# own polling loop. Two kinds of messages:
#   'unlock' / 'lock'            -> from the HTTP server (remote lock/unlock)
#   ('login_result', dict)       -> from the on-screen login form's network call
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
        self.end_headers()
        self.wfile.write(json.dumps(body).encode('utf-8'))

    def do_OPTIONS(self):
        # CORS preflight, needed so a browser-based control page (e.g. opened
        # from a phone) is allowed to call this agent's API.
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
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

    def do_POST(self):
        if self.path == '/unlock':
            self._handle_signal('unlock')
        elif self.path == '/lock':
            self._handle_signal('lock')
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


def get_active_window_title():
    """Returns the foreground window's title bar text, or None if it can't
    be read (non-Windows OS, no window focused, permissions issue, etc).
    Uses only ctypes + the standard library — no pywin32/extra installs.

    NOTE: this reads whatever text Windows puts in the title bar of the
    currently focused window. For a browser that's normally the page
    title (e.g. "Facebook - Google Chrome"), not the address bar's URL —
    this agent never reads the address bar, page content, or keystrokes.
    """
    if os.name != 'nt':
        return None  # only meaningful on the actual Windows lab PCs
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
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


def report_activity_in_background(server_url, secret, window_title):
    """Fire-and-forget POST of one active-window-title sample. Same pattern
    as submit_logout_in_background — never blocks the UI thread, and a
    failed/slow request here just means one missed sample, not a problem
    worth surfacing to the student."""
    def worker():
        url = server_url.rstrip('/') + '/labs/api/pc-agent-activity/'
        payload = json.dumps({'secret': secret, 'window_title': window_title or ''}).encode('utf-8')
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
        self.win.configure(bg='#0a121f')

        w, h = 300, 64
        x = self.win.winfo_screenwidth() - w - 24
        y = self.win.winfo_screenheight() - h - 24
        self.win.geometry(f'{w}x{h}+{x}+{y}')

        border = tk.Frame(self.win, bg='#00e5ff', bd=0)
        border.pack(fill='both', expand=True, padx=1, pady=1)
        inner = tk.Frame(border, bg='#0a121f')
        inner.pack(fill='both', expand=True, padx=1, pady=1)

        left = tk.Frame(inner, bg='#0a121f')
        left.pack(side='left', fill='both', expand=True, padx=(14, 6))
        tk.Label(left, text='SESSION ACTIVE', font=('Consolas', 9, 'bold'), fg='#5b7a94', bg='#0a121f').pack(anchor='w', pady=(10, 0))
        self.countdown_label = tk.Label(left, text='', font=('Consolas', 13, 'bold'), fg='#00e5ff', bg='#0a121f')
        self.countdown_label.pack(anchor='w')

        logout_btn = tk.Button(
            inner, text='LOG OUT', font=('Consolas', 10, 'bold'),
            bg='#ff5470', fg='#2b0007', activebackground='#ff7a90', activeforeground='#2b0007',
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
    """

    NORMAL_ACCENT = '#00e5ff'
    WARNING_ACCENT = '#ffb020'

    def __init__(self, on_lock_now):
        self.win = tk.Toplevel()
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg=self.NORMAL_ACCENT)
        w, h = 260, 92
        screen_w = self.win.winfo_screenwidth()
        self.win.geometry(f'{w}x{h}+{screen_w - w - 16}+16')  # top-right corner

        self.border = tk.Frame(self.win, bg=self.NORMAL_ACCENT, bd=0)
        self.border.pack(fill='both', expand=True, padx=1, pady=1)
        inner = tk.Frame(self.border, bg='#0a121f')
        inner.pack(fill='both', expand=True, padx=1, pady=1)

        self.status_text = tk.Label(
            inner, text='SESSION ACTIVE', font=('Consolas', 10, 'bold'),
            fg=self.NORMAL_ACCENT, bg='#0a121f',
        )
        self.status_text.pack(pady=(10, 2))

        self.countdown_text = tk.Label(
            inner, text='', font=('Consolas', 9), fg='#5b7a94', bg='#0a121f',
        )
        self.countdown_text.pack()

        btn = tk.Button(
            inner, text='🔒 Lock Now', font=('Segoe UI', 9, 'bold'),
            bg='#1c3049', fg='#e8f6ff', activebackground='#2a4666', activeforeground='#e8f6ff',
            relief='flat', cursor='hand2', bd=0, command=on_lock_now,
        )
        btn.pack(pady=(6, 8), ipady=3, ipadx=6)

        self.win.withdraw()

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
        self.status_text.configure(
            fg=accent,
            text='ENDING SOON' if is_warning else 'SESSION ACTIVE',
        )


class WarningBanner:
    """
    Prominent top-center banner that appears 2–3 minutes before a
    reservation's end_time, so the student notices even if they never look
    at the small corner MiniPanel. Has its own 'OK' button so the student
    can dismiss it early if it's blocking their work — dismissing it does
    NOT cancel the countdown, it just hides this banner (the MiniPanel
    still shows the countdown, and the banner will still be force-hidden
    automatically once the session actually ends).
    """

    def __init__(self):
        self.win = tk.Toplevel()
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        w, h = 640, 74
        screen_w = self.win.winfo_screenwidth()
        self.win.geometry(f'{w}x{h}+{(screen_w - w) // 2}+24')

        self.win.configure(bg='#ffb020')
        inner = tk.Frame(self.win, bg='#241a06')
        inner.pack(fill='both', expand=True, padx=2, pady=2)

        text_col = tk.Frame(inner, bg='#241a06')
        text_col.pack(side='left', fill='both', expand=True, padx=(20, 6))

        self.title_label = tk.Label(
            text_col, text='⚠  SESSION ENDING SOON', font=('Consolas', 15, 'bold'),
            fg='#ffb020', bg='#241a06', anchor='w',
        )
        self.title_label.pack(anchor='w', pady=(10, 2))

        self.detail_label = tk.Label(
            text_col, text='', font=('Segoe UI', 10), fg='#f0d9a8', bg='#241a06', anchor='w',
        )
        self.detail_label.pack(anchor='w')

        ok_btn = tk.Button(
            inner, text='OK', font=('Consolas', 11, 'bold'), width=6,
            bg='#241a06', fg='#ffb020', activebackground='#3a2a0c', activeforeground='#ffb020',
            relief='flat', bd=0, cursor='hand2', command=self.hide,
        )
        ok_btn.pack(side='right', padx=(6, 20))

        self.win.withdraw()

    def show(self):
        self.win.deiconify()
        self.win.attributes('-topmost', True)

    def hide(self):
        self.win.withdraw()

    def set_detail(self, text):
        self.detail_label.config(text=text)


class LockScreen:
    """Fullscreen overlay window that blocks the desktop until unlocked.

    Includes a built-in login form (ID Number + Reservation Code) so
    students can check in directly on the locked PC — no separate
    reception/kiosk computer required. Styled as a dark sci-fi/HUD panel:
    grid backdrop, glowing cyan accents, corner brackets, live clock.

    Also owns the session timer: once a login succeeds, it schedules an
    automatic re-lock for when that reservation's end_time is reached, and
    shows a small MiniPanel with a "Lock Now" button for ending early.
    """

    BG = '#05070d'
    GRID = '#0c1626'
    CARD_BG = '#0a121f'
    ACCENT = '#00e5ff'
    ACCENT_DIM = '#0a5a66'
    ACCENT2 = '#7c5cff'
    TEXT = '#e8f6ff'
    MUTED = '#5b7a94'
    ERROR = '#ff5470'
    SUCCESS = '#39ff8f'
    FONT_MONO = 'Consolas'

    CARD_W = 760
    CARD_H = 580
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
        self.mini_panel = None  # created after root exists; see main()
        self.warning_banner = None  # created after root exists; see main()
        self.status_bar = None
        self.relock_after_id = None
        self._activity_stop_event = threading.Event()
        self._activity_thread = None

        root.title('CompuLab')
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

        self.canvas = tk.Canvas(root, bg=self.BG, highlightthickness=0, width=self.screen_w, height=self.screen_h)
        self.canvas.place(x=0, y=0)
        self._draw_backdrop()

        cx, cy = self.screen_w // 2, self.screen_h // 2

        # Live clock, top-center — small touch of "this is a live system" feel.
        self.clock_text_id = self.canvas.create_text(
            cx, 48, text='', fill=self.ACCENT, font=(self.FONT_MONO, 16, 'bold'),
        )
        self._tick_clock()

        frame = tk.Frame(root, bg=self.CARD_BG)
        frame.place(x=cx, y=cy, anchor='center', width=self.CARD_W - 40, height=self.CARD_H - 40)

        inner = tk.Frame(frame, bg=self.CARD_BG)
        inner.place(relx=0.5, rely=0.5, anchor='center')

        # Power controls, bottom-right corner — deliberately small/muted so
        # they don't compete visually with the login form, but always
        # reachable without needing to be logged in first (e.g. a PC that's
        # frozen or needs a routine restart before the next class).
        power_row = tk.Frame(root, bg=self.BG)
        power_row.place(relx=1.0, rely=1.0, anchor='se', x=-28, y=-24)

        def _power_btn(parent, text, command):
            btn = tk.Button(
                parent, text=text, font=(self.FONT_MONO, 9, 'bold'),
                bg=self.BG, fg=self.MUTED, activebackground='#1c3049', activeforeground=self.TEXT,
                relief='flat', bd=0, cursor='hand2', padx=10, pady=6, command=command,
            )
            btn.bind('<Enter>', lambda e: btn.config(fg=self.TEXT))
            btn.bind('<Leave>', lambda e: btn.config(fg=self.MUTED))
            return btn

        restart_btn = _power_btn(power_row, '⟳  RESTART', self.restart_clicked)
        restart_btn.pack(side='right', padx=(6, 0))
        poweroff_btn = _power_btn(power_row, '⏻  POWER OFF', self.power_off_clicked)
        poweroff_btn.pack(side='right')

        # Pulsing status dot + "SYSTEM LOCKED" readout above the big title.
        status_row = tk.Frame(inner, bg=self.CARD_BG)
        status_row.pack(pady=(0, 6))
        self.status_dot = tk.Canvas(status_row, width=12, height=12, bg=self.CARD_BG, highlightthickness=0)
        self.status_dot.pack(side='left', padx=(0, 8))
        self._dot_id = self.status_dot.create_oval(1, 1, 11, 11, fill=self.ACCENT, outline='')
        tk.Label(
            status_row, text='S Y S T E M   L O C K E D', font=(self.FONT_MONO, 11, 'bold'),
            fg=self.MUTED, bg=self.CARD_BG,
        ).pack(side='left')
        self._pulse_dot()

        title_text = config.get('lock_title', 'PC LOCKED')
        spaced_title = ' '.join(title_text)  # letter-spacing hack for a HUD look
        tk.Label(
            inner, text=spaced_title, font=(self.FONT_MONO, 30, 'bold'),
            fg=self.TEXT, bg=self.CARD_BG,
        ).pack(pady=(0, 4))

        # Thin accent underline beneath the title.
        underline = tk.Canvas(inner, width=180, height=3, bg=self.CARD_BG, highlightthickness=0)
        underline.pack(pady=(0, 18))
        underline.create_line(0, 1, 180, 1, fill=self.ACCENT, width=2)

        tk.Label(
            inner, text=config.get('lock_subtitle', 'Enter your Student ID and Reservation Code to use this PC.'),
            font=('Segoe UI', 12), fg=self.MUTED, bg=self.CARD_BG, wraplength=460, justify='center',
        ).pack(pady=(0, 26))

        form = tk.Frame(inner, bg=self.CARD_BG)
        form.pack()

        entry_style = dict(
            font=(self.FONT_MONO, 15), width=26, bg='#0f1c30', fg=self.TEXT,
            insertbackground=self.ACCENT, relief='flat',
            highlightthickness=2, highlightbackground='#1c3049', highlightcolor=self.ACCENT,
        )

        tk.Label(form, text='STUDENT / INSTRUCTOR ID', font=(self.FONT_MONO, 10, 'bold'), fg=self.MUTED, bg=self.CARD_BG).grid(row=0, column=0, sticky='w', pady=(0, 5))
        self.id_entry = tk.Entry(form, **entry_style)
        self.id_entry.grid(row=1, column=0, ipady=9, pady=(0, 18))

        tk.Label(form, text='RESERVATION CODE', font=(self.FONT_MONO, 10, 'bold'), fg=self.MUTED, bg=self.CARD_BG).grid(row=2, column=0, sticky='w', pady=(0, 5))
        self.code_entry = tk.Entry(form, **entry_style)
        self.code_entry.grid(row=3, column=0, ipady=9, pady=(0, 22))

        self.submit_button = tk.Button(
            form, text='►  UNLOCK ACCESS', font=(self.FONT_MONO, 12, 'bold'),
            bg=self.ACCENT, fg='#00252b', activebackground='#33f0ff', activeforeground='#00252b',
            relief='flat', cursor='hand2', bd=0, command=self.on_submit,
        )
        self.submit_button.grid(row=4, column=0, sticky='we', ipady=11)
        self.submit_button.bind('<Enter>', lambda e: self.submit_button.config(bg='#33f0ff'))
        self.submit_button.bind('<Leave>', lambda e: self.submit_button.config(bg=self.ACCENT) if not self.submitting else None)

        self.status_label = tk.Label(
            inner, text='', font=(self.FONT_MONO, 11), fg=self.ERROR, bg=self.CARD_BG, wraplength=460, justify='center',
        )
        self.status_label.pack(pady=(18, 0))

        self.id_entry.bind('<Return>', lambda e: self.code_entry.focus_set())
        self.code_entry.bind('<Return>', lambda e: self.on_submit())

        self._frame = frame
        self.show()

    def _draw_backdrop(self):
        """Subtle HUD-style grid + corner brackets around where the login card sits."""
        step = 48
        for x in range(0, self.screen_w, step):
            self.canvas.create_line(x, 0, x, self.screen_h, fill=self.GRID, width=1)
        for y in range(0, self.screen_h, step):
            self.canvas.create_line(0, y, self.screen_w, y, fill=self.GRID, width=1)

        cx, cy = self.screen_w // 2, self.screen_h // 2
        left, right = cx - self.CARD_W // 2, cx + self.CARD_W // 2
        top, bottom = cy - self.CARD_H // 2, cy + self.CARD_H // 2
        arm = 26

        # Outer glow-ish border rectangle around the card.
        self.canvas.create_rectangle(left, top, right, bottom, outline=self.ACCENT_DIM, width=1)

        # Four sci-fi corner brackets, drawn just outside the card's border.
        pad = 10
        corners = [
            (left - pad, top - pad, 1, 1),      # top-left: draw right + down
            (right + pad, top - pad, -1, 1),    # top-right: draw left + down
            (left - pad, bottom + pad, 1, -1),  # bottom-left: draw right + up
            (right + pad, bottom + pad, -1, -1),  # bottom-right: draw left + up
        ]
        for x, y, dx, dy in corners:
            self.canvas.create_line(x, y, x + arm * dx, y, fill=self.ACCENT, width=3)
            self.canvas.create_line(x, y, x, y + arm * dy, fill=self.ACCENT, width=3)

    def _tick_clock(self):
        now = datetime.datetime.now().strftime('%H:%M:%S    %d %b %Y')
        self.canvas.itemconfig(self.clock_text_id, text=now)
        self.root.after(1000, self._tick_clock)

    def _pulse_dot(self):
        self._pulse_on = not self._pulse_on
        color = self.ACCENT if self._pulse_on else self.ACCENT_DIM
        self.status_dot.itemconfig(self._dot_id, fill=color)
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
        id_number = self.id_entry.get().strip()
        code = self.code_entry.get().strip()

        if not id_number or not code:
            self.status_label.config(fg=self.ERROR, text='Please fill in both ID and Reservation Code.')
            return

        self.submitting = True
        self.submit_button.config(state='disabled', text='►  VERIFYING...')
        self.status_label.config(fg=self.MUTED, text='Confirming your reservation...')

        submit_login_in_background(
            self.config.get('server_url', 'http://127.0.0.1:8000'),
            self.config.get('secret', ''),
            id_number,
            code,
        )

    def handle_login_result(self, result):
        self.submitting = False
        self.submit_button.config(state='normal', text='►  UNLOCK ACCESS')

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
        it actually is."""
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
            while not self._activity_stop_event.is_set():
                title = get_active_window_title()
                if title:
                    report_activity_in_background(server_url, secret, title)
                self._activity_stop_event.wait(interval)

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
                self.mini_panel.set_countdown('')
        self._countdown_after_id = self.root.after(1000, self._tick_countdown)

    def _auto_relock(self):
        self._relock_after_id = None
        self.show(reason='expired')

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
        (student clicked Lock Now), or None (remote /lock command / startup).

        None of these sign the student out of Windows anymore — 'expired',
        'manual', and None (remote /lock / startup) all just re-show this
        overlay over the SAME still-signed-in Windows session. This matches
        how the PC behaves before the very first login of the day (locked
        overlay, desktop untouched underneath). If a clean desktop is needed
        for the next student, use the Restart button on this screen."""
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
        self.id_entry.delete(0, 'end')
        self.code_entry.delete(0, 'end')
        self.root.focus_force()
        self.id_entry.focus_set()
        self.visible = True
        hardening.apply_lock_hardening()

        if reason in ('expired', 'manual'):
            notify_end_session_sync(
                self.config.get('server_url', 'http://127.0.0.1:8000'),
                self.config.get('secret', ''),
                reason,
            )
            # Neither 'expired' nor 'manual' signs the student out of Windows
            # anymore — both just re-lock this overlay over the same still-
            # signed-in session. The next student is expected to use the
            # Restart button on this screen (see the Power/Restart buttons
            # below) if a clean desktop is needed before their turn.

    def hide(self):
        self.root.withdraw()
        self.visible = False
        hardening.remove_lock_hardening()


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
                    lock_screen.hide()
                elif item == 'lock':
                    lock_screen.show()
                elif isinstance(item, tuple) and item[0] == 'login_result':
                    lock_screen.handle_login_result(item[1])
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
    print(f'CompuLab agent listening on port {port} — lock screen active.')

    root = tk.Tk()
    lock_screen = LockScreen(root, config)
    lock_screen.mini_panel = MiniPanel(on_lock_now=lock_screen.lock_now_clicked)
    lock_screen.warning_banner = WarningBanner()
    root.after(200, poll_queue, root, lock_screen)
    root.mainloop()


if __name__ == '__main__':
    main()

