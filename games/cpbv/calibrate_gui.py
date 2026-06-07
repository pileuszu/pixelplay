"""
CPBV 좌표 보정 GUI (인터랙티브)

조작:
  마우스 드래그       : 영역 이동
  모서리 핸들 드래그  : 영역 리사이즈
  N / TAB             : 다음 모드
  P                   : 이전 모드
  R                   : 프레임 새로고침
  L                   : 라이브 모드 토글
  A                   : OCR 자동 영역 감지
  S                   : 저장 (config_override.json)
  Q / ESC             : 종료
"""
import cv2
import sys
import os
import json
import copy
import time
import threading
import socket
import numpy as np
import argparse

# PIL for Korean text rendering
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
    # Windows Korean font
    _FONT_PATHS = [
        "C:/Windows/Fonts/malgun.ttf",          # 맑은 고딕 (Windows)
        "C:/Windows/Fonts/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",  # Linux
    ]
    _FONT_CACHE = {}
    def _get_font(size=13):
        if size in _FONT_CACHE:
            return _FONT_CACHE[size]
        for path in _FONT_PATHS:
            if os.path.exists(path):
                try:
                    f = ImageFont.truetype(path, size)
                    _FONT_CACHE[size] = f
                    return f
                except:
                    pass
        f = ImageFont.load_default()
        _FONT_CACHE[size] = f
        return f
except ImportError:
    PIL_AVAILABLE = False

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
TEAM_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'team_templates')

TEAMS = [
    ('1', '두산'), ('2', '삼성'), ('3', '한화'), ('4', '롯데'),
    ('5', 'KIA'),    ('6', '키움'), ('7', 'SSG'),    ('8', 'LG'),
    ('9', 'NC'),     ('0', 'KT'),
]

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
    'batter_p1':  (0, 220, 180), 'batter_p2':  (0, 165, 255), 'batter_p3': (0, 80, 255),
    'pitcher_p1': (255, 200,  0), 'pitcher_p2': (200, 100, 50),'pitcher_p3':(180, 50,255),
}
CLICK_COLORS = {
    'next_player':(50,255,50), 'prev_player':(50,200,50),
    'next_page':(50,220,255),  'prev_page':(50,170,200), 'close':(50,50,255),
}
HANDLE_R = 6
DOT_R    = 8


