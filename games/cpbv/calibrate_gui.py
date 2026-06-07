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
    PITCHER_P1, PITCHER_P2, PITCHER_P3, UI,
    POTENTIAL_BAR_TOTAL, POTENTIAL_NAMES_BATTER, POTENTIAL_NAMES_PITCHER,
    HOTZONE_ROWS, HOTZONE_COLS,
)

OVERRIDE_PATH        = os.path.join(os.path.dirname(__file__), 'config_override.json')
TEAM_TEMPLATES_DIR   = os.path.join(os.path.dirname(__file__), 'team_templates')
TEAM_TEMPLATES_BATTER  = os.path.join(TEAM_TEMPLATES_DIR, 'batter')
TEAM_TEMPLATES_PITCHER = os.path.join(TEAM_TEMPLATES_DIR, 'pitcher')

TEAMS = [
    ('1', '두산'), ('2', '삼성'), ('3', '한화'), ('4', '롯데'),
    ('5', 'KIA'),    ('6', '키움'), ('7', 'SSG'),    ('8', 'LG'),
    ('9', 'NC'),     ('0', 'KT'),
]

# overall 계산 대상 스탯 (평균으로 산출)
OVERALL_STAT_KEYS = {
    'batter_p1':  ['stat_power', 'stat_endure', 'stat_contact', 'stat_run', 'stat_eye', 'stat_defense'],
    'pitcher_p1': ['stat_speed', 'stat_control', 'stat_break', 'stat_stamina', 'stat_stuff', 'stat_defense'],
}

# 포지션 유효 목록
BATTER_POSITIONS  = ['C', '1B', '2B', '3B', 'SS', 'LF', 'CF', 'RF', 'DH']
PITCHER_POSITIONS = ['SP', 'RP', 'CP']

def _classify_position(raw: str, mode: str) -> str:
    """OCR 결과를 유효 포지션으로 분류. 매칭 실패 시 raw 반환."""
    import difflib, re
    candidates = PITCHER_POSITIONS if mode.startswith('pitcher') else BATTER_POSITIONS
    # 정제: 대문자 + 숫자/알파벳만
    cleaned = re.sub(r'[^A-Za-z0-9]', '', raw).upper()
    # 숫자 OCR 보정: 0→O, 5→S (SS→55 같은 오류 대비)
    corrected = (cleaned
                 .replace('0', 'O')
                 .replace('5', 'S')
                 .replace('1', 'I'))
    # 우선 exact match
    for cand in candidates:
        if cand == cleaned or cand == corrected:
            return cand
    # fuzzy match
    matches = difflib.get_close_matches(corrected, candidates, n=1, cutoff=0.4)
    return matches[0] if matches else raw

GAME_W = STREAM_GAME_X2 - STREAM_GAME_X1
GAME_H = STREAM_GAME_Y2 - STREAM_GAME_Y1

