"""
CPBV 좌표 보정 GUI (인터랙티브)

사용법:
  python games/cpbv/calibrate_gui.py

조작:
  마우스 드래그       : 영역 이동
  모서리 핸들 드래그  : 영역 리사이즈
  클릭 포인트 드래그  : 버튼 위치 이동
  N / TAB             : 다음 모드
  P                   : 이전 모드
  R                   : 프레임 새로고침
  L                   : 라이브 모드 토글
  S                   : 저장 (config_override.json)
  Q / ESC             : 종료
"""
import cv2
import sys
import os
import json
import copy
import numpy as np
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from games.cpbv.config_cpbv import (
    STREAM_URL, MOUSE_HOST, MOUSE_PORT,
    STREAM_WIDTH, STREAM_HEIGHT,
    STREAM_GAME_X1, STREAM_GAME_Y1, STREAM_GAME_X2, STREAM_GAME_Y2,
    WINDOW_LEFT, WINDOW_TOP, WINDOW_WIDTH, WINDOW_HEIGHT,
    BATTER_P1, BATTER_P2, BATTER_P3,
    PITCHER_P1, PITCHER_P2, PITCHER_P3, UI
)

OVERRIDE_PATH = os.path.join(os.path.dirname(__file__), 'config_override.json')

GAME_W = STREAM_GAME_X2 - STREAM_GAME_X1
GAME_H = STREAM_GAME_Y2 - STREAM_GAME_Y1

MODES = [
    ('batter_p1',  '타자 P1 - 능력치'),
    ('batter_p2',  '타자 P2 - 스킬'),
    ('batter_p3',  '타자 P3 - 핫콜드존'),
    ('pitcher_p1', '투수 P1 - 능력치'),
    ('pitcher_p2', '투수 P2 - 스킬'),
    ('pitcher_p3', '투수 P3 - 체력+구종'),
]

MODE_COLORS = {
    'batter_p1':  (0, 220, 180),
    'batter_p2':  (0, 165, 255),
    'batter_p3':  (0, 80,  255),
    'pitcher_p1': (255, 200,  0),
    'pitcher_p2': (200, 100, 50),
    'pitcher_p3': (180,  50, 255),
}

CLICK_COLORS = {
    'next_player': (50, 255, 50),
    'prev_player': (50, 200, 50),
    'next_page':   (50, 220, 255),
    'prev_page':   (50, 170, 200),
    'close':       (50,  50, 255),
}

HANDLE_R = 6   # 코너 핸들 반지름
DOT_R    = 8   # 클릭 포인트 반지름


# ─── 좌표 변환 ────────────────────────────────────────────────────────
def r2s(rx, ry, rw=0, rh=0):
    """게임창 비율 → 스트림 픽셀"""
    sx = STREAM_GAME_X1 + rx * GAME_W
    sy = STREAM_GAME_Y1 + ry * GAME_H
    return int(sx), int(sy), int(rw * GAME_W), int(rh * GAME_H)

def s2r(sx, sy):
    """스트림 픽셀 → 게임창 비율"""
    rx = (sx - STREAM_GAME_X1) / max(GAME_W, 1)
    ry = (sy - STREAM_GAME_Y1) / max(GAME_H, 1)
    return round(rx, 4), round(ry, 4)

def rect_px(rx, ry, rw, rh):
    """비율 4-tuple → (x1,y1,x2,y2) 픽셀"""
    sx, sy, sw, sh = r2s(rx, ry, rw, rh)
    return sx, sy, sx + sw, sy + sh


