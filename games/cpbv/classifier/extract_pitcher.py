"""
CPBV 골든 글러브 투수 정보 자동 추출기
--------------------------------------
게임에서 투수 카드 화면을 열어두면 자동으로:
  - P1: 이름, 오버롤, 포지션, 팀, 능력치 6종 추출
  - P2: 잠재력 5종 (장타억제, 속구구종, 침착성, 변화구구종, 견제) 1~4 값
  - P3: 체력바 구간(픽셀), 구종 + 등급(S/A/B/C/D/E) 추출

사용법:
  python -m games.cpbv.classifier.extract_pitcher
  python -m games.cpbv.classifier.extract_pitcher --count 30
  python -m games.cpbv.classifier.extract_pitcher --name golden_glove
  python -m games.cpbv.classifier.extract_pitcher --out D:/data/pitchers.json

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

# config_override.json에서 pt_pts 및 임계값 로드 (P2 잠재력 포인트)
_OVERRIDE_PATH = os.path.join(_HERE, '..', 'config_override.json')
_PT_PTS: dict = {}
POTENTIAL_THRESHOLD_MIN = 50
POTENTIAL_THRESHOLD_MAX = 222
if os.path.exists(_OVERRIDE_PATH):
    with open(_OVERRIDE_PATH, encoding='utf-8') as _f:
        _ovr = json.load(_f)
    _PT_PTS = _ovr.get('pt_pts', {})
    POTENTIAL_THRESHOLD_MIN = _ovr.get('potential_threshold_min', 50)
    POTENTIAL_THRESHOLD_MAX = _ovr.get('potential_threshold_max', 222)

GAME_W = STREAM_GAME_X2 - STREAM_GAME_X1
GAME_H = STREAM_GAME_Y2 - STREAM_GAME_Y1

# DEFAULT_OUT 은 아래 argparse 에서 --name 인자값에 따라 동적으로 생성됩니다.

# ─── OCR 초기화 (지연 로드) ──────────────────────────────────────────────────
_rec_pred = None

def _init_ocr():
    global _rec_pred
    if _rec_pred is not None:
        return
    print("[*] Surya OCR 초기화 중...")
    from surya.recognition import RecognitionPredictor
    try:
        from surya.recognition import FoundationPredictor
        _rec_pred = RecognitionPredictor(FoundationPredictor())
    except ImportError:
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


def ocr_batch(images_bgr):
    """여러 BGR 이미지를 한 번에 Surya OCR로 인식"""
    if not images_bgr:
        return []
    _init_ocr()
    
    pil_imgs = []
    bboxes = []
    for img in images_bgr:
        h, w = img.shape[:2]
        if h == 0 or w == 0:
            img = np.zeros((10, 10, 3), dtype=np.uint8)
            h, w = 10, 10
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        pil_imgs.append(Image.fromarray(rgb))
        bboxes.append([[0, 0, w, h]])
        
    try:
        results = _rec_pred(pil_imgs, bboxes=bboxes, math_mode=False)
    except Exception as e:
        print(f"[!] Batched OCR Error: {e}")
        return [[] for _ in images_bgr]
        
    output = []
    for res in results:
        if not res or not res.text_lines:
            output.append([])
            continue
        def _clean(t):
            t = re.sub(r'<[^>]+>', '', t)
            t = re.sub(r'\\[a-zA-Z,;!]+', '', t)
            t = re.sub(r'[{}^_]', '', t)
            return t.strip()
        lines = [(_clean(ln.text), ln.confidence) for ln in res.text_lines if ln.confidence > 0.25]
        output.append(lines)
        
    return output


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
    scale = max(3, scale_target_h // max(h, 1))
    up = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
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
        self._drain_flag = False
        self.ok     = False

    def run(self):
        cap = cv2.VideoCapture(self.url)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.ok = cap.isOpened()
        if not self.ok:
            print(f"[!] 스트림 연결 실패: {self.url}")
            return
        fail_count = 0
        while not self._stop.is_set():
            if self._drain_flag:
                # 버퍼 비우기 (실시간 프레임에 도달할 때까지 grab)
                # 최대 100개 프레임까지 비움 (무한루프 방지)
                for _ in range(100):
                    t0 = time.time()
                    grabbed = cap.grab()
                    t1 = time.time()
                    if not grabbed:
                        break
                    # grab이 블로킹(네트워크 대기)했다는 것은 실시간 프레임에 도달했음을 의미함 (10ms 이상 대기 발생)
                    if (t1 - t0) > 0.010:
                        break
                self._drain_flag = False
                
                # 마지막 비워진 프레임 하나만 retrieve 해서 최신 프레임으로 저장
                ret, frame = cap.retrieve()
                if ret:
                    with self._lock:
                        self._frame = frame
            ret, frame = cap.read()
            if ret:
                with self._lock:
                    self._frame = frame
                self.ok = True
                fail_count = 0
            else:
                fail_count += 1
                if fail_count > 100:  # 약 1초 동안 프레임 수신 불가 시 연결 해제로 판단
                    print("[!] 스트림 수신 시간 초과 (1초)")
                    self.ok = False
                    with self._lock:
                        self._frame = None
                    break
                time.sleep(0.01)
        cap.release()

    def get_latest(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def flush(self):
        self._drain_flag = True
        time.sleep(0.1)  # 버퍼 비우기 완료 대기

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
        if not grade or grade[0] not in 'ABCD':
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


from games.cpbv.classifier.dictionary import KBO_PITCHERS

def correct_pitcher_name(name: str) -> str:
    if not name:
        return ""
    
    import unicodedata
    
    # Preserve year suffix (e.g. '93) and single uppercase letter suffix (e.g. C)
    year_suffix = ""
    m_year = re.search(r"('\d{2})", name)
    if m_year:
        year_suffix = m_year.group(1)
        
    english_suffix = ""
    temp_name = name.replace(year_suffix, "").strip()
    m_eng = re.search(r"([A-Z])$", temp_name)
    if m_eng:
        english_suffix = m_eng.group(1)
        
    base_name = temp_name
    if english_suffix:
        base_name = base_name[:-1].strip()
        
    # 1. Check exact match first
    if base_name in KBO_PITCHERS:
        return base_name + english_suffix + year_suffix
        
    # 2. Substring match (longest first, length >= 2)
    for pitcher in sorted(KBO_PITCHERS, key=len, reverse=True):
        if len(pitcher) >= 2 and pitcher in base_name:
            return pitcher + english_suffix + year_suffix

    # 3. Prefix match with similarity search (handles prefix typos with trailing noise)
    for length in [4, 3, 2]:
        if len(base_name) >= length:
            prefix = base_name[:length]
            j_prefix = unicodedata.normalize('NFD', prefix)
            best_prefix_match = None
            best_prefix_score = 0.0
            for pitcher in KBO_PITCHERS:
                if len(pitcher) == length:
                    j_pitcher = unicodedata.normalize('NFD', pitcher)
                    score = difflib.SequenceMatcher(None, j_prefix, j_pitcher).ratio()
                    if score > best_prefix_score:
                        best_prefix_score = score
                        best_prefix_match = pitcher
            if best_prefix_score >= 0.8:
                return best_prefix_match + english_suffix + year_suffix

    # 4. Fallback to global Jamo-level similarity search
    best_match = None
    best_score = 0.0
    threshold = 0.75
    
    j_base = unicodedata.normalize('NFD', base_name)
    
    for pitcher in KBO_PITCHERS:
        j_pitcher = unicodedata.normalize('NFD', pitcher)
        score = difflib.SequenceMatcher(None, j_base, j_pitcher).ratio()
        
        # Length match bonus
        if len(base_name) == len(pitcher):
            score += 0.05
        # First letter match bonus
        if base_name and pitcher and base_name[0] == pitcher[0]:
            score += 0.05
            
        if score > best_score:
            best_score = score
            best_match = pitcher
            
    if best_score >= threshold:
        return best_match + english_suffix + year_suffix
        
    return base_name + english_suffix + year_suffix

def clean_player_name(name_raw: str) -> str:
    if not name_raw:
        return ""
    nc = re.sub(r"^[^\w\uAC00-\uD7A3']+", '', name_raw)
    m_year = re.search(r"'\d{2}", nc)
    if m_year:
        nc = nc[:m_year.end()]
    else:
        mid = len(nc) // 2
        if mid >= 2:
            first, second = nc[:mid], nc[mid:]
            ratio = difflib.SequenceMatcher(None, first, second).ratio()
            if ratio >= 0.5:
                nc = first
    cleaned = nc.strip()
    return correct_pitcher_name(cleaned)


# ─── P1 추출 ────────────────────────────────────────────────────────────────
def extract_pitcher_p1(frame, regions=None):
    """투수 Page 1: 이름, 오버롤, 포지션, 팀, 능력치 6종 (배치 OCR 적용)"""
    r = regions or PITCHER_P1

    # 크롭하고 스케일링할 이미지들을 준비
    crop_configs = [
        ('name', r['name_area'], 60),
        ('position', r['position_area'], 60),
        ('speed', r['stat_speed'], 60),
        ('control', r['stat_control'], 60),
        ('break', r['stat_break'], 60),
        ('stamina', r['stat_stamina'], 60),
        ('stuff', r['stat_stuff'], 60),
        ('defense', r['stat_defense'], 60),
    ]

    images_to_ocr = []
    for key, region, target_h in crop_configs:
        crop = crop_region(frame, region)
        if crop is None or crop.size == 0:
            # 빈 이미지 대응을 위한 더미
            up = np.zeros((target_h, target_h * 3, 3), dtype=np.uint8)
        else:
            h, w = crop.shape[:2]
            scale = max(3, target_h // h)
            up = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
            if key == 'name':
                kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
                up = cv2.filter2D(up, -1, kernel)
        images_to_ocr.append(up)

    # 배치 OCR 호출 (단 1번의 모델 인퍼런스!)
    ocr_results = ocr_batch(images_to_ocr)

    # 1) 이름 정제
    name_texts = ocr_results[0]
    name_raw = ' '.join(t for t, _ in name_texts) if name_texts else ''
    name = clean_player_name(name_raw)

    # 2) 포지션 정제
    pos_texts = ocr_results[1]
    pos_raw = ' '.join(t for t, _ in pos_texts) if pos_texts else ''
    pos_m = re.search(r'SP|RP|CP', pos_raw.upper())
    position = pos_m.group() if pos_m else pos_raw.strip()[:3]

    # 3) 팀 감지 (템플릿 매칭)
    team = detect_team(frame, r.get('team_logo', (0.04, 0.452, 0.10, 0.060)))

    # 4) 능력치 정제
    stats = {}
    stat_labels = ['speed', 'control', 'break', 'stamina', 'stuff', 'defense']
    for idx, label in enumerate(stat_labels):
        stat_texts = ocr_results[2 + idx]
        raw = ' '.join(t for t, _ in stat_texts) if stat_texts else ''
        m = re.search(r'\d+', raw)
        stats[label] = int(m.group()) if m else None

    # 오버롤 계산 (능력치 6개 평균, 소수점 버림)
    vals = [v for v in stats.values() if v is not None]
    overall = sum(vals) // 6 if len(vals) == 6 else None

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
    """OCR 결과 → 알려진 구종명으로 분류 (자모 분리 + 첫 글자 가중치 기반)"""
    import unicodedata
    from difflib import SequenceMatcher

    if not ocr_text:
        return ''

    # 1) 직접 포함 확인 (긴 이름 먼저 검사: 서클체인지업 > 체인지업 우선)
    for pitch in sorted(KNOWN_PITCH_NAMES, key=len, reverse=True):
        if pitch in ocr_text:
            return pitch

    best_name = ''
    best_score = 0.0

    # 자모 분리 후 유사도 비교
    ocr_jamo = unicodedata.normalize('NFD', ocr_text)

    for pitch in KNOWN_PITCH_NAMES:
        pitch_jamo = unicodedata.normalize('NFD', pitch)
        
        # Jamo sequence similarity
        score = SequenceMatcher(None, ocr_jamo, pitch_jamo).ratio()
        
        # First syllable match bonus (if first characters are identical)
        if ocr_text[0] == pitch[0]:
            score += 0.15
            
        if score > best_score:
            best_score = score
            best_name = pitch

    # 유사도가 너무 낮으면 OCR 원본 반환
    return best_name if best_score >= 0.4 else ocr_text


def extract_pitches(frame, regions=None):
    """구종 pitch1~5 이름+등급 OCR → [{'name': ..., 'grade': ...}, ...]
    OCR 결과를 알려진 구종명 목록으로 분류하여 정확도 향상 (배치 OCR 적용)"""
    r = regions or PITCHER_P3
    
    # 5개 구종 이름 영역 크롭 및 준비
    images_to_ocr = []
    valid_indices = []
    for i in range(1, 6):
        nreg = r.get(f'pitch{i}_name')
        if nreg:
            crop = crop_region(frame, nreg)
            if crop is not None and crop.size > 0:
                h, w = crop.shape[:2]
                scale = max(3, 60 // h)
                up = cv2.resize(crop, (w * scale, h * scale), interpolation=cv2.INTER_LANCZOS4)
                images_to_ocr.append(up)
                valid_indices.append(i)
                
    # 배치 OCR 호출
    ocr_results = ocr_batch(images_to_ocr)
    
    pitches = []
    for idx, i in enumerate(valid_indices):
        ntexts = ocr_results[idx]
        ntexts_sorted = sorted(ntexts, key=lambda x: -x[1])
        name = ''
        for ntext, _ in ntexts_sorted:
            nm = re.search(r'[\uAC00-\uD7A3]{2,}', ntext)
            if nm:
                name = _classify_pitch_name(nm.group())
                break
                
        # 등급: 템플릿 매칭 우선, 없으면 OCR fallback
        greg = r.get(f'pitch{i}_grade')
        grade = ''
        if greg:
            gcrop = crop_region(frame, greg)
            if gcrop is not None and gcrop.size > 0:
                grade = match_grade(gcrop) or ''
                if not grade:
                    gh, gw = gcrop.shape[:2]
                    gscale = max(6, 80 // gh)
                    gup = cv2.resize(gcrop, (gw * gscale, gh * gscale), interpolation=cv2.INTER_CUBIC)
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
    color_diff > 30 및 임계값 범위 내이면 해당 슬롯 활성
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
            color_diff = int(max(r, g, b)) - int(min(r, g, b))
            if color_diff > 30 and POTENTIAL_THRESHOLD_MIN <= gray_val < POTENTIAL_THRESHOLD_MAX:
                count += 1
            else:
                break            # 슬롯은 연속적 → 빈 슬롯 나오면 중단
        result[name] = count

    return result


# ─── 유효성 검증 함수 및 합의 대기 ──────────────────────────────────────────
# ─── 유효성 검증 함수 및 합의 대기 ──────────────────────────────────────────
def is_in_dictionary(name: str) -> bool:
    if not name:
        return False
    base = re.sub(r"'\d{2}", "", name)
    base = re.sub(r"[A-Z]$", "", base)
    base = base.strip()
    return base in KBO_PITCHERS

def is_valid_p1(p1):
    if not p1:
        return False
    name = p1.get('name')
    if not name or not isinstance(name, str) or len(name.strip()) == 0:
        return False
    if not is_in_dictionary(name):
        return False
    if p1.get('overall') is None:
        return False
    if p1.get('position') not in ['SP', 'RP', 'CP']:
        return False
    if not p1.get('team') or p1['team'] not in ['두산', '삼성', '한화', '롯데', 'KIA', '키움', 'SSG', 'LG', 'NC', 'KT']:
        return False
    stats = p1.get('stats', {})
    expected_stats = ['speed', 'control', 'break', 'stamina', 'stuff', 'defense']
    if len(stats) != 6:
        return False
    for k in expected_stats:
        if stats.get(k) is None:
            return False
    return True

def get_p1_invalid_reasons(p1):
    reasons = []
    if not p1:
        return ["데이터 없음(None)"]
    name = p1.get('name')
    if not name or not isinstance(name, str) or len(name.strip()) == 0:
        reasons.append(f"이름 누락/형식 이상(값: {repr(name)})")
    elif not is_in_dictionary(name):
        reasons.append(f"사전에 존재하지 않는 선수명(값: {repr(name)})")
    if p1.get('overall') is None:
        reasons.append("오버롤 누락(능력치 추출 실패로 인한 None)")
    if p1.get('position') not in ['SP', 'RP', 'CP']:
        reasons.append(f"포지션 이상(값: {repr(p1.get('position'))})")
    if not p1.get('team') or p1['team'] not in ['두산', '삼성', '한화', '롯데', 'KIA', '키움', 'SSG', 'LG', 'NC', 'KT']:
        reasons.append(f"팀 이름 인식 실패(값: {repr(p1.get('team'))})")
    stats = p1.get('stats', {})
    expected_stats = ['speed', 'control', 'break', 'stamina', 'stuff', 'defense']
    if len(stats) != 6:
        reasons.append(f"능력치 개수 부족(개수: {len(stats)})")
    for k in expected_stats:
        if stats.get(k) is None:
            reasons.append(f"능력치 {k} 누락(None)")
    return reasons

def is_valid_p2(p2):
    if not p2:
        return False
    expected_potentials = ['suppress_hr', 'fastball', 'composure', 'breaking', 'pickoff']
    if len(p2) != 5:
        return False
    for k in expected_potentials:
        if p2.get(k) is None:
            return False
    return True

def get_p2_invalid_reasons(p2):
    reasons = []
    if not p2:
        return ["데이터 없음(None)"]
    expected_potentials = ['suppress_hr', 'fastball', 'composure', 'breaking', 'pickoff']
    if len(p2) != 5:
        reasons.append(f"잠재력 개수 부족(개수: {len(p2)})")
    for k in expected_potentials:
        if p2.get(k) is None:
            reasons.append(f"잠재력 {k} 누락(None)")
    return reasons

def is_valid_p3(p3):
    if not p3:
        return False
    stamina = p3.get('stamina')
    if not stamina:
        return False
    px_widths = stamina.get('px_widths', [])
    if sum(px_widths) <= 20:
        return False
    pitches = p3.get('pitches', [])
    if not pitches:
        return False
    pnames = [x['name'] for x in pitches]
    if len(pnames) != len(set(pnames)):  # 중복 구종 확인
        return False
    for p in pitches:
        name = p.get('name')
        grade = p.get('grade')
        if not name or name not in KNOWN_PITCH_NAMES:
            return False
        if not grade or grade == '?' or grade not in ['S', 'A', 'B', 'C', 'D', 'E']:
            return False
    return True

def get_p3_invalid_reasons(p3):
    reasons = []
    if not p3:
        return ["데이터 없음(None)"]
    stamina = p3.get('stamina')
    if not stamina:
        reasons.append("체력 정보 누락")
    else:
        px_widths = stamina.get('px_widths', [])
        if sum(px_widths) <= 20:
            reasons.append(f"체력바 픽셀 부족(합: {sum(px_widths)})")
    pitches = p3.get('pitches', [])
    if not pitches:
        reasons.append("구종 정보 누락")
    else:
        pnames = [x['name'] for x in pitches]
        if len(pnames) != len(set(pnames)):
            reasons.append(f"구종 중복(목록: {pnames})")
        for idx, p in enumerate(pitches):
            name = p.get('name')
            grade = p.get('grade')
            if not name or name not in KNOWN_PITCH_NAMES:
                reasons.append(f"구종{idx+1} 이름 이상(값: {repr(name)})")
            if not grade or grade == '?' or grade not in ['S', 'A', 'B', 'C', 'D', 'E']:
                reasons.append(f"구종{idx+1} 등급 이상(값: {repr(grade)})")
    return reasons

def wait_for_consensus(stream, extract_fn, is_valid_fn, timeout=10.0, poll_interval=0.1, get_invalid_reasons_fn=None, label="Page", required_consensus=1):
    """
    스트림에서 프레임을 읽어 유효(valid) 결과가 나올 때까지 대기합니다.
    required_consensus가 1보다 크면 해당 횟수만큼 연속해서 동일한 유효한 결과가 나와야 반환합니다.
    """
    start_time = time.time()
    last_debug_time = 0
    
    consecutive_count = 0
    last_res = None
    
    while time.time() - start_time < timeout:
        if not stream.ok:
            print("\n[!] 스트림 연결이 해제되었습니다. 추출을 중단하고 종료합니다.")
            sys.exit(1)
            
        frame = stream.get_latest()
        if frame is None:
            time.sleep(poll_interval)
            continue
            
        res = extract_fn(frame)
        is_valid = res and is_valid_fn(res)
        
        if is_valid:
            if required_consensus <= 1:
                # 1회 유효 성공 즉시 반환!
                return res
            else:
                if last_res is not None and res == last_res:
                    consecutive_count += 1
                else:
                    consecutive_count = 1
                last_res = res
                
                if consecutive_count >= required_consensus:
                    return res
        else:
            consecutive_count = 0
            last_res = None
            
            # 유효하지 않은 경우 주기적 디버그 출력 (2초 간격)
            curr = time.time()
            if curr - last_debug_time > 2.0:
                reasons = get_invalid_reasons_fn(res) if get_invalid_reasons_fn else ["유효성 실패"]
                print(f"    [디버그] {label} 유효성 실패: {', '.join(reasons)}")
                print(f"      -> 현재 추출값: {res}")
                last_debug_time = curr
            
        time.sleep(poll_interval)
        
    return None


def wait_for_transition(stream, before_frame, label=""):
    """스트림 화면이 이전(before_frame)과 유의미하게 달라질 때까지 대기"""
    if before_frame is None:
        time.sleep(1.5)
        return
    
    start_t = time.time()
    transitioned = False
    
    h, w = before_frame.shape[:2]
    cy1, cy2 = int(h * 0.4), int(h * 0.8)
    cx1, cx2 = int(w * 0.2), int(w * 0.8)
    
    before_small = cv2.resize(before_frame[cy1:cy2, cx1:cx2], (32, 32))
    before_gray = cv2.cvtColor(before_small, cv2.COLOR_BGR2GRAY)
    
    while time.time() - start_t < 4.0:
        frame = stream.get_latest()
        if frame is None:
            time.sleep(0.05)
            continue
            
        small = cv2.resize(frame[cy1:cy2, cx1:cx2], (32, 32))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(before_gray, gray)
        mean_diff = np.mean(diff)
        
        if mean_diff > 12.0:
            transitioned = True
            break
        time.sleep(0.05)
        
    if transitioned:
        # 화면 변화가 시작된 후 완전히 멈출 때까지 대기 (안정화)
        last_gray = None
        stable_start = time.time()
        while time.time() - stable_start < 1.0:
            frame = stream.get_latest()
            if frame is None:
                time.sleep(0.05)
                continue
            small = cv2.resize(frame[cy1:cy2, cx1:cx2], (32, 32))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if last_gray is not None:
                diff = cv2.absdiff(last_gray, gray)
                mean_diff = np.mean(diff)
                if mean_diff < 1.0:
                    break
            last_gray = gray
            time.sleep(0.05)
        time.sleep(0.2)
        stream.flush()
    else:
        print(f"    [!] {label} 화면 전환 감지 타임아웃, 1.5초 기본 대기")
        time.sleep(1.5)
        stream.flush()


# ─── 메인 추출 루프 ──────────────────────────────────────────────────────────
def run(stream_url: str, mouse: MouseClient, count: int, out_path: str,
        page_wait: float = 1.5, player_wait: float = 1.0):
    """
    골든 글러브 투수 카드 순회 추출
      - 시작 전: 게임에서 투수 카드 P1 화면을 열어두세요
      - count: 추출할 선수 수 (0 = 무한)
    """
    _init_ocr()
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
    if mouse.connected:
        print("[*] 마우스 서버 연결 감지 - 초기 창 포커싱 수행...")
        mouse.click_ratio(0.5, 0.5, focus_first=True)
    time.sleep(5)

    players = []
    seen_players = set()   # 중복 감지용 (이름, 팀, 오버롤, 스탯 6종)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    if os.path.exists(out_path):
        try:
            with open(out_path, 'r', encoding='utf-8') as f:
                players = json.load(f)
            for p in players:
                st = p.get('stats', {})
                stats_tuple = (st.get('speed'), st.get('control'), st.get('break'), st.get('stamina'), st.get('stuff'), st.get('defense'))
                seen_players.add((p['name'], p['team'], p['overall'], stats_tuple))
            print(f"[*] 기존 파일 로드됨: {len(players)}명 이어서 추출 진행")
        except Exception as e:
            print(f"[!] 기존 파일 로드 실패, 새로 시작합니다: {e}")

    idx = len(players)
    while (count == 0 or idx < count):
        # ── P1 추출 (3회 연속 동일 검증) ────────────────────────
        print(f"\n[{idx+1}] ── P1 추출 중...")
        stream.flush()
        p1 = wait_for_consensus(stream, extract_pitcher_p1, is_valid_p1, timeout=40.0, poll_interval=0.1, get_invalid_reasons_fn=get_p1_invalid_reasons, label="P1")
        
        if p1 is None:
            print("    [!] P1 안정화 타임아웃 (3회 연속 검증 실패) -> 대기 후 재시도")
            time.sleep(page_wait)
            continue  # P1은 제자리에서 다시 시작
            
        print(f"    이름={p1['name']}  전체={p1['overall']}  포지션={p1['position']}  팀={p1['team']}")
        print(f"    능력치={p1['stats']}")
 
        # 중복 감지 (count=0일 때만 자동 종료)
        st = p1.get('stats', {})
        stats_tuple = (st.get('speed'), st.get('control'), st.get('break'), st.get('stamina'), st.get('stuff'), st.get('defense'))
        player_key = (p1['name'], p1['team'], p1['overall'], stats_tuple)
        if count == 0 and player_key in seen_players:
            print(f"\n[*] 중복 감지: '{p1['name']} ({p1['team']}, 오버롤={p1['overall']})' → 전체 순환 완료, 종료")
            break
 
        # ── P1 → P2 ──────────────────────────────────────
        if mouse.connected:
            before_frame = stream.get_latest()
            mouse.click_ui('next_page')
            wait_for_transition(stream, before_frame, "P1 -> P2")
 
        # ── P2 추출 (3회 연속 동일 검증) ────────────────────────
        print(f"[{idx+1}] ── P2 추출 중...")
        p2 = wait_for_consensus(stream, extract_pitcher_p2, is_valid_p2, timeout=10.0, poll_interval=0.1, get_invalid_reasons_fn=get_p2_invalid_reasons, label="P2", required_consensus=3)
        
        if p2 is None:
            print("    [!] P2 안정화 타임아웃 (3회 연속 검증 실패) -> P1 복귀 후 재시도")
            if mouse.connected:
                before_frame = stream.get_latest()
                mouse.click_ui('prev_page')
                wait_for_transition(stream, before_frame, "P2 -> P1")
            continue
 
        print(f"    잠재력={p2}")
 
        # ── P2 → P3 ──────────────────────────────────────
        if mouse.connected:
            before_frame = stream.get_latest()
            mouse.click_ui('next_page')
            wait_for_transition(stream, before_frame, "P2 -> P3")
 
        # ── P3 추출 (3회 연속 동일 검증) ────────────────────────
        print(f"[{idx+1}] ── P3 추출 중...")
        p3 = wait_for_consensus(stream, extract_pitcher_p3, is_valid_p3, timeout=30.0, poll_interval=0.1, get_invalid_reasons_fn=get_p3_invalid_reasons, label="P3")
        
        if p3 is None:
            print("    [!] P3 안정화 타임아웃 (3회 연속 검증 실패) -> P1 복귀 후 재시도")
            if mouse.connected:
                before_frame = stream.get_latest()
                mouse.click_ui('prev_page')
                wait_for_transition(stream, before_frame, "P3 -> P2")
                
                before_frame = stream.get_latest()
                mouse.click_ui('prev_page')
                wait_for_transition(stream, before_frame, "P2 -> P1")
            continue

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
        seen_players.add(player_key)

        # 중간 저장
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(players, f, ensure_ascii=False, indent=2)
        print(f"    → 저장됨 ({len(players)}명)")

        idx += 1
        if count and idx >= count:
            break

        # ── 다음 선수로 (P3 → next_player → prev_page×2 → P1) ──
        if mouse.connected:
            before_frame = stream.get_latest()
            mouse.click_ui('next_player')
            wait_for_transition(stream, before_frame, "Next Player")
            
            before_frame = stream.get_latest()
            mouse.click_ui('prev_page')
            wait_for_transition(stream, before_frame, "P3 -> P2")
            
            before_frame = stream.get_latest()
            mouse.click_ui('prev_page')
            wait_for_transition(stream, before_frame, "P2 -> P1")

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
    parser = argparse.ArgumentParser(description='CPBV 투수 정보 추출')
    parser.add_argument('--stream',      default=STREAM_URL,  help='MJPEG 스트림 URL')
    parser.add_argument('--mouse-host',  default=MOUSE_HOST,  help='게임 PC Tailscale IP')
    parser.add_argument('--mouse-port',  default=MOUSE_PORT,  type=int)
    parser.add_argument('--count',       default=0,           type=int,
                        help='추출할 선수 수 (0=무한)')
    parser.add_argument('--name',        default='golden_glove', help='카드 종류 이름 (출력 파일명 접두사)')
    parser.add_argument('--out',         default=None,        help='출력 JSON 경로 (기본값: data/{name}_pitchers_YYYYMMDD_HHMMSS.json)')
    parser.add_argument('--page-wait',   default=1.5,         type=float,
                        help='페이지 전환 대기(초)')
    parser.add_argument('--player-wait', default=1.0,         type=float,
                        help='선수 전환 대기(초)')
    parser.add_argument('--test-image',  default=None,        help='단일 이미지 테스트')
    parser.add_argument('--test-page',   default='p1',        choices=['p1','p2','p3'],
                        help='테스트 페이지 (p1/p2/p3)')
    args = parser.parse_args()

    if not args.out:
        args.out = os.path.join(_HERE, '..', '..', '..', 'data',
                                f'{args.name}_pitchers_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')

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
