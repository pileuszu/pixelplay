"""
CPBV 골든 글러브 투수 정보 자동 추출기
--------------------------------------
게임에서 투수 카드 화면을 열어두면 자동으로:
  - P1: 이름, 오버롤, 포지션, 팀, 능력치 6종 추출
  - P2: 잠재력 5종 (장타억제, 속구구종, 침착성, 변화구구종, 견제) 1~4 값
  - P3: 체력바 구간(픽셀), 구종 + 등급(S/A/B/C/D/E) 추출

사용법:
  python -m games.cpbv.classifier.extract_golden_glove_pitcher
  python -m games.cpbv.classifier.extract_golden_glove_pitcher --count 30
  python -m games.cpbv.classifier.extract_golden_glove_pitcher --out D:/data/pitchers.json

흐름:
  P1 추출 → next_page → P2 추출 → next_page → P3 추출 → 저장
  → next_player → prev_page×2 → P1 추출 → ...
"""

import cv2
import re
import difflib
import sys
import os
import json
import time
import socket
import threading
import numpy as np
import argparse
from datetime import datetime
from PIL import Image

# ─── 경로 설정 ──────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
GRADE_TEMPLATES_DIR = os.path.join(_HERE, '..', 'assets', 'grades', 'pitcher')
sys.path.insert(0, os.path.join(_HERE, '..', '..', '..'))

from games.cpbv.config_cpbv import (
    STREAM_URL, MOUSE_HOST, MOUSE_PORT,
    STREAM_GAME_X1, STREAM_GAME_Y1, STREAM_GAME_X2, STREAM_GAME_Y2,
    WINDOW_LEFT, WINDOW_TOP, WINDOW_WIDTH, WINDOW_HEIGHT,
    PITCHER_P1, PITCHER_P3, UI,
    TEAM_TEMPLATES_DIR, POTENTIAL_NAMES_PITCHER,
)

# config_override.json에서 pt_pts 로드 (P2 잠재력 포인트)
_OVERRIDE_PATH = os.path.join(_HERE, '..', 'config_override.json')
_PT_PTS: dict = {}
if os.path.exists(_OVERRIDE_PATH):
    with open(_OVERRIDE_PATH, encoding='utf-8') as _f:
        _ovr = json.load(_f)
    _PT_PTS = _ovr.get('pt_pts', {})

GAME_W = STREAM_GAME_X2 - STREAM_GAME_X1
GAME_H = STREAM_GAME_Y2 - STREAM_GAME_Y1

DEFAULT_OUT = os.path.join(_HERE, '..', '..', '..', 'data',
                           f'golden_glove_pitchers_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')

# ─── OCR 초기화 (지연 로드) ──────────────────────────────────────────────────
_rec_pred = None

def _init_ocr():
    global _rec_pred
    if _rec_pred is not None:
        return
    print("[*] Surya OCR 초기화 중...")
    from surya.recognition import RecognitionPredictor
    _rec_pred = RecognitionPredictor()
    print("[*] OCR 준비 완료")


def _ocr_crop(img_bgr):
    """단일 bbox Surya OCR → [(text, confidence), ...]
    calibrate_gui.py의 _ocr_crop과 동일한 방식
    """
    _init_ocr()
    h, w = img_bgr.shape[:2]
    if h == 0 or w == 0:
        return []
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    results = _rec_pred([pil_img], bboxes=[[[0, 0, w, h]]], math_mode=False)
    if not results or not results[0].text_lines:
        return []
    def _clean(t):
        t = re.sub(r'<[^>]+>', '', t)          # HTML 태그
        t = re.sub(r'\\[a-zA-Z,;!]+', '', t)  # LaTeX 커맨드
        t = re.sub(r'[{}^_]', '', t)           # LaTeX 괄호
        return t.strip()
    return [(_clean(ln.text), ln.confidence)
            for ln in results[0].text_lines
            if ln.confidence > 0.25]


