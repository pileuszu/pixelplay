"""
CPBV 좌표 보정 도구 (Calibration Tool)

사용법:
  python games/cpbv/calibrate.py --stream http://IP:8080/video --mouse-host IP

기능:
  1. 현재 게임 화면 캡처 → 오버레이로 UI 요소 위치 표시
  2. 클릭 테스트
  3. 팀 로고 템플릿 저장
"""
import cv2
import sys
import os
import json
import argparse
import time
import socket

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from games.cpbv.config_cpbv import (
    WINDOW_LEFT, WINDOW_TOP, WINDOW_RIGHT, WINDOW_BOTTOM,
    WINDOW_WIDTH, WINDOW_HEIGHT,
    STREAM_URL, MOUSE_HOST, MOUSE_PORT, UI,
    BATTER_P1, BATTER_P2, BATTER_P3,
    PITCHER_P1, PITCHER_P2, PITCHER_P3,
    TEAM_TEMPLATES_DIR, TEAMS
)


# ─── 마우스 클라이언트 ─────────────────────────────────────────────────
class MouseClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        if host:
            self._connect()

    def _connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            print(f"[+] 마우스 서버 연결: {self.host}:{self.port}")
        except Exception as e:
            print(f"[!] 마우스 서버 연결 실패: {e}")
            self.sock = None

    def click_ratio(self, rx, ry):
        """창 비율 좌표(0~1)로 클릭"""
        abs_x = int(WINDOW_LEFT + rx * WINDOW_WIDTH)
        abs_y = int(WINDOW_TOP  + ry * WINDOW_HEIGHT)
        # 먼저 게임 창 중앙 클릭으로 포커스 확보
        focus_x = int(WINDOW_LEFT + 0.5 * WINDOW_WIDTH)
        focus_y = int(WINDOW_TOP  + 0.1 * WINDOW_HEIGHT)
        self._send({'action': 'click', 'x': focus_x, 'y': focus_y})
        import time; time.sleep(0.3)
        self._send({'action': 'click', 'x': abs_x, 'y': abs_y})
        print(f"  클릭: ({rx:.3f}, {ry:.3f}) → 스크린 ({abs_x}, {abs_y})")

    def _send(self, cmd):
        if not self.sock:
            return
        try:
            self.sock.sendall((json.dumps(cmd) + '\n').encode())
            self.sock.recv(1024)
        except:
            self._connect()


# ─── 스트림 캡처 ──────────────────────────────────────────────────────
def get_frame(cap):
    ret, frame = cap.read()
    return frame if ret else None


def crop_to_window(frame):
    """스트림 프레임에서 게임 창 영역만 크롭
    
    OBS가 게임창만 캡처하는 경우 → 크롭 불필요, 그대로 반환
    OBS가 전체 화면 캡처하는 경우 → 스트림 해상도 기준으로 스케일링 후 크롭
    """
    fh, fw = frame.shape[:2]
    
    # 스트림이 게임창 크기(736×1319)보다 작거나 비슷하면 그대로 반환
    # (OBS가 게임창만 캡처하는 케이스)
    if fw <= WINDOW_WIDTH * 1.2 and fh <= WINDOW_HEIGHT * 1.2:
        return frame
    
    # 스트림이 전체 화면을 캡처하는 경우: 스케일 보정 후 크롭
    # 예: 1920×1080 → 1280×720 스트림이면 scale = 1280/1920
    # 게임 PC 화면 해상도가 필요한데 모르면 일단 그냥 반환
    # TODO: 실제 게임 PC 해상도 확인 후 보정
    return frame