# ─── 마우스 클라이언트 ────────────────────────────────────────────────
class MouseClient:
    def __init__(self, host, port):
        self.host = host; self.port = port
        self.sock = None; self.connected = False
        self._try_connect()

    def _try_connect(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((self.host, self.port))
            self.sock = s; self.connected = True
            print(f"[+] 마우스 서버 연결: {self.host}:{self.port}")
        except Exception as e:
            print(f"[!] 마우스 연결 실패: {e}")
            self.connected = False

    def _send(self, cmd):
        if not self.sock: return
        try:
            self.sock.settimeout(2.0)
            self.sock.sendall((json.dumps(cmd) + '\n').encode())
            self.sock.recv(1024)
        except:
            self.connected = False; self.sock = None

    def click_ratio(self, rx, ry, focus_first=True):
        if not self.connected: self._try_connect()
        abs_x = int(WINDOW_LEFT + rx * WINDOW_WIDTH)
        abs_y = int(WINDOW_TOP  + ry * WINDOW_HEIGHT)
        if focus_first:
            # 클릭 대신 win32 API로 창 포커스 (게임 상태 변경 없음)
            cx = int(WINDOW_LEFT + WINDOW_WIDTH  * 0.5)
            cy = int(WINDOW_TOP  + WINDOW_HEIGHT * 0.5)
            self._send({'action': 'focus_window', 'x': cx, 'y': cy})
            time.sleep(0.15)
        self._send({'action': 'click', 'x': abs_x, 'y': abs_y})
        return abs_x, abs_y



# ─── 스트림 스레드 (항상 최신 프레임 유지) ─────────────────────
class StreamThread(threading.Thread):
    """
    MJPEG 네트워크 스트림에서 항상 최신 프레임만 유지하는 백그라운드 스레드.
    OpenCV cap.read() 버퍼 상충 문제 해결.
    """
    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url
        self._frame = None
        self._lock  = threading.Lock()
        self._stop  = threading.Event()
        self.ok     = False

    def run(self):
        cap = cv2.VideoCapture(self.url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.ok = cap.isOpened()
        if not self.ok:
            print(f"[!] 스트림 연결 실패: {self.url}")
            return
        print(f"[+] 스트림 스레드 시작: {self.url}")
        while not self._stop.is_set():
            ret, frame = cap.read()
            if ret:
                with self._lock:
                    self._frame = frame
            else:
                time.sleep(0.05)
        cap.release()

    def get_latest(self):
        """최신 프레임 반환 (없으면 None)"""
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def stop(self):
        self._stop.set()


# ─── OCR 자동 감지 규칙 ──────────────────────────────────────────────
# {찾을 텍스트: {모드: (region_key, bbox_기준_x비율_오프셋, y오프셋, w, h)}}
# 오프셋은 감지된 텍스트 박스의 (x1, y1) 기준 (게임창 비율 단위)
OCR_RULES = {

    '세트덱': {
        'batter_p1':  ('setdeck_area',   0.28, -0.005, 0.12, 0.05),
        'pitcher_p1': ('setdeck_area',   0.28, -0.005, 0.12, 0.05),
    },
    '평균': {
        'batter_p1':  ('launch_area',    0.45,  0.005, 0.14, 0.05),
    },
    '현재': {
        'pitcher_p1': ('stamina_bar',    0.25,  0.005, 0.45, 0.03),
    },
    '파워': {
        'batter_p1':  ('stat_power',     0.28,  0.002, 0.14, 0.04),
    },
    '인내': {
        'batter_p1':  ('stat_endure',    0.28,  0.002, 0.14, 0.04),
    },
    '정확': {
        'batter_p1':  ('stat_contact',   0.28,  0.002, 0.14, 0.04),
    },
    '주루': {
        'batter_p1':  ('stat_run',       0.28,  0.002, 0.14, 0.04),
    },
    '선구': {
        'batter_p1':  ('stat_eye',       0.28,  0.002, 0.14, 0.04),
    },
    '구속': {
        'pitcher_p1': ('stat_speed',     0.28,  0.002, 0.14, 0.04),
    },
    '제구': {
        'pitcher_p1': ('stat_control',   0.28,  0.002, 0.14, 0.04),
    },
    '변화': {
        'pitcher_p1': ('stat_break',     0.28,  0.002, 0.14, 0.04),
    },
    '지구력': {
        'pitcher_p1': ('stat_stamina',   0.28,  0.002, 0.14, 0.04),
    },
    '구위': {
        'pitcher_p1': ('stat_stuff',     0.28,  0.002, 0.14, 0.04),
    },
    '수비': {
        'batter_p1':  ('stat_defense',   0.28,  0.002, 0.14, 0.04),
        'pitcher_p1': ('stat_defense',   0.28,  0.002, 0.14, 0.04),
    },
    '핫': {
        'batter_p3':  ('hotzone_grid',   -0.05, 0.03,  0.70, 0.30),
    },
    '구종': {
        'pitcher_p3': ('pitches_area',   -0.05, 0.03,  0.85, 0.32),
    },
    '체력': {
        'pitcher_p3': ('stamina_bar_detail', 0.10, 0.03, 0.75, 0.04),
    },
}


# ─── 좌표 변환 ────────────────────────────────────────────────────────
def r2s(rx, ry, rw=0, rh=0):
    sx = STREAM_GAME_X1 + rx * GAME_W
    sy = STREAM_GAME_Y1 + ry * GAME_H
    return int(sx), int(sy), int(rw * GAME_W), int(rh * GAME_H)

def rect_px(rx, ry, rw, rh):
    sx, sy, sw, sh = r2s(rx, ry, rw, rh)
    return sx, sy, sx+sw, sy+sh


# ─── PIL 텍스트 렌더링 ────────────────────────────────────────────────
def draw_text_pil(img, lines_with_info, padding=4):
    """
    img: BGR numpy array
    lines_with_info: list of (text, x, y, size, color_bgr, bg_color_bgr_or_None)
    """
    if not PIL_AVAILABLE:
        # fallback: ASCII only
        for (text, x, y, size, color, bg) in lines_with_info:
            scale = size / 25
            cv2.putText(img, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1)
        return img

    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil)

    for (text, x, y, size, color_bgr, bg_bgr) in lines_with_info:
        font = _get_font(size)
        rgb = (color_bgr[2], color_bgr[1], color_bgr[0])
        if bg_bgr is not None:
            try:
                bbox = draw.textbbox((x, y), text, font=font)
            except AttributeError:
                tw, th = draw.textsize(text, font=font)
                bbox = (x, y, x+tw, y+th)
            draw.rectangle([bbox[0]-padding, bbox[1]-2, bbox[2]+padding, bbox[3]+2],
                           fill=(bg_bgr[2], bg_bgr[1], bg_bgr[0]))
        draw.text((x, y), text, font=font, fill=rgb)

    return cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)


