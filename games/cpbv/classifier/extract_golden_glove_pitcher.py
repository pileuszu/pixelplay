"""
CPBV 골든 글러브 투수 정보 자동 추출기
--------------------------------------
게임에서 투수 카드 화면을 열어두면 자동으로:
  - P1: 이름, 오버롤, 포지션, 팀, 능력치 6종 추출
  - P3: 체력바 구간(픽셀), 구종 + 등급(S/A/B/C/D/E) 추출

사용법:
  python -m games.cpbv.classifier.extract_golden_glove_pitcher
  python -m games.cpbv.classifier.extract_golden_glove_pitcher --count 30
  python -m games.cpbv.classifier.extract_golden_glove_pitcher --out D:/data/pitchers.json

흐름:
  P1 추출 → next_page×2 → P3 추출 → 저장
  → next_player → prev_page×2 → P1 추출 → ...
"""

import cv2
import re
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
sys.path.insert(0, os.path.join(_HERE, '..', '..', '..'))

from games.cpbv.config_cpbv import (
    STREAM_URL, MOUSE_HOST, MOUSE_PORT,
    STREAM_GAME_X1, STREAM_GAME_Y1, STREAM_GAME_X2, STREAM_GAME_Y2,
    PITCHER_P1, PITCHER_P3, UI,
    TEAM_TEMPLATES_DIR,
)

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
    """단일 bbox Surya OCR → [(text, confidence), ...]"""
    _init_ocr()
    h, w = img_bgr.shape[:2]
    if h == 0 or w == 0:
        return []
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    bboxes = [[[0, 0, w, h]]]
    result = _rec_pred([pil_img], [['ko', 'en']], bboxes=bboxes)
    return [(ln.text, ln.confidence) for ln in result[0].text_lines]


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


# ─── 스트림 스레드 ───────────────────────────────────────────────────────────
class StreamThread(threading.Thread):
    def __init__(self, url):
        super().__init__(daemon=True)
        self.url = url
        self._cap = None
        self._frame = None
        self._lock = threading.Lock()
        self.ok = False

    def run(self):
        self._cap = cv2.VideoCapture(self.url)
        if not self._cap.isOpened():
            print(f"[!] 스트림 연결 실패: {self.url}")
            return
        self.ok = True
        while True:
            ret, frame = self._cap.read()
            if not ret:
                time.sleep(0.05)
                continue
            with self._lock:
                self._frame = frame

    def get_latest(self):
        with self._lock:
            return self._frame.copy() if self._frame is not None else None

    def stop(self):
        if self._cap:
            self._cap.release()


# ─── 마우스 클라이언트 ───────────────────────────────────────────────────────
class MouseClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self.connected = False
        if host:
            self._connect()

    def _connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.settimeout(3)
            self.sock.connect((self.host, self.port))
            self.connected = True
            print(f"[+] 마우스 서버 연결: {self.host}:{self.port}")
        except Exception as e:
            print(f"[!] 마우스 연결 실패: {e}")
            self.connected = False

    def _send(self, cmd):
        if not self.sock:
            return None
        try:
            self.sock.sendall((json.dumps(cmd) + '\n').encode())
            resp = self.sock.recv(1024)
            return json.loads(resp.decode())
        except Exception:
            self._connect()
            return None

    def click_ratio(self, rx, ry):
        """창 내 비율 좌표로 클릭"""
        ax = int(STREAM_GAME_X1 + rx * GAME_W)
        ay = int(STREAM_GAME_Y1 + ry * GAME_H)
        return self._send({'action': 'click', 'x': ax, 'y': ay})

    def click_ui(self, key):
        """UI 딕셔너리 키로 클릭 (next_player, next_page 등)"""
        rx, ry = UI[key]
        return self.click_ratio(rx, ry)


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
        img = cv2.imread(os.path.join(tmpl_dir, fname))
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

    # 이름
    name_raw = ocr_text(frame, r['name_area'])
    name_m = re.search(r'[\uAC00-\uD7A3]{2,5}', name_raw)
    name = name_m.group() if name_m else name_raw.strip()

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


def extract_pitches(frame, regions=None):
    """구종 pitch1~5 이름+등급 OCR → [{'name': ..., 'grade': ...}, ...]"""
    r = regions or PITCHER_P3
    pitches = []
    for i in range(1, 6):
        nreg = r.get(f'pitch{i}_name')
        greg = r.get(f'pitch{i}_grade')
        if not nreg or not greg:
            continue

        # 이름
        name_raw = ocr_text(frame, nreg, scale_target_h=60)
        nm = re.search(r'[\uAC00-\uD7A3]{2,}', name_raw)
        name = nm.group() if nm else ''

        # 등급 OCR (배지 영역 고배율)
        gcrop = crop_region(frame, greg)
        grade = ''
        if gcrop is not None:
            gh, gw = gcrop.shape[:2]
            if gw > 0 and gh > 0:
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
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    idx = 0
    while (count == 0 or idx < count):
        frame = stream.get_latest()
        if frame is None:
            time.sleep(0.3)
            continue

        print(f"\n[{idx+1}] ── P1 추출 중...")
        p1 = extract_pitcher_p1(frame)
        print(f"    이름={p1['name']}  전체={p1['overall']}  포지션={p1['position']}  팀={p1['team']}")
        print(f"    능력치={p1['stats']}")

        # P3으로 이동 (next_page × 2)
        if mouse.connected:
            mouse.click_ui('next_page')
            time.sleep(page_wait)
            mouse.click_ui('next_page')
            time.sleep(page_wait)

        frame = stream.get_latest()
        print(f"[{idx+1}] ── P3 추출 중...")
        p3 = extract_pitcher_p3(frame)

        if p3['stamina']:
            st = p3['stamina']
            print(f"    체력: {st['seg_count']}구간({st['role']})  px={st['px_widths']}")
        pitches_str = '  '.join(f"{p['name']}:{p['grade']}" for p in p3['pitches'])
        print(f"    구종: {pitches_str}")

        # 통합 레코드
        record = {
            **p1,
            'stamina_detail': p3['stamina'],
            'pitches': p3['pitches'],
            'page_idx': idx + 1,
            'timestamp': datetime.now().isoformat(timespec='seconds'),
        }
        players.append(record)

        # 중간 저장
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(players, f, ensure_ascii=False, indent=2)
        print(f"    → 저장됨 ({len(players)}명)")

        idx += 1
        if count and idx >= count:
            break

        # 다음 선수로 (P3에서 next_player → prev_page×2 → P1)
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
def test_frame(img_path: str):
    """스크린샷으로 P1/P3 추출 테스트"""
    _init_ocr()
    frame = cv2.imread(img_path)
    if frame is None:
        print(f"[!] 이미지 로드 실패: {img_path}")
        return

    print("=== P1 추출 ===")
    p1 = extract_pitcher_p1(frame)
    print(json.dumps(p1, ensure_ascii=False, indent=2))

    print("\n=== P3 추출 ===")
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
    args = parser.parse_args()

    if args.test_image:
        test_frame(args.test_image)
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