MODES = [
    ('batter_p1',  '타자 P1 - 능력치'),
    ('batter_p2',  '타자 P2 - 잠재력'),
    ('batter_p3',  '타자 P3 - 핫콜드존'),
    ('pitcher_p1', '투수 P1 - 능력치'),
    ('pitcher_p2', '투수 P2 - 잠재력'),
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

        # 잠재력 픽셀 포인트 픽커
        # pt_pts[mode][bar_key] = [(rx,ry), (rx,ry), (rx,ry)]  # 슬롯 2,3,4
        self.pt_pts: dict = {}
        self.pt_pick_queue: list = []   # [(mode, bar_key, slot_idx), ...]
        self.pt_pick_idx: int  = -1     # 현재 찍어야 할 인덱스
        self._load_pt_pts()

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

    def _load_pt_pts(self):
        """override JSON의 pt_pts 섹션 로드"""
        if not os.path.exists(OVERRIDE_PATH): return
        with open(OVERRIDE_PATH, encoding='utf-8') as f:
            data = json.load(f)
        for mk, bars in data.get('pt_pts', {}).items():
            if mk not in self.pt_pts:
                self.pt_pts[mk] = {}
            for bk, pts in bars.items():
                self.pt_pts[mk][bk] = [tuple(p) for p in pts]

    def save(self):
        data = {
            'regions':   {m: {k: list(v) for k, v in rgs.items()} for m, rgs in self.regions.items()},
            'click_pts': {k: list(v) for k, v in self.click_pts.items()},
            'pt_pts':    {m: {bk: [list(p) for p in pts] for bk, pts in bars.items()}
                          for m, bars in self.pt_pts.items()},
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
            # ── 포인트 픽커 모드 우선 처리 ───────────────────────────────
            if self.pt_pick_idx >= 0 and self.pt_pick_idx < len(self.pt_pick_queue):
                mode, key_name, slot_idx = self.pt_pick_queue[self.pt_pick_idx]
                rx = (x - STREAM_GAME_X1) / max(GAME_W, 1)
                ry = (y - STREAM_GAME_Y1) / max(GAME_H, 1)
                if mode not in self.pt_pts:
                    self.pt_pts[mode] = {}

                if key_name == 'hotzone_pts':
                    # 핫존: 단일 리스트에 순서대로 저장
                    total = HOTZONE_ROWS * HOTZONE_COLS
                    if key_name not in self.pt_pts[mode]:
                        self.pt_pts[mode][key_name] = [None] * total
                    self.pt_pts[mode][key_name][slot_idx] = (round(rx,4), round(ry,4))
                    r, c = divmod(slot_idx, HOTZONE_COLS)
                    print(f"  [PT] 핫존 {r+1}행{c+1}열 → ({rx:.4f}, {ry:.4f})")
                else:
                    # 잠재력 바: 슬롯 2,3,4
                    if key_name not in self.pt_pts[mode]:
                        self.pt_pts[mode][key_name] = [None, None, None]
                    self.pt_pts[mode][key_name][slot_idx] = (round(rx,4), round(ry,4))
                    print(f"  [PT] {key_name} 슬롯{slot_idx+2} → ({rx:.4f}, {ry:.4f})")

                self.pt_pick_idx += 1
                if self.pt_pick_idx >= len(self.pt_pick_queue):
                    self.pt_pick_idx = -1
                    print("  [PT] 완료! S키로 저장하세요.")
                else:
                    nxt = self.pt_pick_queue[self.pt_pick_idx]
                    if nxt[1] == 'hotzone_pts':
                        nr, nc = divmod(nxt[2], HOTZONE_COLS)
                        print(f"  [PT] → {nr+1}행 {nc+1}열 클릭")
                    else:
                        print(f"  [PT] → {nxt[1]} 슬롯{nxt[2]+2} 클릭")
                return

            # ── 일반 드래그 시작 ──────────────────────────────────────────
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
                from surya.recognition import RecognitionPredictor
                from surya.detection import DetectionPredictor
                from PIL import Image as _PilImg

                det_pred = DetectionPredictor()
                rec_pred = RecognitionPredictor()

                gf = self.frame[STREAM_GAME_Y1:STREAM_GAME_Y2,
                                STREAM_GAME_X1:STREAM_GAME_X2]
                gf_rgb = cv2.cvtColor(gf, cv2.COLOR_BGR2RGB)
                pil_gf = _PilImg.fromarray(gf_rgb)

                rec_results = rec_pred([pil_gf], det_predictor=det_pred)

                detected = []
                auto_set = []

                for line in rec_results[0].text_lines:
                    if line.confidence < 0.3: continue
                    b = line.bbox  # [x1,y1,x2,y2]
                    x1g, y1g, x2g, y2g = int(b[0]), int(b[1]), int(b[2]), int(b[3])
                    text, conf = line.text, line.confidence
                    detected.append((x1g, y1g, x2g, y2g, text, conf))

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
                self.ocr_status = 'surya 없음 (pip install surya-ocr)'
                print(f"[!] {self.ocr_status}")
            except Exception as e:
                self.ocr_status = f'오류: {e}'
                print(f"[!] OCR 오류: {e}")
            finally:
                self.ocr_running = False

        threading.Thread(target=_run, daemon=True).start()

    # ── OCR 영역별 추출 테스트 (E키) ─────────────────────────────────
    def _match_team_logo(self, crop, mode=None):
        """스트림 프레임 크롭에 저장된 팀 로고 템플릿 매칭
        mode에 따라 batter/ 또는 pitcher/ 서브폴더 우선 탐색 → 없으면 루트 탐색
        """
        if mode and mode.startswith('pitcher'):
            search_dirs = [TEAM_TEMPLATES_PITCHER, TEAM_TEMPLATES_DIR]
        else:
            search_dirs = [TEAM_TEMPLATES_BATTER, TEAM_TEMPLATES_DIR]

        templates = []
        for d in search_dirs:
            if os.path.exists(d):
                for f in os.listdir(d):
                    if f.endswith('.png'):
                        templates.append((os.path.join(d, f), os.path.splitext(f)[0]))
                if templates:
                    break  # 서브폴더에 있으면 루트 탐색 안 함

        if not templates:
            return None, 0.0, "저장된 템플릿 없음 (T키로 캡처)"

        best_score = -1.0
        best_team  = None

        for fpath, team in templates:
            try:
                from PIL import Image as _PilImg
                pil_tmpl = _PilImg.open(fpath).convert('RGB')
                tmpl = cv2.cvtColor(np.array(pil_tmpl), cv2.COLOR_RGB2BGR)
            except Exception:
                continue
            if tmpl is None: continue

            th, tw = tmpl.shape[:2]
            ch, cw = crop.shape[:2]

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
            return best_team, best_score, f"(저신도: {best_score:.0%})"
        return None, 0.0, "(인식 실패)"

    def _count_potential_bar(self, crop):
        """잠재력 바에서 총 칸 수 반환. Returns (count, ratios)"""
        n = POTENTIAL_BAR_TOTAL
        if crop is None or crop.size == 0:
            return (0, [])
        h, w = crop.shape[:2]
        if w == 0 or h == 0:
            return (0, [])
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        # 흰 배경(230+)은 제외, 슬롯(체브론)은 30~210 범위
        mask = cv2.inRange(gray, 30, 210)
        count = 0
        ratios = []
        seg_w = max(1, w // n)
        for i in range(n):
            x_start = i * seg_w
            x_end   = x_start + seg_w if i < n - 1 else w
            seg = mask[:, x_start:x_end]
            ratio = np.count_nonzero(seg) / (seg.size or 1)
            ratios.append(round(ratio, 2))
            if ratio > 0.08:
                count += 1
        return (count, ratios)

    def _save_team_logo(self, team_name):
        """team_logo 영역 크롭을 team_templates/{batter|pitcher}/팀이름.png로 저장"""
        # 현재 모드에 따라 서브폴더 결정
        if self.mode.startswith('pitcher'):
            save_dir = TEAM_TEMPLATES_PITCHER
        else:
            save_dir = TEAM_TEMPLATES_BATTER

        region = self.regions.get(self.mode, {}).get('team_logo')
        if region is None:
            # 현재 모드에 없으면 같은 타입(batter/pitcher)에서 탐색
            prefix = 'pitcher' if self.mode.startswith('pitcher') else 'batter'
            for mode_key, regs in self.regions.items():
                if mode_key.startswith(prefix) and 'team_logo' in regs:
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
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, f"{team_name}.png")
        try:
            from PIL import Image as _Img
            rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
            _Img.fromarray(rgb).save(path)
            sub = 'pitcher' if self.mode.startswith('pitcher') else 'batter'
            print(f"[+] 팀 로고 저장: team_templates/{sub}/{team_name}.png  ({crop.shape[1]}×{crop.shape[0]}px)")
        except Exception as e:
            print(f"[!] 로고 저장 실패: {e}")

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
                from surya.recognition import RecognitionPredictor
                from PIL import Image as _PilImg

                rec_predictor = RecognitionPredictor()
                regions = self.regions[self.mode]

                def _ocr_crop(crop_bgr):
                    """crop(BGR numpy) → [(text, conf), ...]
                    detection 없이 전체 크롭을 bbox로 직접 넘김 (게임 UI 숫자 detection 실패 방지)
                    """
                    import re as _re
                    rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
                    pil_img = _PilImg.fromarray(rgb)
                    w, h = pil_img.size
                    # 전체 크롭을 단일 bbox로 전달, math_mode=False (LaTeX 출력 방지)
                    results = rec_predictor([pil_img], bboxes=[[[0, 0, w, h]]], math_mode=False)
                    if not results or not results[0].text_lines:
                        return []
                    def _clean(t):
                        t = _re.sub(r'<[^>]+>', '', t)   # HTML 태그
                        t = _re.sub(r'\\[a-zA-Z,;!]+', '', t)  # LaTeX 커맨드
                        t = _re.sub(r'[{}^_]', '', t)    # LaTeX 괄호
                        return t.strip()
                    return [(_clean(line.text), line.confidence)
                            for line in results[0].text_lines
                            if line.confidence > 0.25]

                # ─── P2 모드: pt_pts 픽셀 포인트 기반 감지 ──────────────────
                if self.mode in ('batter_p2', 'pitcher_p2'):
                    names = (POTENTIAL_NAMES_BATTER if self.mode == 'batter_p2'
                             else POTENTIAL_NAMES_PITCHER)
                    bar_keys = [n + '_bar' for n in names]
                    pts_map = self.pt_pts.get(self.mode, {})
                    fh, fw = self.frame.shape[:2]
                    for bar_key in bar_keys:
                        pts = pts_map.get(bar_key, [])
                        count = 1  # 슬롯 1은 항상 존재
                        for pt in pts:
                            if pt is None:
                                break
                            px = int(STREAM_GAME_X1 + pt[0] * GAME_W)
                            py = int(STREAM_GAME_Y1 + pt[1] * GAME_H)
                            px = max(0, min(fw-1, px))
                            py = max(0, min(fh-1, py))
                            pixel = self.frame[py, px]   # BGR
                            gray_val = int(pixel[0]) * 0.114 + int(pixel[1]) * 0.587 + int(pixel[2]) * 0.299
                            # 흰 배경(230+)이 아니면 슬롯 있음
                            if gray_val < 220:
                                count += 1
                            else:
                                break   # 슬롯은 연속적이므로 흰색 나오면 중단
                        self.extract_results[bar_key] = str(count)
                        dbg = f"pts={len(pts)}" if pts else "포인트 미설정"
                        print(f"  {bar_key:<22} : {count}  [{dbg}]")
                    self.extract_status = f"완료 ({len(bar_keys)}개 잠재력)"
                    print(f"{'─'*52}\n")
                    return

                # ─── batter_p3: 핫/콜드존 색상코드 추출 ───────────────────────
                if self.mode == 'batter_p3':
                    pts_list = self.pt_pts.get('batter_p3', {}).get('hotzone_pts', [])
                    fh, fw = self.frame.shape[:2]
                    total = HOTZONE_ROWS * HOTZONE_COLS
                    grid = []
                    for idx in range(total):
                        pt = pts_list[idx] if idx < len(pts_list) else None
                        if pt is None:
                            grid.append('??????')
                            continue
                        px = int(STREAM_GAME_X1 + pt[0] * GAME_W)
                        py = int(STREAM_GAME_Y1 + pt[1] * GAME_H)
                        px = max(0, min(fw-1, px))
                        py = max(0, min(fh-1, py))
                        b, g, r = self.frame[py, px]
                        hex_color = f"{int(r):02X}{int(g):02X}{int(b):02X}"
                        grid.append(hex_color)
                    # 저장: 콤마 구분
                    self.extract_results['hotzone_pts'] = ','.join(grid)
                    print(f"  {'hotzone_pts':<22} :")
                    for row in range(HOTZONE_ROWS):
                        row_vals = grid[row*HOTZONE_COLS:(row+1)*HOTZONE_COLS]
                        print(f"    {'  '.join(f'#{v}' for v in row_vals)}")
                    self.extract_status = "완료 (핫존 9셀)"
                    print(f"{'─'*52}\n")
                    return

                # ─── pitcher_p3: 구종 추출 ───────────────────────────────────
                if self.mode == 'pitcher_p3':
                    import re as _re2
                    fh, fw = self.frame.shape[:2]
                    pairs = []

                    # ① 체력바: 구간 수 + 각 구간별 너비 비율 측정
                    sbar = regions.get('stamina_bar_detail')
                    if sbar:
                        bx1, by1, bx2, by2 = rect_px(*sbar)
                        bx1 = max(0, min(fw-1, bx1)); bx2 = max(0, min(fw, bx2))
                        by1 = max(0, min(fh-1, by1)); by2 = max(0, min(fh, by2))
                        bar_crop = self.frame[by1:by2, bx1:bx2]
                        bh, bw = bar_crop.shape[:2]
                        if bw > 0:
                            mid_y = bh // 2
                            # HSV Hue로 색상 분류: 0=어두움 1=빨강 2=오렌지 3=노랑 4=초록 5=파랑/시안
                            hsv_bar = cv2.cvtColor(bar_crop, cv2.COLOR_BGR2HSV)
                            def _hue_zone(pix_hsv):
                                h, s, v = int(pix_hsv[0]), int(pix_hsv[1]), int(pix_hsv[2])
                                if v < 40 or s < 40: return 0   # 어두움(빈 공간)
                                if h < 10 or h > 165: return 1  # 빨강
                                if h < 22: return 2              # 오렌지
                                if h < 38: return 3              # 노랑
                                if h < 85: return 4              # 초록
                                return 5                          # 파랑/시안
                            # 픽셀별 구간 레이블
                            zones = [_hue_zone(hsv_bar[mid_y, xi]) for xi in range(bw)]
                            # 연속 구간 그룹화 (어두움 제외)
                            segments = []
                            cur_zone, seg_start = zones[0], 0
                            for xi, z in enumerate(zones[1:], 1):
                                if z != cur_zone:
                                    if cur_zone != 0:
                                        segments.append((cur_zone, seg_start, xi))
                                    cur_zone, seg_start = z, xi
                            if cur_zone != 0:
                                segments.append((cur_zone, seg_start, bw))
                            # 전체 채움 폭 계산 + 노이즈 필터(3% 미만 제거) + 구간 비율
                            total_filled = sum(e - s for _, s, e in segments)
                            segments = [(z, s, e) for z, s, e in segments
                                        if (e - s) / max(total_filled, 1) >= 0.03]
                            total_filled = sum(e - s for _, s, e in segments)
                            seg_count = len(segments)
                            ratios = [round((e - s) / max(total_filled, 1), 3) for _, s, e in segments]
                            role = {5: 'CP', 4: 'RP', 3: 'SP'}.get(seg_count, '?')
                            ratios_str = ' '.join(str(r) for r in ratios)
                            self.extract_results['stamina_bar_detail'] = f"{seg_count}:{ratios_str}"
                            print(f"  {'stamina_bar_detail':<22} : {seg_count}구간({role})  비율=[{ratios_str}]")

                    parea = regions.get('pitches_area')
                    if parea:
                        px1, py1, px2, py2 = rect_px(*parea)
                        px1 = max(0, min(fw-1, px1)); px2 = max(0, min(fw, px2))
                        py1 = max(0, min(fh-1, py1)); py2 = max(0, min(fh, py2))
                        pcrop = self.frame[py1:py2, px1:px2]
                        ph, pw2 = pcrop.shape[:2]

                        if pw2 > 0 and ph > 0:
                            # 수평 구분선 자동 감지: 평균 밝기가 낮은 행 = 셀 경계
                            row_brightness = [
                                float(pcrop[ry, :, :].mean()) for ry in range(ph)
                            ]
                            # 밝기 임계: 전체 평균의 60% 이하 = 어두운 구분선
                            avg_b = sum(row_brightness) / max(len(row_brightness), 1)
                            threshold = avg_b * 0.6
                            # 구분선 y 위치 찾기 (연속된 어두운 행 중 가운데)
                            sep_ys = []
                            in_sep = False
                            seg_start = 0
                            for ry, b in enumerate(row_brightness):
                                if b < threshold and not in_sep:
                                    in_sep = True; seg_start = ry
                                elif b >= threshold and in_sep:
                                    in_sep = False
                                    sep_ys.append((seg_start + ry) // 2)
                            # 행 범위 생성
                            row_bounds = []
                            prev = 0
                            for sy in sep_ys:
                                if sy - prev > ph * 0.1:  # 최소 행 높이 10%
                                    row_bounds.append((prev, sy))
                                    prev = sy
                            row_bounds.append((prev, ph))

                            def _grade_from_badge(badge_bgr):
                                """배지 이미지 중심 픽셀 HSV → 등급 문자"""
                                if badge_bgr.size == 0:
                                    return None
                                bh, bw = badge_bgr.shape[:2]
                                cx, cy = bw // 2, bh // 2
                                hsv = cv2.cvtColor(badge_bgr, cv2.COLOR_BGR2HSV)
                                h, s, v = int(hsv[cy, cx, 0]), int(hsv[cy, cx, 1]), int(hsv[cy, cx, 2])
                                if v < 50 or s < 40:   return 'D'   # 어두움/회색
                                if h < 12 or h > 165:  return 'A'   # 빨강
                                if h < 35:             return 'S'   # 금색/노랑
                                if h < 85:             return 'B'   # 초록
                                return 'C'                           # 파랑/시안

                            for row_y1, row_y2 in row_bounds:
                                if row_y2 - row_y1 < 8:
                                    continue
                                row_crop = pcrop[row_y1:row_y2, :]
                                rh, rw = row_crop.shape[:2]
                                half = rw // 2
                                # 각 열(좌/우)을 개별 처리
                                for col_crop in [row_crop[:, :half], row_crop[:, half:]]:
                                    if col_crop.shape[1] < 20:
                                        continue
                                    ch, cw = col_crop.shape[:2]
                                    # 배지 영역: 우측 20% (등급 색상)
                                    badge_x = int(cw * 0.80)
                                    badge = col_crop[:, badge_x:]
                                    grade = _grade_from_badge(badge)
                                    if grade is None:
                                        continue
                                    # 이름 영역: 좌측 75% OCR
                                    name_crop = col_crop[:, :int(cw * 0.75)]
                                    nh, nw = name_crop.shape[:2]
                                    scale = max(3, 200 // max(nh, 1))
                                    nup = cv2.resize(name_crop, (nw*scale, nh*scale),
                                                     interpolation=cv2.INTER_CUBIC)
                                    ntexts = _ocr_crop(nup)
                                    nraw = ' '.join(t for t, _ in ntexts) if ntexts else ''
                                    # 한글 이름만 추출 (2글자 이상)
                                    nm = _re2.search(r'[\uAC00-\uD7A3]{2,}', nraw)
                                    if nm:
                                        pairs.append((nm.group(), grade))

                    pitches_str = '  '.join(f"{n}:{g.upper()}" for n, g in pairs)
                    self.extract_results['pitches_area'] = pitches_str
                    if pairs:
                        print(f"  {'pitches_area':<22} : {pitches_str}")
                    else:
                        print(f"  {'pitches_area':<22} : (파싱실패)")

                    self.extract_status = "완료 (구종)"
                    print(f"{'─'*52}\n")
                    return

                # ─── 일반 모드: 영역 OCR ─────────────────────────────────────
                for key, region in regions.items():
                    x1, y1, x2, y2 = rect_px(*region)
                    fh, fw = self.frame.shape[:2]
                    x1c, y1c = max(0,x1), max(0,y1)
                    x2c, y2c = min(fw,x2), min(fh,y2)
                    if x2c <= x1c or y2c <= y1c:
                        self.extract_results[key] = '(범위 오류)'
                        print(f"  {key:<22} : (범위 오류)")
                        continue

                    crop = self.frame[y1c:y2c, x1c:x2c]



                    # 팀 로고
                    if key == 'team_logo':
                        team, score, warn = self._match_team_logo(crop, mode=self.mode)
                        if team and score >= 0.5:
                            display = f"{team}  ({score:.0%})"
                            self.extract_results[key] = team
                        elif team:
                            display = f"{team}?  ({score:.0%}) ← 저신도"
                            self.extract_results[key] = team
                        else:
                            display = warn or '(인식 실패)'
                            self.extract_results[key] = ''
                        print(f"  {key:<22} : {display}")
                        continue

                    # 일반 OCR: 업스케일 + 대비 강화
                    ch, cw = crop.shape[:2]
                    # 최소 3배 upscale (한글 자모 구분력 향상)
                    scale = max(3, 120 // max(cw, 1), 60 // max(ch, 1))
                    if scale > 1:
                        crop = cv2.resize(crop, (cw*scale, ch*scale),
                                          interpolation=cv2.INTER_CUBIC)
                    # CLAHE로 대비 강화 (어두운 배경 텍스트)
                    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
                    l, a, b = cv2.split(lab)
                    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
                    l = clahe.apply(l)
                    crop = cv2.cvtColor(cv2.merge([l, a, b]), cv2.COLOR_LAB2BGR)
                    texts = _ocr_crop(crop)
                    if texts:
                        # 신뢰도 최고 1개만 사용 (모든 필드는 단일 값)
                        best_text, best_conf = max(texts, key=lambda x: x[1])
                        val     = best_text
                        display = f"{val}  ({best_conf:.0%})"
                    else:
                        val     = ''
                        display = '(인식 안 됨)'
                    self.extract_results[key] = val
                    print(f"  {key:<22} : {display}")

                # ─── 사후 처리: overall 계산 + 숫자 필드 정리 + 요약 ─────────
                import re as _re

                # overall: OCR 추출 우선, 실패 시 스탯 평균 fallback
                ocr_overall = self.extract_results.get('overall_area', '')
                m_ov = _re.search(r'\d+', ocr_overall)
                if m_ov:
                    self.extract_results['overall_area'] = m_ov.group()
                else:
                    # OCR 실패 → 스탯 평균으로 계산
                    stat_keys = OVERALL_STAT_KEYS.get(self.mode, [])
                    if stat_keys:
                        vals = []
                        for sk in stat_keys:
                            m = _re.search(r'\d+', self.extract_results.get(sk, ''))
                            if m: vals.append(int(m.group()))
                        self.extract_results['overall_area'] = (
                            str(round(sum(vals)/len(vals))) if vals else ''
                        )

                # 숫자 전용 필드 기호 제거
                NUMERIC_KEYS = {'launch_area','setdeck_area','overall_area',
                                'stat_power','stat_endure','stat_contact',
                                'stat_run','stat_eye','stat_defense',
                                'stat_speed','stat_control','stat_break',
                                'stat_stamina','stat_stuff'}
                for nk in NUMERIC_KEYS:
                    v = self.extract_results.get(nk, '')
                    if v:
                        m = _re.search(r'\d+', v)
                        self.extract_results[nk] = m.group() if m else ''

                # 포지션 분류 (타자: C/1B/2B/3B/SS/LF/CF/RF/DH, 투수: SP/RP/CP)
                pos_raw = self.extract_results.get('position_area', '')
                if pos_raw:
                    self.extract_results['position_area'] = _classify_position(pos_raw, self.mode)

                # 이름 정제: 앞뒤 특수문자 + 그림자 중복 텍스트 제거
                name_raw = self.extract_results.get('name_area', '')
                if name_raw:
                    import difflib as _diff
                    nc = _re.sub(r"^[^\w\uAC00-\uD7A3']+", '', name_raw)  # 앞쪽 쓰레기 제거
                    # 연도 마커('YY)가 있으면 첫 번째 것까지만 사용 (그림자 이중 텍스트 차단)
                    m_year = _re.search(r"'\d{2}", nc)
                    if m_year:
                        nc = nc[:m_year.end()]
                    else:
                        # 연도 없는 이름: 절반 비교로 중복 감지
                        mid = len(nc) // 2
                        if mid >= 2:
                            first, second = nc[:mid], nc[mid:]
                            ratio = _diff.SequenceMatcher(None, first, second).ratio()
                            if ratio >= 0.5:   # 50%+ 유사 = 중복으로 판단
                                nc = first
                    self.extract_results['name_area'] = nc.strip()

                # 종합 요약 출력
                print(f"\n{'─'*52}")
                print(f"  {'[ 추출 결과 요약 ]':^48}")
                print(f"{'─'*52}")
                SUMMARY_ORDER = [
                    'name_area','position_area','team_logo',
                    'setdeck_area','launch_area','overall_area',
                    'stat_power','stat_endure','stat_contact',
                    'stat_run','stat_eye','stat_defense',
                    'stat_speed','stat_control','stat_break',
                    'stat_stamina','stat_stuff',
                ]
                for k in SUMMARY_ORDER:
                    v = self.extract_results.get(k)
                    if v is None: continue
                    print(f"  {k:<22} : {v or '(없음)'}")
                print(f"{'─'*52}\n")

                self.extract_status = f"완료 ({len(regions)}개 영역)"

            except ImportError:
                self.extract_status = 'surya 없음'
                print("[!] pip install surya-ocr")
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

        import time as _time
        _last_save = 0.0
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
                now = _time.time()
                if now - _last_save >= 1.0:  # 1초 debounce
                    self.save()
                    _last_save = now
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
                # C: 화면 클리어
                self.ocr_boxes = []; self.ocr_status = ''
                self.extract_results = {}; self.extract_status = ''
                self.last_click_label = ''
            elif key == ord('f'):
                # F: P2 모드에서 픽셀 포인트 픽커 시작
                if self.mode in ('batter_p2', 'pitcher_p2'):
                    names = (POTENTIAL_NAMES_BATTER if self.mode == 'batter_p2'
                             else POTENTIAL_NAMES_PITCHER)
                    bar_keys = [n + '_bar' for n in names]
                    self.pt_pick_queue = [
                        (self.mode, bk, si)
                        for bk in bar_keys
                        for si in range(3)  # 슬롯 2,3,4
                    ]
                    self.pt_pick_idx = 0
                    total = len(self.pt_pick_queue)
                    print(f"  [PT] 잠재력 픽커 시작 ({total}개 포인트)")
                    if total > 0:
                        print(f"  [PT] → {self.pt_pick_queue[0][1]} 슬롯{self.pt_pick_queue[0][2]+2} 클릭")

                elif self.mode == 'batter_p3':
                    # 핫/콜드존: 3×3 = 9 포인트
                    total = HOTZONE_ROWS * HOTZONE_COLS
                    self.pt_pick_queue = [
                        ('batter_p3', 'hotzone_pts', idx)
                        for idx in range(total)
                    ]
                    self.pt_pick_idx = 0
                    print(f"  [PT] 핫/콜드존 픽커 시작 ({total}개 셀)")
                    r, c = 0, 0
                    print(f"  [PT] → {r+1}행 {c+1}열 클릭")

                else:
                    print("[!] P2/P3(batter) 모드에서만 사용 가능")

        self.stream.stop()
        cv2.destroyAllWindows()
        print("[*] 종료")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stream', default=STREAM_URL)
    args = parser.parse_args()
    CalibrationGUI(args.stream).run()