# ─── 오버레이 그리기 ──────────────────────────────────────────────────
def draw_ui_overlay(frame, mode='batter_p1'):
    """UI 요소 위치를 오버레이로 표시"""
    h, w = frame.shape[:2]
    overlay = frame.copy()

    def ratio_to_px(rx, ry):
        return int(rx * w), int(ry * h)

    def draw_point(rx, ry, label, color=(0, 255, 0)):
        x, y = ratio_to_px(rx, ry)
        cv2.circle(overlay, (x, y), 8, color, -1)
        cv2.putText(overlay, label, (x + 10, y + 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)

    def draw_region(rx, ry, rw, rh, label, color=(0, 200, 255)):
        x1, y1 = ratio_to_px(rx, ry)
        x2, y2 = ratio_to_px(rx + rw, ry + rh)
        cv2.rectangle(overlay, (x1, y1), (x2, y2), color, 2)
        cv2.putText(overlay, label, (x1, y1 - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, color, 1)

    # 네비게이션 버튼
    draw_point(*UI["next_player"], "next_player", (0, 255, 0))
    draw_point(*UI["prev_player"], "prev_player", (0, 255, 0))
    draw_point(*UI["next_page"],   "next_page",   (0, 200, 255))
    draw_point(*UI["prev_page"],   "prev_page",   (0, 200, 255))
    draw_point(*UI["close"],       "close",        (0, 0, 255))

    # 선택된 모드에 따라 OCR 영역 표시
    mode_map = {
        'batter_p1':  (BATTER_P1,  (255, 200,   0)),
        'batter_p2':  (BATTER_P2,  (255, 150,  50)),
        'batter_p3':  (BATTER_P3,  (255, 100, 100)),
        'pitcher_p1': (PITCHER_P1, (  0, 200, 255)),
        'pitcher_p2': (PITCHER_P2, ( 50, 150, 255)),
        'pitcher_p3': (PITCHER_P3, (100, 100, 255)),
    }
    if mode in mode_map:
        regions, color = mode_map[mode]
        for key, (rx, ry, rw, rh) in regions.items():
            draw_region(rx, ry, rw, rh, key, color)

    # 반투명 합성
    result = cv2.addWeighted(overlay, 0.8, frame, 0.2, 0)
    return result


# ─── 메뉴 ─────────────────────────────────────────────────────────────
def print_menu():
    print("\n" + "="*50)
    print("  CPBV 좌표 보정 도구")
    print("="*50)
    print("  [1] 캡처 + 오버레이 - 타자 P1 (능력치)")
    print("  [2] 캡처 + 오버레이 - 타자 P2 (스킬)")
    print("  [3] 캡처 + 오버레이 - 타자 P3 (핫콜드존)")
    print("  [4] 캡처 + 오버레이 - 투수 P1 (능력치)")
    print("  [5] 캡처 + 오버레이 - 투수 P2 (스킬)")
    print("  [6] 캡처 + 오버레이 - 투수 P3 (체력바+구종)")
    print("  [7] 클릭 테스트 - 다음 선수 (next_player)")
    print("  [8] 클릭 테스트 - 다음 페이지 (next_page)")
    print("  [9] 클릭 테스트 - 이전 페이지 (prev_page)")
    print("  [t] 팀 로고 템플릿 저장")
    print("  [q] 종료")
    print("="*50)


def save_team_template(frame, team_name):
    """팀 로고 영역을 잘라서 템플릿으로 저장"""
    os.makedirs(TEAM_TEMPLATES_DIR, exist_ok=True)
    h, w = frame.shape[:2]
    rx, ry, rw, rh = BATTER_P1["team_logo"]
    x1, y1 = int(rx * w), int(ry * h)
    x2, y2 = int((rx + rw) * w), int((ry + rh) * h)
    logo = frame[y1:y2, x1:x2]
    path = os.path.join(TEAM_TEMPLATES_DIR, f"{team_name}.png")
    cv2.imwrite(path, logo)
    print(f"[+] 저장됨: {path} ({logo.shape})")


def main(args):
    print(f"[*] 스트림 연결: {args.stream}")
    cap = cv2.VideoCapture(args.stream)
    if not cap.isOpened():
        print("[!] 스트림 연결 실패")
        return

    mouse = MouseClient(args.mouse_host, MOUSE_PORT) if args.mouse_host else None

    os.makedirs("games/cpbv/calibration_output", exist_ok=True)

    while True:
        print_menu()
        choice = input("선택: ").strip().lower()

        frame = get_frame(cap)
        if frame is None:
            print("[!] 프레임 수신 실패")
            continue

        # 게임 창 크롭 시도
        cropped = crop_to_window(frame)

        if choice == '1':
            out = draw_ui_overlay(cropped, 'batter_p1')
            path = "games/cpbv/calibration_output/batter_p1.png"
            cv2.imwrite(path, out)
            print(f"[+] 저장: {path}")

        elif choice == '2':
            out = draw_ui_overlay(cropped, 'batter_p2')
            path = "games/cpbv/calibration_output/batter_p2.png"
            cv2.imwrite(path, out)
            print(f"[+] 저장: {path}")

        elif choice == '3':
            out = draw_ui_overlay(cropped, 'batter_p3')
            path = "games/cpbv/calibration_output/batter_p3.png"
            cv2.imwrite(path, out)
            print(f"[+] 저장: {path}")

        elif choice == '4':
            out = draw_ui_overlay(cropped, 'pitcher_p1')
            path = "games/cpbv/calibration_output/pitcher_p1.png"
            cv2.imwrite(path, out)
            print(f"[+] 저장: {path}")

        elif choice == '5':
            out = draw_ui_overlay(cropped, 'pitcher_p2')
            path = "games/cpbv/calibration_output/pitcher_p2.png"
            cv2.imwrite(path, out)
            print(f"[+] 저장: {path}")

        elif choice == '6':
            out = draw_ui_overlay(cropped, 'pitcher_p3')
            path = "games/cpbv/calibration_output/pitcher_p3.png"
            cv2.imwrite(path, out)
            print(f"[+] 저장: {path}")

        elif choice == '7':
            if mouse:
                mouse.click_ratio(*UI["next_player"])
            else:
                print("[!] --mouse-host 필요")

        elif choice == '8':
            if mouse:
                mouse.click_ratio(*UI["next_page"])
            else:
                print("[!] --mouse-host 필요")

        elif choice == '9':
            if mouse:
                mouse.click_ratio(*UI["prev_page"])
            else:
                print("[!] --mouse-host 필요")

        elif choice == 't':
            print(f"지원 팀: {', '.join(TEAMS)}")
            team = input("저장할 팀 이름: ").strip()
            if team in TEAMS:
                save_team_template(cropped, team)
            else:
                print(f"[!] 지원하지 않는 팀: {team}")

        elif choice == 'q':
            break

    cap.release()
    print("[*] 종료")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CPBV 좌표 보정 도구')
    parser.add_argument('--stream', default=STREAM_URL)
    parser.add_argument('--mouse-host', default=MOUSE_HOST)
    args = parser.parse_args()
    main(args)
