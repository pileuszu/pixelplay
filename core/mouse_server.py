"""
Mouse/Keyboard remote control server for game PC.
Run this on the game PC, then control it from dev PC via TCP.

Usage (run as Administrator):
  python mouse_server.py
"""
import socket, json, time, sys, ctypes

# Force UTF-8 output to avoid encoding errors on Korean Windows
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0
    print("[+] pyautogui OK", flush=True)
except ImportError:
    print("[!] pyautogui not found. Run: pip install pyautogui", flush=True)
    sys.exit(1)

try:
    import win32gui, win32con, win32api
    WIN32_AVAILABLE = True
    print("[+] win32api OK", flush=True)
except ImportError:
    WIN32_AVAILABLE = False
    print("[!] win32api not found. Run: pip install pywin32", flush=True)

HOST = '0.0.0.0'
PORT = 9999

# ─── Admin check ──────────────────────────────────────────────
try:
    is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
except Exception:
    is_admin = False

if not is_admin:
    print("=" * 55, flush=True)
    print("  [WARNING] Not running as Administrator!", flush=True)
    print("  If the game runs as admin, clicks will be blocked (UIPI).", flush=True)
    print("  -> Restart terminal as Administrator and try again.", flush=True)
    print("=" * 55, flush=True)
else:
    print("[+] Administrator privileges confirmed", flush=True)


# ─── Click helper ─────────────────────────────────────────────
def do_click(x, y):
    """Try win32api.mouse_event first (low-level), fall back to pyautogui"""
    ctypes.windll.user32.SetCursorPos(x, y)
    time.sleep(0.05)

    if WIN32_AVAILABLE:
        try:
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0)
            time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP,   0, 0)
            return 'win32api'
        except Exception as e:
            print(f"  [!] win32api click failed: {e}", flush=True)

    pyautogui.click(x, y)
    return 'pyautogui'


def focus_window_at(x, y):
    """Bring the window at screen coord (x,y) to foreground"""
    if not WIN32_AVAILABLE:
        return False, "win32api not available"
    try:
        hwnd   = win32gui.WindowFromPoint((x, y))
        if not hwnd:
            return False, "no window at coords"
        root   = win32gui.GetAncestor(hwnd, 3)  # GA_ROOTOWNER
        target = root if root else hwnd
        title  = win32gui.GetWindowText(target)

        if win32gui.IsIconic(target):
            win32gui.ShowWindow(target, win32con.SW_RESTORE)
            time.sleep(0.1)

        win32gui.SetForegroundWindow(target)
        time.sleep(0.05)
        return True, f"focused [{title}] hwnd={target}"
    except Exception as e:
        return False, f"focus failed: {e}"


def handle_command(cmd):
    action = cmd.get('action')
    x = cmd.get('x', 0)
    y = cmd.get('y', 0)

    if action == 'focus_window':
        ok, msg = focus_window_at(x, y)
        print(f"  [focus] {msg}", flush=True)
        return 'focused' if ok else msg

    elif action == 'move':
        ctypes.windll.user32.SetCursorPos(x, y)
        print(f"  [move] ({x},{y})", flush=True)

    elif action == 'click':
        method = do_click(x, y)
        print(f"  [click/{method}] ({x},{y})", flush=True)

    elif action == 'double_click':
        do_click(x, y)
        time.sleep(0.05)
        do_click(x, y)
        print(f"  [dbl_click] ({x},{y})", flush=True)

    elif action == 'scroll':
        amt = cmd.get('amount', -3)
        pyautogui.scroll(amt, x=x, y=y)
        print(f"  [scroll] ({x},{y}) amt={amt}", flush=True)

    elif action == 'key':
        k = cmd['key']
        pyautogui.press(k)
        print(f"  [key] {k}", flush=True)

    elif action == 'ping':
        return 'pong'

    return 'ok'


print(f"[*] Mouse server listening on {HOST}:{PORT}", flush=True)
print(f"[*] Connect from dev PC using this PC's Tailscale IP", flush=True)
print(f"[*] Press Ctrl+C to stop", flush=True)

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(5)

    while True:
        conn, addr = srv.accept()
        print(f"\n[+] Connected: {addr}", flush=True)
        with conn:
            data = b''
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                while b'\n' in data:
                    line, data = data.split(b'\n', 1)
                    try:
                        cmd    = json.loads(line.decode())
                        result = handle_command(cmd)
                        conn.sendall((json.dumps({'result': result}) + '\n').encode())
                    except Exception as e:
                        print(f"  [!] error: {e}", flush=True)
                        conn.sendall((json.dumps({'error': str(e)}) + '\n').encode())
        print(f"[-] Disconnected: {addr}", flush=True)