# ─── GUI 클래스 ───────────────────────────────────────────────────────
class CalibrationGUI:
    def __init__(self, stream_url):
        self.stream_url = stream_url
        self.cap = None

        # 편집 가능한 region 딕셔너리 (비율값)
        self.regions = {
            'batter_p1':  dict(BATTER_P1),
            'batter_p2':  dict(BATTER_P2),
            'batter_p3':  dict(BATTER_P3),
            'pitcher_p1': dict(PITCHER_P1),
            'pitcher_p2': dict(PITCHER_P2),
            'pitcher_p3': dict(PITCHER_P3),
        }
        self.click_pts = dict(UI)

        # 저장된 오버라이드 로드
        self._load_override()

        self.mode_idx = 0
        self.frame    = None
        self.live     = False

        # 드래그 상태
        self.selected   = None   # ('region', mode, key) | ('click', key) | None
        self.drag_start = None   # (x, y) 시작점
        self.drag_type  = None   # 'move' | 'tl' | 'tr' | 'bl' | 'br'
        self.drag_orig  = None   # 드래그 시작 시의 원본 값
        self.mx, self.my = 0, 0

    # ── 속성 ──────────────────────────────────────────────────────────
    @property
    def mode(self):
        return MODES[self.mode_idx][0]

    @property
    def mode_label(self):
        return MODES[self.mode_idx][1]

    # ── 오버라이드 I/O ────────────────────────────────────────────────
    def _load_override(self):
        if not os.path.exists(OVERRIDE_PATH):
            return
        with open(OVERRIDE_PATH, encoding='utf-8') as f:
            data = json.load(f)
        for mode_key, rgs in data.get('regions', {}).items():
            if mode_key in self.regions:
                self.regions[mode_key].update({k: tuple(v) for k, v in rgs.items()})
        self.click_pts.update({k: tuple(v) for k, v in data.get('click_pts', {}).items()})
        print(f"[+] 오버라이드 로드: {OVERRIDE_PATH}")

    def save(self):
        data = {
            'regions':    {m: {k: list(v) for k, v in rgs.items()}
                           for m, rgs in self.regions.items()},
            'click_pts':  {k: list(v) for k, v in self.click_pts.items()},
        }
        with open(OVERRIDE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[+] 저장됨: {OVERRIDE_PATH}")

    # ── 스트림 ────────────────────────────────────────────────────────
    def _connect(self):
        self.cap = cv2.VideoCapture(self.stream_url)
        if not self.cap.isOpened():
            print(f"[!] 스트림 연결 실패: {self.stream_url}")
            return False
        print(f"[+] 스트림 연결: {self.stream_url}")
        return True

    def grab_frame(self):
        if self.cap is None or not self.cap.isOpened():
            return False
        ret, frame = self.cap.read()
        if ret:
            self.frame = frame.copy()
        return ret

    # ── 히트 테스트 ───────────────────────────────────────────────────
    def _corner_hit(self, mx, my, x1, y1, x2, y2):
        for tag, (cx, cy) in [('tl',(x1,y1)),('tr',(x2,y1)),('bl',(x1,y2)),('br',(x2,y2))]:
            if abs(mx - cx) <= HANDLE_R + 3 and abs(my - cy) <= HANDLE_R + 3:
                return tag
        return None

    def _find_hit(self, mx, my):
        """마우스 위치에 있는 요소 탐색"""
        # 클릭 포인트
        for key, (rx, ry) in self.click_pts.items():
            sx, sy, _, _ = r2s(rx, ry)
            if abs(mx - sx) <= DOT_R + 2 and abs(my - sy) <= DOT_R + 2:
                return ('click', key), 'move'

        # 현재 모드 영역 (코너 우선)
        for key, region in self.regions[self.mode].items():
            x1, y1, x2, y2 = rect_px(*region)
            corner = self._corner_hit(mx, my, x1, y1, x2, y2)
            if corner:
                return ('region', self.mode, key), corner
        for key, region in self.regions[self.mode].items():
            x1, y1, x2, y2 = rect_px(*region)
            if x1 <= mx <= x2 and y1 <= my <= y2:
                return ('region', self.mode, key), 'move'
        return None, None

    # ── 마우스 콜백 ───────────────────────────────────────────────────
    def on_mouse(self, event, x, y, flags, param):
        self.mx, self.my = x, y

        if event == cv2.EVENT_LBUTTONDOWN:
            sel, dtype = self._find_hit(x, y)
            self.selected   = sel
            self.drag_type  = dtype
            self.drag_start = (x, y)
            if sel:
                if sel[0] == 'click':
                    self.drag_orig = self.click_pts[sel[1]]
                else:
                    self.drag_orig = self.regions[sel[1]][sel[2]]

        elif event == cv2.EVENT_MOUSEMOVE and self.drag_start and self.selected:
            dx = x - self.drag_start[0]
            dy = y - self.drag_start[1]
            drx = dx / max(GAME_W, 1)
            dry = dy / max(GAME_H, 1)

            if self.selected[0] == 'click':
                key = self.selected[1]
                ox, oy = self.drag_orig
                self.click_pts[key] = (round(ox + drx, 4), round(oy + dry, 4))

            elif self.selected[0] == 'region':
                mode, key = self.selected[1], self.selected[2]
                orx, ory, orw, orh = self.drag_orig
                t = self.drag_type
                if   t == 'move': self.regions[mode][key] = (round(orx+drx,4), round(ory+dry,4), orw, orh)
                elif t == 'tl':   self.regions[mode][key] = (round(orx+drx,4), round(ory+dry,4), round(orw-drx,4), round(orh-dry,4))
                elif t == 'tr':   self.regions[mode][key] = (orx, round(ory+dry,4), round(orw+drx,4), round(orh-dry,4))
                elif t == 'bl':   self.regions[mode][key] = (round(orx+drx,4), ory, round(orw-drx,4), round(orh+dry,4))
                elif t == 'br':   self.regions[mode][key] = (orx, ory, round(orw+drx,4), round(orh+dry,4))

        elif event == cv2.EVENT_LBUTTONUP:
            self.drag_start = None

    # ── 렌더링 ────────────────────────────────────────────────────────
    def _draw(self):
        if self.frame is not None:
            img = self.frame.copy()
        else:
            img = np.zeros((STREAM_HEIGHT, STREAM_WIDTH, 3), dtype=np.uint8)
            cv2.putText(img, "No frame - press R to capture",
                        (STREAM_WIDTH//2 - 140, STREAM_HEIGHT//2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150,150,150), 1)

        h, w = img.shape[:2]
        color = MODE_COLORS[self.mode]

        # 게임창 경계
        cv2.rectangle(img, (STREAM_GAME_X1, STREAM_GAME_Y1),
                           (STREAM_GAME_X2, STREAM_GAME_Y2), (80, 80, 80), 1)

        # 영역 박스
        for key, region in self.regions[self.mode].items():
            x1, y1, x2, y2 = rect_px(*region)
            is_sel = (self.selected == ('region', self.mode, key))
            c = (0, 255, 255) if is_sel else color
            thick = 2 if is_sel else 1
            cv2.rectangle(img, (x1, y1), (x2, y2), c, thick)
            cv2.putText(img, key, (x1+2, max(y1-3, 10)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.3, c, 1)
            # 코너 핸들
            for cx, cy in [(x1,y1),(x2,y1),(x1,y2),(x2,y2)]:
                cv2.rectangle(img, (cx-HANDLE_R, cy-HANDLE_R),
                                   (cx+HANDLE_R, cy+HANDLE_R), c, -1)

        # 클릭 포인트
        for key, (rx, ry) in self.click_pts.items():
            sx, sy, _, _ = r2s(rx, ry)
            is_sel = (self.selected == ('click', key))
            c = (0, 255, 255) if is_sel else CLICK_COLORS.get(key, (200,200,200))
            cv2.circle(img, (sx, sy), DOT_R, c, -1)
            cv2.circle(img, (sx, sy), DOT_R+2, (0,0,0), 1)
            cv2.putText(img, key, (sx+10, sy+4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.3, c, 1)

        # ── HUD ────────────────────────────────────────────────────────
        # 상단바
        live_mark = "[LIVE]" if self.live else ""
        hud = (f"[{self.mode_idx+1}/{len(MODES)}] {self.mode_label}  {live_mark}"
               f"  |  N/TAB:다음  P:이전  R:새로고침  L:라이브  S:저장  Q:종료")
        cv2.rectangle(img, (0, 0), (w, 20), (20, 20, 20), -1)
        cv2.putText(img, hud, (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (230,230,230), 1)

        # 하단바 (선택된 요소 좌표)
        if self.selected:
            if self.selected[0] == 'click':
                val = self.click_pts[self.selected[1]]
                info = f"  [{self.selected[1]}]  rx={val[0]:.4f}  ry={val[1]:.4f}"
            else:
                val = self.regions[self.selected[1]][self.selected[2]]
                info = (f"  [{self.selected[2]}]  "
                        f"rx={val[0]:.4f}  ry={val[1]:.4f}  "
                        f"rw={val[2]:.4f}  rh={val[3]:.4f}")
            cv2.rectangle(img, (0, h-20), (w, h), (20, 20, 20), -1)
            cv2.putText(img, info, (4, h-5), cv2.FONT_HERSHEY_SIMPLEX, 0.37, (255,255,100), 1)

        return img

    # ── 메인 루프 ─────────────────────────────────────────────────────
    def run(self):
        if not self._connect():
            return

        win = 'CPBV Calibration GUI'
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, STREAM_WIDTH, STREAM_HEIGHT)
        cv2.setMouseCallback(win, self.on_mouse)

        # 초기 프레임
        self.grab_frame()
        print("[*] GUI 시작. 드래그로 영역 조정 → S로 저장")

        while True:
            if self.live:
                self.grab_frame()

            cv2.imshow(win, self._draw())
            key = cv2.waitKey(16) & 0xFF   # ~60fps

            if key in (ord('q'), 27):      # Q or ESC
                break
            elif key in (ord('n'), 9):     # N or TAB
                self.mode_idx = (self.mode_idx + 1) % len(MODES)
                print(f"  → {self.mode_label}")
            elif key == ord('p'):
                self.mode_idx = (self.mode_idx - 1) % len(MODES)
                print(f"  → {self.mode_label}")
            elif key == ord('r'):
                ok = self.grab_frame()
                print(f"  {'프레임 갱신' if ok else '[!] 캡처 실패 - 스트림 확인'}")
            elif key == ord('l'):
                self.live = not self.live
                print(f"  라이브: {'ON' if self.live else 'OFF'}")
            elif key == ord('s'):
                self.save()

        self.cap.release()
        cv2.destroyAllWindows()
        print("[*] 종료")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stream', default=STREAM_URL)
    args = parser.parse_args()
    CalibrationGUI(args.stream).run()
