"""
게임 PC에서 실행하는 마우스/키보드 원격 제어 서버
개발 PC에서 TCP 명령을 받아 실행합니다.

실행법: python mouse_server.py
"""
import socket, json, time, sys

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.05
except ImportError:
    print("[!] pyautogui가 없습니다. pip install pyautogui 실행 후 다시 시작하세요.")
    sys.exit(1)

# win32gui: 창 포커스용 (없어도 동작)
try:
    import win32gui, win32con, win32api
    WIN32_AVAILABLE = True
    print("[+] win32gui 사용 가능 (창 포커스 지원)")
except ImportError:
    WIN32_AVAILABLE = False
    print("[!] win32gui 없음 - pip install pywin32  (포커스 기능 제한)")

HOST = '0.0.0.0'
PORT = 9999


def focus_window_at(x, y):
    """화면 좌표 (x,y)에 있는 창을 최상위로 올림"""
    if not WIN32_AVAILABLE:
        return False, "win32gui 없음"
    try:
        hwnd = win32gui.WindowFromPoint((x, y))
        if not hwnd:
            return False, "창 없음"
        # 최상위 부모 창 찾기
        root = win32gui.GetAncestor(hwnd, 3)  # GA_ROOTOWNER
        target = root if root else hwnd
        title  = win32gui.GetWindowText(target)

        # 최소화되어 있으면 복원
        if win32gui.IsIconic(target):
            win32gui.ShowWindow(target, win32con.SW_RESTORE)
            time.sleep(0.1)

        win32gui.SetForegroundWindow(target)
        time.sleep(0.05)
        return True, f"포커스: [{title}] hwnd={target}"
    except Exception as e:
        return False, f"포커스 실패: {e}"


def handle_command(cmd):
    action = cmd.get('action')
    x = cmd.get('x', 0)
    y = cmd.get('y', 0)

    if action == 'focus_window':
        ok, msg = focus_window_at(x, y)
        print(f"  [focus] {msg}")
        return 'focused' if ok else msg

    elif action == 'move':
        pyautogui.moveTo(x, y, duration=0.1)
        print(f"  [move] ({x},{y})")

    elif action == 'click':
        pyautogui.click(x, y)
        print(f"  [click] ({x},{y})")

    elif action == 'double_click':
        pyautogui.doubleClick(x, y)
        print(f"  [dbl_click] ({x},{y})")

    elif action == 'scroll':
        amt = cmd.get('amount', -3)
        pyautogui.scroll(amt, x=x, y=y)
        print(f"  [scroll] ({x},{y}) amt={amt}")

    elif action == 'key':
        k = cmd['key']
        pyautogui.press(k)
        print(f"  [key] {k}")

    elif action == 'ping':
        return 'pong'

    return 'ok'


print(f"[*] 마우스 서버 시작: {HOST}:{PORT}")
print(f"[*] 개발 PC에서 이 PC의 Tailscale IP로 접속하세요")
print(f"[*] 종료: Ctrl+C")

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(5)

    while True:
        conn, addr = srv.accept()
        print(f"\n[+] 연결됨: {addr}")
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
                        cmd = json.loads(line.decode())
                        result = handle_command(cmd)
                        conn.sendall((json.dumps({'result': result}) + '\n').encode())
                    except Exception as e:
                        print(f"  [!] 오류: {e}")
                        conn.sendall((json.dumps({'error': str(e)}) + '\n').encode())
        print(f"[-] 연결 종료: {addr}")
