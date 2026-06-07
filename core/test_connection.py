"""
마우스 서버 연결 테스트
게임 PC에서 mouse_server.py 실행 후, 개발 PC에서 이 스크립트 실행

사용법:
  python core/test_connection.py --host <게임PC_IP>
  python core/test_connection.py --host 100.x.x.x --port 9999
"""
import socket
import json
import time
import argparse

DEFAULT_PORT = 9999


def send_command(sock, cmd, timeout=5):
    try:
        sock.settimeout(timeout)
        sock.sendall((json.dumps(cmd) + '\n').encode())
        resp = sock.recv(1024)
        return json.loads(resp.decode().strip())
    except socket.timeout:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}


def run_tests(host, port):
    print(f"\n{'='*50}")
    print(f"  마우스 서버 연결 테스트")
    print(f"  대상: {host}:{port}")
    print(f"{'='*50}\n")

    # 1. TCP 연결 시도
    print("[1] TCP 연결 시도...")
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((host, port))
        print(f"    ✅ 연결 성공!\n")
    except socket.timeout:
        print(f"    ❌ 연결 실패: 타임아웃 (방화벽 또는 서버 미실행 확인)\n")
        return
    except ConnectionRefusedError:
        print(f"    ❌ 연결 거부: mouse_server.py가 게임 PC에서 실행 중인지 확인\n")
        return
    except Exception as e:
        print(f"    ❌ 연결 실패: {e}\n")
        return

    # 2. Ping 테스트
    print("[2] Ping 테스트...")
    result = send_command(sock, {"action": "ping"})
    if result.get("result") == "pong":
        print(f"    ✅ Ping → Pong 성공!\n")
    else:
        print(f"    ❌ Ping 실패: {result}\n")

    # 3. 마우스 이동 테스트 (화면 중앙 근처로)
    print("[3] 마우스 이동 테스트 (100, 100)...")
    result = send_command(sock, {"action": "move", "x": 100, "y": 100})
    if result.get("result") == "ok":
        print(f"    ✅ 마우스 이동 명령 전송 성공\n")
    else:
        print(f"    ❌ 마우스 이동 실패: {result}\n")

    # 4. 왕복 레이턴시 측정 (5회)
    print("[4] 레이턴시 측정 (5회 ping)...")
    latencies = []
    for i in range(5):
        t0 = time.time()
        result = send_command(sock, {"action": "ping"})
        elapsed_ms = (time.time() - t0) * 1000
        if "error" not in result:
            latencies.append(elapsed_ms)
            print(f"    #{i+1}: {elapsed_ms:.1f}ms")
        else:
            print(f"    #{i+1}: 실패 - {result}")
        time.sleep(0.1)

    if latencies:
        avg = sum(latencies) / len(latencies)
        print(f"\n    평균 레이턴시: {avg:.1f}ms")
        if avg < 30:
            print(f"    ✅ 레이턴시 양호 (같은 네트워크 또는 Tailscale 연결)")
        elif avg < 100:
            print(f"    ⚠️  레이턴시 보통 (자동화에 사용 가능)")
        else:
            print(f"    ❌ 레이턴시 높음 ({avg:.0f}ms) - 네트워크 확인 필요")

    sock.close()

    print(f"\n{'='*50}")
    print("  테스트 완료")
    print(f"{'='*50}\n")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='마우스 서버 연결 테스트')
    parser.add_argument('--host', required=True, help='게임 PC IP 또는 Tailscale IP')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT, help=f'포트 (기본값: {DEFAULT_PORT})')
    args = parser.parse_args()

    run_tests(args.host, args.port)
