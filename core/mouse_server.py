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

HOST = '0.0.0.0'
PORT = 9999

def handle_command(cmd):
    action = cmd.get('action')
    if action == 'move':
        pyautogui.moveTo(cmd['x'], cmd['y'], duration=0.1)
    elif action == 'click':
        pyautogui.click(cmd['x'], cmd['y'])
    elif action == 'double_click':
        pyautogui.doubleClick(cmd['x'], cmd['y'])
    elif action == 'scroll':
        pyautogui.scroll(cmd.get('amount', -3), x=cmd.get('x'), y=cmd.get('y'))
    elif action == 'key':
        pyautogui.press(cmd['key'])
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
        print(f"[+] 연결됨: {addr}")
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
                        conn.sendall((json.dumps({'error': str(e)}) + '\n').encode())