# ─── 좌표 변환 ───────────────────────────────────────────────────────────────
def rect_px(rx, ry, rw, rh):
    x1 = int(STREAM_GAME_X1 + rx * GAME_W)
    y1 = int(STREAM_GAME_Y1 + ry * GAME_H)
    x2 = int(STREAM_GAME_X1 + (rx + rw) * GAME_W)
    y2 = int(STREAM_GAME_Y1 + (ry + rh) * GAME_H)
    return x1, y1, x2, y2


def crop_region(frame, region):
    fh, fw = frame.shape[:2]
    x1, y1, x2, y2 = rect_px(*region)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(fw, x2), min(fh, y2)
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2]


def ocr_text(frame, region, scale_target_h=60):
    """region을 OCR해서 텍스트 반환"""
    crop = crop_region(frame, region)
    if crop is None:
        return ''
    h, w = crop.shape[:2]
    if h == 0 or w == 0:
        return ''
    scale = max(2, scale_target_h // max(h, 1))
    up = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
    texts = _ocr_crop(up)
    return ' '.join(t for t, _ in texts) if texts else ''


def ocr_number(frame, region):
    """숫자 추출 (예: 능력치 수치)"""
    raw = ocr_text(frame, region)
    m = re.search(r'\d+', raw)
    return int(m.group()) if m else None


# ─── 스트림 스레드 (calibrate_gui.py와 동일) ───────────────────────────────────
class StreamThread(threading.Thread):
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
        while not self._stop.is_set():
            ret, frame = cap.read()
            if ret:
                with self._lock:
                    self._frame = frame
            else:
                time.sleep(0.05)
        cap.release()

    def get_latest(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def stop(self):
        self._stop.set()


# ─── 마우스 클라이언트 (calibrate_gui.py와 동일) ─────────────────────────────
class MouseClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
        if host:
            self._try_connect()

    def _try_connect(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect((self.host, self.port))
            self.sock = s
            self.connected = True
            print(f"[+] 마우스 서버 연결: {self.host}:{self.port}")
        except Exception as e:
            print(f"[!] 마우스 연결 실패: {e}")
            self.connected = False

    def _send(self, cmd):
        if not self.sock:
            return
        try:
            self.sock.settimeout(2.0)
            self.sock.sendall((json.dumps(cmd) + '\n').encode())
            self.sock.recv(1024)
        except Exception:
            self.connected = False
            self.sock = None

    def click_ratio(self, rx, ry, focus_first=True):
        """GUI와 동일: 게임 PC 실제 윈도우 좌표로 변환"""
        if not self.connected:
            self._try_connect()
        abs_x = int(WINDOW_LEFT + rx * WINDOW_WIDTH)
        abs_y = int(WINDOW_TOP  + ry * WINDOW_HEIGHT)
        if focus_first:
            cx = int(WINDOW_LEFT + WINDOW_WIDTH  * 0.5)
            cy = int(WINDOW_TOP  + WINDOW_HEIGHT * 0.5)
            self._send({'action': 'focus_window', 'x': cx, 'y': cy})
            time.sleep(0.15)
        self._send({'action': 'click', 'x': abs_x, 'y': abs_y})
        return abs_x, abs_y

    def click_ui(self, key):
        """UI 디셔너리 키로 클릭 (next_player, next_page 등)"""
        rx, ry = UI[key]
        return self.click_ratio(rx, ry)


# ─── 등급 감지 (템플릿 매칭) ─────────────────────────────────────────────────
_grade_templates: dict = {}

def _load_grade_templates():
    """assets/grades/pitcher/*.png 로드
    파일명: pitch{n}_{grade}.png (예: pitch1_A.png, pitch3_B.png)
    또는 {grade}.png 단순 형식도 지원.
    같은 등급의 template 여러 개 → 리스트로 저장"""
    global _grade_templates
    if _grade_templates:
        return
    if not os.path.exists(GRADE_TEMPLATES_DIR):
        return
    for fname in os.listdir(GRADE_TEMPLATES_DIR):
        if not fname.lower().endswith('.png'):
            continue
        stem = os.path.splitext(fname)[0].upper()  # 'PITCH1_A' or 'A'
        # 등급 글자 추출: 마지막 '_' 뒤 또는 전체
        if '_' in stem:
            grade = stem.rsplit('_', 1)[-1]  # 'PITCH1_A' → 'A'
        else:
            grade = stem
        if not grade or grade[0] not in 'SABCDE':
            continue
        grade = grade[0]
        fpath = os.path.join(GRADE_TEMPLATES_DIR, fname)
        try:
            buf = np.fromfile(fpath, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception:
            img = None
        if img is not None:
            _grade_templates.setdefault(grade, []).append(img)


def match_grade(crop_bgr):
    """등급 crop → 템플릿 매칭. 같은 등급 templates 중 최고 점수 사용.
    templates 없으면 None 반환 (OCR fallback)"""
    _load_grade_templates()
    if not _grade_templates:
        return None
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    best_grade, best_score = '?', 0.0
    for grade, tmpl_list in _grade_templates.items():
        for tmpl in tmpl_list:
            try:
                t = cv2.resize(tmpl, (crop_bgr.shape[1], crop_bgr.shape[0]))
                res = cv2.matchTemplate(crop_bgr, t, cv2.TM_CCOEFF_NORMED)
                score = float(res.max())
                if score > best_score:
                    best_score, best_grade = score, grade
            except Exception:
                continue
    return best_grade if best_score > 0.45 else None


# ─── 팀 감지 (템플릿 매칭) ───────────────────────────────────────────────────
_team_templates = {}

def _load_team_templates():
    global _team_templates
    if _team_templates:
        return
    tmpl_dir = os.path.join(TEAM_TEMPLATES_DIR, 'pitcher')
    if not os.path.exists(tmpl_dir):
        tmpl_dir = TEAM_TEMPLATES_DIR
    for fname in os.listdir(tmpl_dir):
        if not fname.endswith('.png'):
            continue
        team_name = os.path.splitext(fname)[0]
        fpath = os.path.join(tmpl_dir, fname)
        # Windows에서 한글 경로 처리: np.fromfile + imdecode
        try:
            buf = np.fromfile(fpath, dtype=np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        except Exception:
            img = None
        if img is not None:
            _team_templates[team_name] = img


def detect_team(frame, region):
    """팀 로고 영역을 템플릿 매칭으로 감지"""
    _load_team_templates()
    if not _team_templates:
        return '?'
    logo = crop_region(frame, region)
    if logo is None:
        return '?'
    best_team, best_score = '?', 0.0
    for team, tmpl in _team_templates.items():
        try:
            t = cv2.resize(tmpl, (logo.shape[1], logo.shape[0]))
            res = cv2.matchTemplate(logo, t, cv2.TM_CCOEFF_NORMED)
            score = float(res.max())
            if score > best_score:
                best_score, best_team = score, team
        except Exception:
            continue
    return best_team if best_score > 0.5 else '?'


# ─── P1 추출 ────────────────────────────────────────────────────────────────
def extract_pitcher_p1(frame, regions=None):
    """투수 Page 1: 이름, 오버롤, 포지션, 팀, 능력치 6종"""
    r = regions or PITCHER_P1

    # 이름: calibrate_gui.py와 동일한 정제 로직 (연도 마커 + 그림자 중복 제거)
    name_raw = ocr_text(frame, r['name_area'])
    nc = re.sub(r"^[^\w\uAC00-\uD7A3']+", '', name_raw)  # 앞쪽 쓰레기
    m_year = re.search(r"'\d{2}", nc)  # '25 같은 연도 마커
    if m_year:
        nc = nc[:m_year.end()]
    else:
        mid = len(nc) // 2
        if mid >= 2:
            first, second = nc[:mid], nc[mid:]
            ratio = difflib.SequenceMatcher(None, first, second).ratio()
            if ratio >= 0.5:
                nc = first
    name = nc.strip()

    # 오버롤
    overall = ocr_number(frame, r['overall_area'])

    # 포지션
    pos_raw = ocr_text(frame, r['position_area'])
    pos_m = re.search(r'SP|RP|CP', pos_raw.upper())
    position = pos_m.group() if pos_m else pos_raw.strip()[:3]

    # 팀 (템플릿 매칭)
    team = detect_team(frame, r.get('team_logo', (0.04, 0.452, 0.10, 0.060)))

    # 능력치
    stat_keys = {
        'stat_speed':   'speed',
        'stat_control': 'control',
        'stat_break':   'break',
        'stat_stamina': 'stamina',
        'stat_stuff':   'stuff',
        'stat_defense': 'defense',
    }
    stats = {}
    for key, label in stat_keys.items():
        if key in r:
            stats[label] = ocr_number(frame, r[key])

    return {
        'name':     name,
        'overall':  overall,
        'position': position,
        'team':     team,
        'stats':    stats,
    }


# ─── P3 추출 ────────────────────────────────────────────────────────────────
def extract_stamina(frame, region):
    """체력바 HSV 구간 감지 → {seg_count, role, px_widths}"""
    crop = crop_region(frame, region)
    if crop is None:
        return None
    bh, bw = crop.shape[:2]
    if bw == 0:
        return None

    mid_y = bh // 2
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)

    def _hue_zone(px):
        h, s, v = int(px[0]), int(px[1]), int(px[2])
        if v < 40 or s < 40:  return 0  # 어두움(빈 공간)
        if h < 10 or h > 165: return 1  # 빨강
        if h < 22:             return 2  # 오렌지
        if h < 38:             return 3  # 노랑
        if h < 85:             return 4  # 초록
        return 5                         # 파랑/시안

    zones = [_hue_zone(hsv[mid_y, xi]) for xi in range(bw)]

    segments, cur_zone, seg_start = [], zones[0], 0
    for xi, z in enumerate(zones[1:], 1):
        if z != cur_zone:
            if cur_zone != 0:
                segments.append((cur_zone, seg_start, xi))
            cur_zone, seg_start = z, xi
    if cur_zone != 0:
        segments.append((cur_zone, seg_start, bw))

    # 3% 미만 노이즈 제거
    total = sum(e - s for _, s, e in segments)
    segments = [(z, s, e) for z, s, e in segments if (e - s) / max(total, 1) >= 0.03]

    seg_count = len(segments)
    px_widths = [e - s for _, s, e in segments]
    role = {5: 'CP', 4: 'RP', 3: 'SP'}.get(seg_count, '?')

    return {
        'seg_count': seg_count,
        'role':      role,
        'px_widths': px_widths,
    }


# 알려진 구종명 목록 (OCR 결과를 이 목록으로 분류)
KNOWN_PITCH_NAMES = [
    '포심', '투심', '커터', '싱커',                   # 속구 계열
    '체인지업', '서클체인지업', '슬라이더', '커브', '포크', '스플리터',  # 변화 계열
]

def _classify_pitch_name(ocr_text: str) -> str:
    """OCR 결과 → 알려진 구종명으로 분류 (difflib 기반)"""
    from difflib import SequenceMatcher

    if not ocr_text:
        return ''

    best_name = ''
    best_score = 0.0

    # 긴 이름 먼저 검사 (서클체인지업 > 체인지업 우선)
    for pitch in sorted(KNOWN_PITCH_NAMES, key=len, reverse=True):
        # 1) 직접 포함 확인 (가장 확실)
        if pitch in ocr_text:
            return pitch
        # 2) OCR 텍스트가 구종 이름에 포함 (짧게 잘린 경우)
        if ocr_text in pitch and len(ocr_text) >= 2:
            score = len(ocr_text) / len(pitch)
            if score > best_score:
                best_score = score
                best_name = pitch
        # 3) 유사도 비교
        score = SequenceMatcher(None, ocr_text, pitch).ratio()
        if score > best_score:
            best_score = score
            best_name = pitch

    # 유사도가 너무 낮으면 OCR 원본 반환
    return best_name if best_score >= 0.4 else ocr_text


def extract_pitches(frame, regions=None):
    """구종 pitch1~5 이름+등급 OCR → [{'name': ..., 'grade': ...}, ...]
    OCR 결과를 알려진 구종명 목록으로 분류하여 정확도 향상"""
    r = regions or PITCHER_P3
    pitches = []
    for i in range(1, 6):
        nreg = r.get(f'pitch{i}_name')
        greg = r.get(f'pitch{i}_grade')
        if not nreg or not greg:
            continue

        # 이름: _ocr_crop 직접 사용, confidence 높은 순 → 구종 분류기 통과
        ncrop = crop_region(frame, nreg)
        name = ''
        if ncrop is not None:
            nh, nw = ncrop.shape[:2]
            if nw > 0 and nh > 0:
                nscale = max(2, 60 // max(nh, 1))
                nup = cv2.resize(ncrop, (nw*nscale, nh*nscale), interpolation=cv2.INTER_CUBIC)
                ntexts = _ocr_crop(nup)
                # confidence 높은 순으로 한글 추출 후 구종명 분류
                ntexts_sorted = sorted(ntexts, key=lambda x: -x[1])
                for ntext, _ in ntexts_sorted:
                    import re as _re
                    nm = _re.search(r'[\uAC00-\uD7A3]{2,}', ntext)
                    if nm:
                        name = _classify_pitch_name(nm.group())
                        break

        # 등급: 템플릿 매칭 우선, 없으면 OCR fallback
        gcrop = crop_region(frame, greg)
        grade = ''
        if gcrop is not None:
            gh, gw = gcrop.shape[:2]
            if gw > 0 and gh > 0:
                # 1) 템플릿 매칭
                grade = match_grade(gcrop) or ''
                # 2) OCR fallback
                if not grade:
                    gscale = max(6, 80 // max(gh, 1))
                    gup = cv2.resize(gcrop, (gw * gscale, gh * gscale),
                                     interpolation=cv2.INTER_CUBIC)
                    gtexts = _ocr_crop(gup)
                    graw = ' '.join(t for t, _ in gtexts) if gtexts else ''
                    gm = re.search(r'[SABCDEsabcde]', graw)
                    if gm:
                        grade = gm.group().upper()

        if name:
            pitches.append({'name': name, 'grade': grade or '?'})

    return pitches
def extract_pitcher_p3(frame, regions=None):
    """투수 Page 3: 체력바 + 구종"""
    r = regions or PITCHER_P3
    stamina = extract_stamina(frame, r['stamina_bar_detail'])
    pitches = extract_pitches(frame, r)
    return {
        'stamina': stamina,
        'pitches': pitches,
    }


# ─── P2 추출 ────────────────────────────────────────────────────────────────
def extract_pitcher_p2(frame, pt_pts: dict = None):
    """
    투수 Page 2: 잠재력 5종 (pt_pts 픽셀 샘플링)
    pt_pts[bar_key] = [(rx,ry), (rx,ry), (rx,ry)]  # 슬롯 2, 3, 4
    슬롯 1은 항상 있음 → count 시작 = 1
    gray < 220 이면 해당 슬롯 활성
    """
    if pt_pts is None:
        pt_pts = _PT_PTS.get('pitcher_p2', {})

    fh, fw = frame.shape[:2]
    bar_names = POTENTIAL_NAMES_PITCHER  # [suppress_hr, fastball, composure, breaking, pickoff]
    result = {}

    for name in bar_names:
        bar_key = f'{name}_bar'
        pts = pt_pts.get(bar_key, [])
        count = 1  # 슬롯 1은 항상 존재
        for pt in pts:
            if pt is None:
                break
            px = int(STREAM_GAME_X1 + pt[0] * GAME_W)
            py = int(STREAM_GAME_Y1 + pt[1] * GAME_H)
            px = max(0, min(fw - 1, px))
            py = max(0, min(fh - 1, py))
            b, g, r = frame[py, px]
            gray_val = int(b) * 0.114 + int(g) * 0.587 + int(r) * 0.299
            if gray_val < 220:   # 슬롯 채워져 있음
                count += 1
            else:
                break            # 슬롯은 연속적 → 빈 슬롯 나오면 중단
        result[name] = count

    return result


# ─── 메인 추출 루프 ──────────────────────────────────────────────────────────
def run(stream_url: str, mouse: MouseClient, count: int, out_path: str,
        page_wait: float = 1.5, player_wait: float = 1.0):
    """
    골든 글러브 투수 카드 순회 추출
      - 시작 전: 게임에서 투수 카드 P1 화면을 열어두세요
      - count: 추출할 선수 수 (0 = 무한)
    """
    stream = StreamThread(stream_url)
    stream.start()

    print("[*] 스트림 연결 대기...")
    for _ in range(30):
        if stream.get_latest() is not None:
            break
        time.sleep(0.2)

    if not stream.ok:
        print("[!] 스트림 연결 실패")
        return []

    print(f"[*] 추출 시작 | 목표: {count if count else '무한'} 명")
    print("[*] 게임에서 투수 카드 P1 화면을 열어두세요. 5초 후 시작...")
    time.sleep(5)

    players = []
    seen_names = set()   # 중복 감지용
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    idx = 0
    while (count == 0 or idx < count):
        frame = stream.get_latest()
        if frame is None:
            time.sleep(0.3)
            continue

        # ── P1 추출 (이름 비면 최대 3회 재시도) ────────────────────────
        print(f"\n[{idx+1}] ── P1 추출 중...")
        for attempt in range(3):
            p1 = extract_pitcher_p1(stream.get_latest())
            if p1['name'] and p1['overall'] is not None:
                break
            print(f"    [!] P1 이름/overall 미추출, 재시도 ({attempt+1}/3)...")
            time.sleep(page_wait)
        print(f"    이름={p1['name']}  전체={p1['overall']}  포지션={p1['position']}  팀={p1['team']}")
        print(f"    능력치={p1['stats']}")

        # 중복 감지 (count=0일 때만 자동 종료)
        if count == 0 and p1['name'] and p1['name'] in seen_names:
            print(f"\n[*] 중복 감지: '{p1['name']}' → 전체 순환 완료, 종료")
            break

        # ── P1 → P2 ──────────────────────────────────────
        if mouse.connected:
            mouse.click_ui('next_page')
            time.sleep(page_wait)

        # P2: 픽셀 샘플링 - 전환 완료 대기 후 추출
        print(f"[{idx+1}] ── P2 추출 중...")
        for attempt in range(3):
            frame = stream.get_latest()
            p2 = extract_pitcher_p2(frame)
            vals = list(p2.values())
            # 모두 같은 값이면 잘못된 페이지 가능성 → 재시도
            if vals and len(set(vals)) > 1:
                break
            msg = f"    [!] P2 값 일관성 없음({vals}), 재시도 ({attempt+1}/3)..."
            print(msg)
            time.sleep(page_wait)
        print(f"    잠재력={p2}")

        # ── P2 → P3 ──────────────────────────────────────
        if mouse.connected:
            mouse.click_ui('next_page')
            time.sleep(page_wait)

        print(f"[{idx+1}] ── P3 추출 중...")
        for attempt in range(3):
            frame = stream.get_latest()
            p3 = extract_pitcher_p3(frame)
            total_px = sum(p3['stamina']['px_widths']) if p3['stamina'] else 0
            pnames = [x['name'] for x in p3['pitches']]
            has_dup = len(pnames) != len(set(pnames))
            has_qgrade = any(x['grade'] == '?' for x in p3['pitches'])
            if total_px > 20 and not has_dup and not has_qgrade:
                break
            reasons = []
            if total_px <= 20: reasons.append(f'체력bar={total_px}')
            if has_dup: reasons.append('중복구종')
            if has_qgrade: reasons.append('등급?')
            print(f"    [!] P3 재시도({attempt+1}/3): {', '.join(reasons)}")
            time.sleep(page_wait)

        if p3['stamina']:
            st = p3['stamina']
            print(f"    체력: {st['seg_count']}구간({st['role']})  px={st['px_widths']}")
        pitches_str = '  '.join(f"{p['name']}:{p['grade']}" for p in p3['pitches'])
        print(f"    구종: {pitches_str}")

        # ── 통합 레코드 ───────────────────────────────────
        record = {
            **p1,
            'potential': p2,
            'stamina_detail': p3['stamina'],
            'pitches': p3['pitches'],
            'page_idx': idx + 1,
            'timestamp': datetime.now().isoformat(timespec='seconds'),
        }
        players.append(record)
        if p1['name']:
            seen_names.add(p1['name'])

        # 중간 저장
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(players, f, ensure_ascii=False, indent=2)
        print(f"    → 저장됨 ({len(players)}명)")

        idx += 1
        if count and idx >= count:
            break

        # ── 다음 선수로 (P3 → next_player → prev_page×2 → P1) ──
        if mouse.connected:
            mouse.click_ui('next_player')
            time.sleep(player_wait)
            mouse.click_ui('prev_page')
            time.sleep(page_wait)
            mouse.click_ui('prev_page')
            time.sleep(page_wait)

    stream.stop()
    print(f"\n[*] 완료: {len(players)}명 → {out_path}")
    return players


# ─── 단일 프레임 테스트 ──────────────────────────────────────────────────────
def test_frame(img_path: str, page: str = 'p1'):
    """스크린샷으로 P1/P2/P3 추출 테스트"""
    _init_ocr()
    frame = cv2.imread(img_path)
    if frame is None:
        print(f"[!] 이미지 로드 실패: {img_path}")
        return

    if page == 'p1':
        print("=== P1 추출 ===")
        p1 = extract_pitcher_p1(frame)
        print(json.dumps(p1, ensure_ascii=False, indent=2))
    elif page == 'p2':
        print("=== P2 추출 (잠재력) ===")
        p2 = extract_pitcher_p2(frame)
        print(json.dumps(p2, ensure_ascii=False, indent=2))
    elif page == 'p3':
        print("=== P3 추출 ===")
        p3 = extract_pitcher_p3(frame)
        print(json.dumps(p3, ensure_ascii=False, indent=2))


# ─── 진입점 ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CPBV 골든 글러브 투수 정보 추출')
    parser.add_argument('--stream',      default=STREAM_URL,  help='MJPEG 스트림 URL')
    parser.add_argument('--mouse-host',  default=MOUSE_HOST,  help='게임 PC Tailscale IP')
    parser.add_argument('--mouse-port',  default=MOUSE_PORT,  type=int)
    parser.add_argument('--count',       default=0,           type=int,
                        help='추출할 선수 수 (0=무한)')
    parser.add_argument('--out',         default=DEFAULT_OUT, help='출력 JSON 경로')
    parser.add_argument('--page-wait',   default=1.5,         type=float,
                        help='페이지 전환 대기(초)')
    parser.add_argument('--player-wait', default=1.0,         type=float,
                        help='선수 전환 대기(초)')
    parser.add_argument('--test-image',  default=None,        help='단일 이미지 테스트')
    parser.add_argument('--test-page',   default='p1',        choices=['p1','p2','p3'],
                        help='테스트 페이지 (p1/p2/p3)')
    args = parser.parse_args()

    if args.test_image:
        test_frame(args.test_image, args.test_page)
    else:
        mouse = MouseClient(args.mouse_host, args.mouse_port)
        run(
            stream_url=args.stream,
            mouse=mouse,
            count=args.count,
            out_path=args.out,
            page_wait=args.page_wait,
            player_wait=args.player_wait,
        )
