"""
CompuLab Agent — Windows lockdown helpers
==========================================

Two independent pieces of hardening, both toggled on when the lock screen
is shown and off again when it's unlocked:

  1. A low-level keyboard hook that swallows the Windows key, Alt+Tab,
     Alt+Esc, and Ctrl+Esc, so a locked PC can't be escaped via a hotkey
     into the Start menu or the task switcher.
  2. A registry policy (HKEY_CURRENT_USER ...\\Policies\\System\\DisableTaskMgr)
     that makes Windows itself refuse to open Task Manager.

IMPORTANT — read this before relying on it:
  - Ctrl+Alt+Delete CANNOT be intercepted by any user-mode script. It's a
    "Secure Attention Sequence" handled directly by Winlogon by design,
    specifically so that no application (malicious or otherwise) can trap
    it. This is not a limitation of this code — it's true of every kiosk
    tool that doesn't ship a custom Winlogon credential provider or use
    Group Policy / MDM at the OS level.
  - What DOES work here: even if a student presses Ctrl+Alt+Delete and
    picks "Task Manager" from that screen, the DisableTaskMgr policy below
    stops it from actually opening.
  - This module only writes to HKEY_CURRENT_USER, so it does not need
    Administrator rights — but it also only affects the currently logged
    in Windows user account. If your lab PCs auto-login to a single shared
    "student" account (typical for kiosk setups), this is exactly the
    right scope. For a stronger, machine-wide lockdown you'd additionally
    want Group Policy / MDM configuration, which is outside what a Python
    script can or should silently change.

Uses only the Python standard library (ctypes + winreg, both included with
the standard Windows Python installer). Safe to import on non-Windows —
every function becomes a no-op so the rest of the agent stays testable.
"""
import platform

IS_WINDOWS = platform.system().lower() == 'windows'

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes
    import winreg

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_SYSKEYDOWN = 0x0104

    VK_LWIN = 0x5B
    VK_RWIN = 0x5C
    VK_TAB = 0x09
    VK_ESCAPE = 0x1B
    VK_MENU = 0x12    # Alt
    VK_CONTROL = 0x11  # Ctrl

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [
            ('vkCode', wintypes.DWORD),
            ('scanCode', wintypes.DWORD),
            ('flags', wintypes.DWORD),
            ('time', wintypes.DWORD),
            ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG)),
        ]

    LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )

    _hook_handle = None
    _blocking_enabled = False  # only swallow hotkeys while True (i.e. while locked)

    def _key_is_down(vk_code):
        return user32.GetAsyncKeyState(vk_code) & 0x8000 != 0

    def _low_level_handler(n_code, w_param, l_param):
        if n_code == 0 and _blocking_enabled and w_param in (WM_KEYDOWN, WM_SYSKEYDOWN):
            kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            vk = kb.vkCode
            blocked = (
                vk in (VK_LWIN, VK_RWIN)                                  # Windows key
                or (vk == VK_TAB and _key_is_down(VK_MENU))               # Alt+Tab
                or (vk == VK_ESCAPE and _key_is_down(VK_MENU))            # Alt+Esc
                or (vk == VK_ESCAPE and _key_is_down(VK_CONTROL))         # Ctrl+Esc
            )
            if blocked:
                return 1  # non-zero return = "handled, don't pass this key on"
        return user32.CallNextHookEx(_hook_handle, n_code, w_param, l_param)

    # Keep a reference alive for the lifetime of the process — ctypes callback
    # objects get garbage collected otherwise, silently breaking the hook.
    _callback_ref = LowLevelKeyboardProc(_low_level_handler)

    def install_keyboard_hook():
        """Call once at agent startup. The hook itself starts inert (not
        blocking anything) until enable_hotkey_block(True) is called."""
        global _hook_handle
        if _hook_handle is None:
            _hook_handle = user32.SetWindowsHookExA(
                WH_KEYBOARD_LL, _callback_ref, kernel32.GetModuleHandleW(None), 0
            )
        return _hook_handle

    def uninstall_keyboard_hook():
        global _hook_handle
        if _hook_handle:
            user32.UnhookWindowsHookEx(_hook_handle)
            _hook_handle = None

    def enable_hotkey_block(enabled):
        global _blocking_enabled
        _blocking_enabled = bool(enabled)

    def set_task_manager_disabled(disabled):
        """Writes/removes the DisableTaskMgr policy for the CURRENT user."""
        key_path = r'Software\Microsoft\Windows\CurrentVersion\Policies\System'
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        try:
            if disabled:
                winreg.SetValueEx(key, 'DisableTaskMgr', 0, winreg.REG_DWORD, 1)
            else:
                try:
                    winreg.DeleteValue(key, 'DisableTaskMgr')
                except FileNotFoundError:
                    pass
        finally:
            winreg.CloseKey(key)

else:
    # Non-Windows: everything below is a harmless no-op so the rest of the
    # agent (and its tests) still import and run fine on Linux/macOS.
    def install_keyboard_hook():
        return None

    def uninstall_keyboard_hook():
        return None

    def enable_hotkey_block(enabled):
        return None

    def set_task_manager_disabled(disabled):
        return None


def apply_lock_hardening():
    """Call when the PC becomes locked: block hotkeys + disable Task Manager."""
    enable_hotkey_block(True)
    set_task_manager_disabled(True)


def remove_lock_hardening():
    """Call when the PC is unlocked: restore normal hotkeys + Task Manager."""
    enable_hotkey_block(False)
    set_task_manager_disabled(False)
