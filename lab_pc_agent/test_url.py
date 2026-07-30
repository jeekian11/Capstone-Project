from agent import get_foreground_hwnd, get_active_window_title, get_active_window_url

hwnd = get_foreground_hwnd()
print("hwnd:", hwnd)
print("title:", get_active_window_title(hwnd))
print("url:", get_active_window_url(hwnd))