# ─── GUI ─────────────────────────────────────────────────────────────
class CalibrationGUI:
    def __init__(self, stream_url):
        self.stream_url = stream_url
        self.stream = StreamThread(stream_url)
        self.stream.start()
        self.frame  = None
        self.live   = True   # 기본 라이브 ON

        self.regions = {
            'batter_p1':  dict(BATTER_P1),  'batter_p2':  dict(BATTER_P2),
            'batter_p3':  dict(BATTER_P3),  'pitcher_p1': dict(PITCHER_P1),
            'pitcher_p2': dict(PITCHER_P2), 'pitcher_p3': dict(PITCHER_P3),
        }
        self.click_pts = dict(UI)
        self._load_override()

        self.mode_idx  = 0
        self.selected  = None
        self.drag_start= None
        self.drag_type = None
        self.drag_orig = None
        self.mx = self.my = 0

        # OCR 관련
        self.ocr_boxes   = []
        self.ocr_status  = ''
        self.ocr_running = False

        # E키 추출 결과: {key: text}
        self.extract_results = {}
        self.extract_running = False
        self.extract_status  = ''

        # 마우스 클라이언트
        self.mouse = MouseClient(MOUSE_HOST, MOUSE_PORT) if MOUSE_HOST else None
        self.last_click_label = ''

        # 로고 저장 모드
        self.logo_save_mode = False

    # ── 속성 ──────────────────────────────────────────────────────────
    @property
    def mode(self):
        return MODES[self.mode_idx][0]

    @property
    def mode_label(self):
        return MODES[self.mode_idx][1]

    # ── 오버라이드 I/O ────────────────────────────────────────────────
    def _load_override(self):
        if not os.path.exists(OVERRIDE_PATH): return
        with open(OVERRIDE_PATH, encoding='utf-8') as f:
            data = json.load(f)
        for mk, rgs in data.get('regions', {}).items():
            if mk in self.regions:
                self.regions[mk].update({k: tuple(v) for k, v in rgs.items()})
        self.click_pts.update({k: tuple(v) for k, v in data.get('click_pts', {}).items()})
        print(f"[+] 오버라이드 로드됨")

    def save(self):
        data = {
            'regions':   {m: {k: list(v) for k, v in rgs.items()} for m, rgs in self.regions.items()},
            'click_pts': {k: list(v) for k, v in self.click_pts.items()},
        }
        with open(OVERRIDE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[+] 저장됨: {OVERRIDE_PATH}")

    # ── 스트림 ────────────────────────────────────────────────
    def grab_frame(self):
        f = self.stream.get_latest()
        if f is not None:
            self.frame = f
            return True
        return False


    # ── 히트 테스트 ───────────────────────────────────────────────────
    def _corner_hit(self, mx, my, x1, y1, x2, y2):
        for tag, (cx, cy) in [('tl',(x1,y1)),('tr',(x2,y1)),('bl',(x1,y2)),('br',(x2,y2))]:
            if abs(mx-cx) <= HANDLE_R+3 and abs(my-cy) <= HANDLE_R+3:
                return tag
        return None

    def _find_hit(self, mx, my):
        for key, (rx, ry) in self.click_pts.items():
            sx, sy, _, _ = r2s(rx, ry)
            if abs(mx-sx) <= DOT_R+2 and abs(my-sy) <= DOT_R+2:
                return ('click', key), 'move'
        for key, region in self.regions[self.mode].items():
            x1, y1, x2, y2 = rect_px(*region)
            corner = self._corner_hit(mx, my, x1, y1, x2, y2)
            if corner: return ('region', self.mode, key), corner
        for key, region in self.regions[self.mode].items():
            x1, y1, x2, y2 = rect_px(*region)
            if x1 <= mx <= x2 and y1 <= my <= y2:
                return ('region', self.mode, key), 'move'
        return None, None

    # ── 마우스 콜백 ───────────────────────────────────────────────────
    def on_mouse(self, event, x, y, flags, param):
        self.mx, self.my = x, y

        if event == cv2.EVENT_LBUTTONDOWN:
            self.selected, self.drag_type = self._find_hit(x, y)
            self.drag_start = (x, y)
            if self.selected:
                if self.selected[0] == 'click':
                    self.drag_orig = self.click_pts[self.selected[1]]
                else:
                    self.drag_orig = self.regions[self.selected[1]][self.selected[2]]

        elif event == cv2.EVENT_RBUTTONDOWN:
            # 우클릭: 클릭 포인트 위에서 클릭 테스트
            hit, _ = self._find_hit(x, y)
            if hit and hit[0] == 'click':
                key = hit[1]
                rx, ry = self.click_pts[key]
                self._test_click(key, rx, ry)

        elif event == cv2.EVENT_MOUSEMOVE and self.drag_start and self.selected:
            dx, dy = x - self.drag_start[0], y - self.drag_start[1]
            drx, dry = dx / max(GAME_W,1), dy / max(GAME_H,1)

            if self.selected[0] == 'click':
                ox, oy = self.drag_orig
                self.click_pts[self.selected[1]] = (round(ox+drx,4), round(oy+dry,4))
            else:
                mode, key = self.selected[1], self.selected[2]
                orx, ory, orw, orh = self.drag_orig
                t = self.drag_type
                if   t=='move': self.regions[mode][key]=(round(orx+drx,4),round(ory+dry,4),orw,orh)
                elif t=='tl':   self.regions[mode][key]=(round(orx+drx,4),round(ory+dry,4),round(orw-drx,4),round(orh-dry,4))
                elif t=='tr':   self.regions[mode][key]=(orx,round(ory+dry,4),round(orw+drx,4),round(orh-dry,4))
                elif t=='bl':   self.regions[mode][key]=(round(orx+drx,4),ory,round(orw-drx,4),round(orh+dry,4))
                elif t=='br':   self.regions[mode][key]=(orx,ory,round(orw+drx,4),round(orh+dry,4))

        elif event == cv2.EVENT_LBUTTONUP:
            self.drag_start = None

    def _test_click(self, label, rx, ry):
        """게임 PC에 클릭 전송"""
        if not self.mouse:
            print("[!] 마우스 클라이언트 없음")
            return
        ax, ay = self.mouse.click_ratio(rx, ry, focus_first=True)
        self.last_click_label = f"{label} → ({ax},{ay})"
        print(f"  [클릭] {self.last_click_label}")



    # ── OCR 자동 감지 ─────────────────────────────────────────────────
    def start_ocr_detect(self):
        if self.ocr_running:
            print("[!] OCR 이미 실행 중")
            return
        if self.frame is None:
            print("[!] 먼저 R키로 프레임 캡처")
            return

        def _run():
            self.ocr_running = True
            self.ocr_status = 'OCR 실행 중...'
            self.ocr_boxes = []
            try:
                import easyocr
                reader = easyocr.Reader(['ko', 'en'], verbose=False)
                gf = self.frame[STREAM_GAME_Y1:STREAM_GAME_Y2,
                                STREAM_GAME_X1:STREAM_GAME_X2]
                results = reader.readtext(gf, detail=1)

                detected = []
                auto_set = []

                for bbox, text, conf in results:
                    if conf < 0.3: continue
                    x1g = int(bbox[0][0]); y1g = int(bbox[0][1])
                    x2g = int(bbox[2][0]); y2g = int(bbox[2][1])
                    # 스트림 픽셀로 변환 (게임창 오프셋 추가)
                    detected.append((x1g, y1g, x2g, y2g, text, conf))

                    # 규칙 매칭
                    for keyword, mode_map in OCR_RULES.items():
                        if keyword in text and self.mode in mode_map:
                            key, ox, oy, rw, rh = mode_map[self.mode]
                            rx_label = x1g / max(GAME_W, 1)
                            ry_label = y1g / max(GAME_H, 1)
                            new_rx = round(rx_label + ox, 4)
                            new_ry = round(ry_label + oy, 4)
                            self.regions[self.mode][key] = (new_rx, new_ry,
                                                             round(rw,4), round(rh,4))
                            auto_set.append(f"{key}: ({new_rx:.3f},{new_ry:.3f})")

                self.ocr_boxes = detected
                self.ocr_status = f"완료: {len(detected)}개 텍스트, {len(auto_set)}개 자동 설정"
                for s in auto_set:
                    print(f"  [자동] {s}")
                print(f"[+] OCR: {self.ocr_status}")

            except ImportError:
                self.ocr_status = 'easyocr 없음 (pip install easyocr)'
                print(f"[!] {self.ocr_status}")
            except Exception as e:
                self.ocr_status = f'오류: {e}'
                print(f"[!] OCR 오류: {e}")
            finally:
                self.ocr_running = False

        threading.Thread(target=_run, daemon=True).start()

    # ── OCR 영역별 추출 테스트 (E키) ─────────────────────────────────
    def _match_team_logo(self, crop):
        """스트림 필레임 크롭에 저장된 팀 로고 템플릿 매칭"""
        tmpl_dir = os.path.join(os.path.dirname(__file__), 'team_templates')
        if not os.path.exists(tmpl_dir):
            return None, 0.0, "team_templates 폴더 없음"

        templates = [f for f in os.listdir(tmpl_dir) if f.endswith('.png')]
        if not templates:
            return None, 0.0, "저장된 템플릿 없음 (calibrate.py [t] 사용)"

        best_score = -1.0
        best_team  = None

        for fname in templates:
            team = os.path.splitext(fname)[0]
            tmpl = cv2.imread(os.path.join(tmpl_dir, fname))
            if tmpl is None: continue

            th, tw = tmpl.shape[:2]
            ch, cw = crop.shape[:2]

            # 템플릿이 크롭보다 크면 크롭 크기에 맞춰 축소
            if tw > cw or th > ch:
                scale = min(cw / max(tw,1), ch / max(th,1))
                tmpl  = cv2.resize(tmpl, (max(1,int(tw*scale)), max(1,int(th*scale))))
                th, tw = tmpl.shape[:2]

            if tw < 4 or th < 4 or tw > cw or th > ch:
                continue

            res = cv2.matchTemplate(crop, tmpl, cv2.TM_CCOEFF_NORMED)
            _, mv, _, _ = cv2.minMaxLoc(res)
            if mv > best_score:
                best_score = mv
                best_team  = team

        if best_team and best_score >= 0.5:
            return best_team, best_score, None
        elif best_team:
            return best_team, best_score, f"(저신도 낙음: {best_score:.0%})"
        return None, 0.0, "(인식 실패)"

    def _save_team_logo(self, team_name):
        """team_logo 영역 크롭을 team_templates/팀이름.png로 저장"""
        region = self.regions.get(self.mode, {}).get('team_logo')
        if region is None:
            # 어떤 모드든 team_logo가 있으면 사용
            for mode_key, regs in self.regions.items():
                if 'team_logo' in regs:
                    region = regs['team_logo']
                    break
        if region is None:
            print("[!] team_logo 영역 미정의"); return
        if self.frame is None:
            print("[!] 프레임 없음 (R키 먼저)"); return

        x1, y1, x2, y2 = rect_px(*region)
        fh, fw = self.frame.shape[:2]
        x1c, y1c = max(0,x1), max(0,y1)
        x2c, y2c = min(fw,x2), min(fh,y2)
        if x2c <= x1c or y2c <= y1c:
            print("[!] 영역 범위 오류"); return

        crop = self.frame[y1c:y2c, x1c:x2c]
        os.makedirs(TEAM_TEMPLATES_DIR, exist_ok=True)
        path = os.path.join(TEAM_TEMPLATES_DIR, f"{team_name}.png")
        cv2.imwrite(path, crop)
        print(f"[+] 팀 로고 저장: {path}  ({crop.shape[1]}×{crop.shape[0]}px)")

    def start_ocr_extract(self):
        """현재 모드의 각 영역 크롭 → OCR → 터미널+GUI 표시"""
        if self.extract_running:
            print("[!] 이미 추출 중"); return
        if self.frame is None:
            print("[!] R키로 프레임 캡처 먼저"); return

        def _run():
            self.extract_running = True
            self.extract_status  = '추출 중...'
            self.extract_results = {}
            print(f"\n{'─'*52}")
            print(f"  OCR 추출 테스트 │ {self.mode_label}")
            print(f"{'─'*52}")
            try:
                import easyocr
                reader = easyocr.Reader(['ko', 'en'], verbose=False)
                regions = self.regions[self.mode]

                for key, region in regions.items():
                    x1, y1, x2, y2 = rect_px(*region)
                    # 프레임 범위 클램프
                    fh, fw = self.frame.shape[:2]
                    x1c, y1c = max(0,x1), max(0,y1)
                    x2c, y2c = min(fw,x2), min(fh,y2)
                    if x2c <= x1c or y2c <= y1c:
                        self.extract_results[key] = '(범위 오류)'
                        print(f"  {key:<22} : (범위 오류)")
                        continue

                    crop = self.frame[y1c:y2c, x1c:x2c]

                    # ─── 팀 로고: 템플릿 매칭 ───────────────────────────
                    if key == 'team_logo':
                        team, score, warn = self._match_team_logo(crop)
                        if team and score >= 0.5:
                            display = f"{team}  ({score:.0%})"
                            self.extract_results[key] = team
                        elif team:
                            display = f"{team}?  ({score:.0%}) ← 저신도 낙음"
                            self.extract_results[key] = team
                        else:
                            display = warn or '(인식 실패)'
                            self.extract_results[key] = ''
                        print(f"  {key:<22} : {display}")
                        continue

                    # ─── 일반 텍스트 영역: OCR ──────────────────────────
                    # 작은 크롭은 확대 (OCR 정확도 향상)
                    ch, cw = crop.shape[:2]
                    if cw < 60 or ch < 20:
                        scale = max(3, 60 // max(cw,1))
                        crop = cv2.resize(crop, (cw*scale, ch*scale),
                                          interpolation=cv2.INTER_CUBIC)

                    results = reader.readtext(crop, detail=1)
                    texts = [(t, c) for _, t, c in results if c > 0.25]
                    if texts:
                        val = ' | '.join(t for t, _ in texts)
                        conf_avg = sum(c for _, c in texts) / len(texts)
                        display  = f"{val}  ({conf_avg:.0%})"
                    else:
                        display = '(인식 안 됨)'

                    self.extract_results[key] = val if texts else ''
                    print(f"  {key:<22} : {display}")

                self.extract_status = f"완료 ({len(regions)}개 영역)"
                print(f"{'─'*52}\n")

            except ImportError:
                self.extract_status = 'easyocr 없음'
                print("[!] pip install easyocr")
            except Exception as e:
                self.extract_status = f'오류: {e}'
                print(f"[!] 추출 오류: {e}")
            finally:
                self.extract_running = False

        threading.Thread(target=_run, daemon=True).start()

    # ── 렌더링 ────────────────────────────────────────────────────────
    def _draw(self):
        if self.frame is not None:
            img = self.frame.copy()
        else:
            img = np.zeros((STREAM_HEIGHT, STREAM_WIDTH, 3), dtype=np.uint8)

        h, w = img.shape[:2]
        color = MODE_COLORS[self.mode]

        # 게임창 경계
        cv2.rectangle(img, (STREAM_GAME_X1, STREAM_GAME_Y1),
                           (STREAM_GAME_X2, STREAM_GAME_Y2), (80,80,80), 1)

        # OCR 감지 박스 (반투명)
        if self.ocr_boxes:
            overlay = img.copy()
            for (x1g, y1g, x2g, y2g, text, conf) in self.ocr_boxes:
                sx1 = STREAM_GAME_X1 + x1g; sy1 = STREAM_GAME_Y1 + y1g
                sx2 = STREAM_GAME_X1 + x2g; sy2 = STREAM_GAME_Y1 + y2g
                cv2.rectangle(overlay, (sx1,sy1),(sx2,sy2),(0,255,150),1)
            img = cv2.addWeighted(overlay, 0.6, img, 0.4, 0)

        # 현재 모드 영역
        for key, region in self.regions[self.mode].items():
            x1, y1, x2, y2 = rect_px(*region)
            is_sel = (self.selected == ('region', self.mode, key))
            c = (0,255,255) if is_sel else color
            thick = 2 if is_sel else 1
            cv2.rectangle(img,(x1,y1),(x2,y2),c,thick)
            for cx,cy in [(x1,y1),(x2,y1),(x1,y2),(x2,y2)]:
                cv2.rectangle(img,(cx-HANDLE_R,cy-HANDLE_R),(cx+HANDLE_R,cy+HANDLE_R),c,-1)

        # 클릭 포인트
        for key, (rx, ry) in self.click_pts.items():
            sx, sy, _, _ = r2s(rx, ry)
            is_sel = (self.selected == ('click', key))
            c = (0,255,255) if is_sel else CLICK_COLORS.get(key,(200,200,200))
            cv2.circle(img,(sx,sy),DOT_R,c,-1)
            cv2.circle(img,(sx,sy),DOT_R+2,(0,0,0),1)

        # ── 텍스트 오버레이 (PIL) ─────────────────────────────────────
        live_mark = '[라이브] ' if self.live else ''
        ocr_mark  = f'  | {self.ocr_status}' if self.ocr_status else ''

        # 상단 HUD
        hud_bg = (20,20,20)
        cv2.rectangle(img,(0,0),(w,22),hud_bg,-1)

        # 하단 HUD
        if self.selected:
            cv2.rectangle(img,(0,h-22),(w,h),hud_bg,-1)

        text_items = []

        # 마우스 연결 상태
        conn_color = (50,255,50) if (self.mouse and self.mouse.connected) else (50,50,255)
        conn_text  = 'MOUSE:OK' if (self.mouse and self.mouse.connected) else 'MOUSE:X'

        # 상단 텍스트
        extract_mark = f'  | E:{self.extract_status}' if self.extract_status else ''
        logo_mark = '  | [V:로고저장모드]' if self.logo_save_mode else ''
        hud_text = (f"{live_mark}[{self.mode_idx+1}/{len(MODES)}] {self.mode_label}"
                    f"  |  N:다음  P:이전  R:새로고침  L:라이브  A:OCR  E:추출  C:클리어  V:로고  S:저장"
                    f"  |  우클릭/T:클릭테스트  Q:종료"
                    f"{ocr_mark}{extract_mark}{logo_mark}")
        text_items.append((hud_text, 4, 3, 13, (230,230,230), None))
        # 연결 상태 (우상단)
        text_items.append((conn_text, w-80, 3, 12, conn_color, None))
        # 마지막 클릭 정보
        if self.last_click_label:
            text_items.append((f'클릭: {self.last_click_label}', 4, 25, 11, (200,255,100), None))

        # 영역 라벨 + 추출 결과
        for key, region in self.regions[self.mode].items():
            x1, y1, x2, y2 = rect_px(*region)
            is_sel = (self.selected == ('region', self.mode, key))
            c = (0,255,255) if is_sel else color
            # 영역 키 라벨
            text_items.append((key, x1+2, max(y1-14, 2), 11, c, None))
            # OCR 추출 결과 - 박스 안에 노란색으로
            if key in self.extract_results and self.extract_results[key]:
                val_text = self.extract_results[key]
                # 박스 안에 배경 채워서 텍스트 표시
                cv2.rectangle(img, (x1,y1), (x2,y2), (30,30,30), -1)  # 어두운 오버레이
                text_items.append((val_text, x1+3, y1+3, 11, (80,255,255), None))

        # 클릭 포인트 라벨
        for key, (rx, ry) in self.click_pts.items():
            sx, sy, _, _ = r2s(rx, ry)
            c = (0,255,255) if (self.selected == ('click', key)) else CLICK_COLORS.get(key,(200,200,200))
            text_items.append((key, sx+10, sy-5, 11, c, None))

        # OCR 박스 텍스트
        for (x1g, y1g, x2g, y2g, text, conf) in self.ocr_boxes:
            sx1 = STREAM_GAME_X1 + x1g; sy1 = STREAM_GAME_Y1 + y1g
            text_items.append((f"{text}({conf:.0%})", sx1, max(sy1-12,0), 10, (100,255,180), None))

        # 하단 선택 정보
        if self.selected:
            if self.selected[0] == 'click':
                val = self.click_pts[self.selected[1]]
                info = f"  [{self.selected[1]}]  rx={val[0]:.4f}  ry={val[1]:.4f}"
            else:
                val = self.regions[self.selected[1]][self.selected[2]]
                info = (f"  [{self.selected[2]}]  rx={val[0]:.4f}  ry={val[1]:.4f}"
                        f"  rw={val[2]:.4f}  rh={val[3]:.4f}")
            text_items.append((info, 4, h-19, 13, (255,255,100), None))

        # 팀 로고 저장 모드 오버레이
        if self.logo_save_mode:
            ow, oh = 280, 40 + len(TEAMS) * 22
            ox, oy = (w - ow) // 2, (h - oh) // 2
            cv2.rectangle(img, (ox-4, oy-4), (ox+ow+4, oy+oh+4), (0,180,255), 2)
            cv2.rectangle(img, (ox, oy), (ox+ow, oy+oh), (15,15,40), -1)
            text_items.append(('팀 로고 저장 — 번호 선택 (V:취소)', ox+8, oy+4, 14, (0,220,255), None))
            for i, (num, team) in enumerate(TEAMS):
                saved = os.path.exists(os.path.join(TEAM_TEMPLATES_DIR, f'{team}.png'))
                mark = ' ✔' if saved else ''
                text_items.append((f'  {num}: {team}{mark}', ox+8, oy+26+i*22, 13,
                                   (80,255,80) if saved else (255,220,80), None))

        img = draw_text_pil(img, text_items)
        return img

    # ── 메인 루프 ────────────────────────────────────────────────
    def run(self):
        # 스레드 시작 후 첫 프레임 기다리기
        print("[*] 스트림 연결 대기...")
        for _ in range(30):
            if self.stream.get_latest() is not None:
                break
            time.sleep(0.1)
        self.grab_frame()

        if not self.stream.ok:
            print("[!] 스트림 연결 실패 - 종료")
            return


        win = 'CPBV Calibration GUI'
        cv2.namedWindow(win, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(win, STREAM_WIDTH, STREAM_HEIGHT)
        cv2.setMouseCallback(win, self.on_mouse)

        print("[*] GUI 시작  |  드래그: 영역 편집  |  A: OCR 자동감지  |  S: 저장  |  L: 라이브 토글")

        while True:
            if self.live: self.grab_frame()
            cv2.imshow(win, self._draw())
            key = cv2.waitKey(16) & 0xFF

            if key in (ord('q'), 27):    break
            elif key in (ord('n'), 9):
                self.mode_idx = (self.mode_idx+1) % len(MODES)
                self.ocr_boxes = []; self.ocr_status = ''
                self.extract_results = {}; self.extract_status = ''
                print(f"  → {self.mode_label}")
            elif key == ord('p'):
                self.mode_idx = (self.mode_idx-1) % len(MODES)
                self.ocr_boxes = []; self.ocr_status = ''
                self.extract_results = {}; self.extract_status = ''
                print(f"  → {self.mode_label}")
            elif key == ord('r'):
                ok = self.grab_frame()
                print(f"  {'프레임 갱신' if ok else '[!] 캡처 실패'}")
            elif key == ord('l'):
                self.live = not self.live
                print(f"  라이브: {'ON' if self.live else 'OFF'}")
            elif key == ord('a'):
                self.start_ocr_detect()
            elif key == ord('e'):
                self.start_ocr_extract()
            elif key == ord('s'):
                self.save()
            elif key == ord('t'):
                # T: 선택된 클릭 포인트 테스트
                if self.selected and self.selected[0] == 'click':
                    k = self.selected[1]
                    rx, ry = self.click_pts[k]
                    self._test_click(k, rx, ry)
                else:
                    print("[!] 먼저 클릭 포인트(점)를 선택하세요")
            elif key == ord('v'):
                self.logo_save_mode = not self.logo_save_mode
                if self.logo_save_mode:
                    print("  [V] 팀 로고 저장 모드: " + '  '.join(f"{n}:{t}" for n,t in TEAMS))
            elif self.logo_save_mode and (48 <= key <= 57):  # 0에서 9
                digit = chr(key)
                match = next((t for n,t in TEAMS if n == digit), None)
                if match:
                    self._save_team_logo(match)
                self.logo_save_mode = False
            elif key == ord('c'):
                self.ocr_boxes = []; self.ocr_status = ''
                self.extract_results = {}; self.extract_status = ''
                self.last_click_label = ''

        self.stream.stop()
        cv2.destroyAllWindows()
        print("[*] 종료")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stream', default=STREAM_URL)
    args = parser.parse_args()
    CalibrationGUI(args.stream).run()
