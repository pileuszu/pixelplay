"""
CPBV 선수 카드 데이터 자동 추출기
- 게임 화면을 MJPEG 스트림으로 수신 (OBS Virtual Camera / NDI)
- Surya OCR로 한국어 텍스트 추출
- 게임 PC 마우스 서버로 자동 네비게이션

사용법:
  python capture_player_data.py --stream http://GAME_PC_IP:8080/video  (OBS MJPEG 스트림)
  python capture_player_data.py --mouse-host GAME_PC_TAILSCALE_IP
"""
import cv2
import json
import time
import socket
import re
import os
import argparse
import numpy as np
from datetime import datetime
from PIL import Image

# ─── 설정 ───────────────────────────────────────────
STREAM_URL = None          # OBS MJPEG 스트림 URL (--stream 으로 설정)
MOUSE_HOST = None          # 게임 PC Tailscale IP (--mouse-host 로 설정)
MOUSE_PORT = 9999

OUTPUT_DIR = r"D:\Repos\data\tools\player_data"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ─── OCR 초기화 (Surya) ────────────────────
print("[*] Surya OCR 초기화 중...")
from surya.recognition import RecognitionPredictor
from surya.detection import DetectionPredictor
try:
    from surya.recognition import FoundationPredictor
    _rec_pred = RecognitionPredictor(FoundationPredictor())
except ImportError:
    _rec_pred = RecognitionPredictor()
_det_pred = DetectionPredictor()
print("[*] OCR 준비 완료")

# ─── 마우스 클라이언트 ─────────────────────────────
class MouseClient:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = None
        self._connect()

    def _connect(self):
        if not self.host:
            return
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            print(f"[+] 마우스 서버 연결됨: {self.host}:{self.port}")
        except Exception as e:
            print(f"[!] 마우스 서버 연결 실패: {e}")
            self.sock = None

    def send(self, cmd):
        if not self.sock:
            return
        try:
            self.sock.sendall((json.dumps(cmd) + '\n').encode())
            resp = self.sock.recv(1024)
            return json.loads(resp.decode())
        except:
            self._connect()

    def click(self, x, y):
        return self.send({'action': 'click', 'x': x, 'y': y})

    def scroll(self, x, y, amount=-3):
        return self.send({'action': 'scroll', 'x': x, 'y': y, 'amount': amount})

    def key(self, k):
        return self.send({'action': 'key', 'key': k})

# ─── 화면 캡처 ─────────────────────────────────────
def get_frame(cap):
    ret, frame = cap.read()
    if not ret:
        return None
    return frame

def preprocess_for_ocr(img, region=None):
    """OCR 정확도를 높이기 위한 이미지 전처리"""
    if region:
        x, y, w, h = region
        img = img[y:y+h, x:x+w]
    
    # 2배 확대 (작은 텍스트 인식률 향상)
    img = cv2.resize(img, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    # 그레이스케일
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 대비 향상
    gray = cv2.convertScaleAbs(gray, alpha=1.5, beta=10)
    # 노이즈 제거
    gray = cv2.GaussianBlur(gray, (1, 1), 0)
    return gray

def ocr_region(img, region=None):
    """특정 영역에서 텍스트 추출 (Surya)"""
    proc = preprocess_for_ocr(img, region)
    # BGR→RGB→PIL
    rgb = cv2.cvtColor(proc if len(proc.shape)==3 else cv2.cvtColor(proc, cv2.COLOR_GRAY2BGR),
                       cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    rec_results = _rec_pred([pil_img], [['ko', 'en']], _det_pred)
    texts = [line.text for line in rec_results[0].text_lines if line.confidence > 0.25]
    return ' '.join(texts)

# ─── 선수 데이터 파싱 ──────────────────────────────
def parse_stat(text, label):
    """능력치 수치 파싱 (예: '컨택 85' → 85)"""
    patterns = [
        rf'{label}\s*[:：]?\s*(\d+)',
        rf'(\d+)\s*{label}',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return int(m.group(1))
    return None

def extract_batter_stats(text):
    """타자 능력치 추출"""
    stat_keys = {
        'name': None,
        '컨택': None, '파워': None, '스피드': None,
        '수비': None, '어깨': None, '순발력': None,
        '장타율': None, '출루율': None,
    }
    for key in stat_keys:
        if key == 'name':
            # 선수 이름은 첫 줄 또는 큰 글씨 부분
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            # 한국 이름 패턴 (2-4자 한글)
            for line in lines:
                if re.match(r'^[가-힣]{2,4}$', line.strip()):
                    stat_keys['name'] = line.strip()
                    break
        else:
            val = parse_stat(text, key)
            if val is not None:
                stat_keys[key] = val
    return stat_keys

def extract_pitcher_stats(text):
    """투수 능력치 추출"""
    stat_keys = {
        'name': None,
        '구속': None, '제구': None, '변화구': None,
        '스태미나': None, '멘탈': None,
        '방어율': None, '탈삼진': None,
    }
    for key in stat_keys:
        if key == 'name':
            lines = [l.strip() for l in text.split('\n') if l.strip()]
            for line in lines:
                if re.match(r'^[가-힣]{2,4}$', line.strip()):
                    stat_keys['name'] = line.strip()
                    break
        else:
            val = parse_stat(text, key)
            if val is not None:
                stat_keys[key] = val
    return stat_keys

# ─── 메인 추출 루프 ────────────────────────────────
def run_extraction(stream_url, mouse_client, player_type='bat'):
    """도감 화면을 순회하며 선수 데이터 추출"""
    
    print(f"[*] 스트림 연결: {stream_url}")
    cap = cv2.VideoCapture(stream_url if stream_url else 0)
    
    if not cap.isOpened():
        print(f"[!] 스트림 연결 실패: {stream_url}")
        return
    
    players = []
    output_file = os.path.join(OUTPUT_DIR, f'{player_type}_data_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
    
    print(f"[*] 추출 시작. 저장 위치: {output_file}")
    print(f"[*] 게임에서 도감 → {'타자' if player_type=='bat' else '투수'} 목록 화면을 열어주세요")
    print(f"[*] 5초 후 시작... (Ctrl+C로 중단)")
    time.sleep(5)
    
    page = 0
    consecutive_empty = 0
    
    while True:
        frame = get_frame(cap)
        if frame is None:
            time.sleep(0.5)
            continue
        
        # 전체 화면 OCR
        text = ocr_region(frame)
        print(f"\n--- 페이지 {page+1} ---")
        print(f"OCR 결과: {text[:200]}")
        
        # 능력치 파싱
        if player_type == 'bat':
            data = extract_batter_stats(text)
        else:
            data = extract_pitcher_stats(text)
        
        data['page'] = page + 1
        data['raw_text'] = text
        data['timestamp'] = datetime.now().isoformat()
        
        if data.get('name') or any(v for k, v in data.items() if k not in ('name', 'page', 'raw_text', 'timestamp') and v):
            players.append(data)
            print(f"[+] 선수 추출: {data.get('name', '이름미상')} - {data}")
            consecutive_empty = 0
        else:
            consecutive_empty += 1
            print(f"[?] 데이터 없음 ({consecutive_empty}번 연속)")
        
        # 중간 저장
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(players, f, ensure_ascii=False, indent=2)
        
        # 다음 선수로 이동 (스크롤 또는 다음 버튼)
        if mouse_client and mouse_client.sock:
            # 게임 화면 중앙 기준으로 다음 버튼 좌표 (조정 필요)
            # TODO: 실제 게임 해상도에 맞게 좌표 조정
            mouse_client.key('right')  # 또는 다음 버튼 클릭
        
        page += 1
        time.sleep(1.5)  # 화면 전환 대기
        
        # 10번 연속 데이터 없으면 종료
        if consecutive_empty >= 10:
            print(f"[*] 10번 연속 데이터 없음 → 추출 완료")
            break
    
    cap.release()
    print(f"\n[*] 총 {len(players)}명 추출 완료")
    print(f"[*] 저장: {output_file}")
    return players

# ─── 단일 스크린샷 테스트 ──────────────────────────
def test_single_screenshot(img_path):
    """스크린샷 파일로 OCR 테스트"""
    print(f"[*] 이미지 테스트: {img_path}")
    img = cv2.imread(img_path)
    if img is None:
        print(f"[!] 이미지 로드 실패")
        return
    
    text = ocr_region(img)
    print(f"OCR 결과:\n{text}")
    
    bat = extract_batter_stats(text)
    pit = extract_pitcher_stats(text)
    print(f"\n타자 파싱: {bat}")
    print(f"투수 파싱: {pit}")

# ─── 진입점 ────────────────────────────────────────
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--stream', help='OBS MJPEG 스트림 URL (예: http://100.x.x.x:8080/video)')
    parser.add_argument('--mouse-host', help='게임 PC Tailscale IP')
    parser.add_argument('--type', choices=['bat', 'pit'], default='bat', help='타자(bat) 또는 투수(pit)')
    parser.add_argument('--test-image', help='단일 이미지로 OCR 테스트')
    args = parser.parse_args()
    
    if args.test_image:
        test_single_screenshot(args.test_image)
    else:
        mouse = MouseClient(args.mouse_host, MOUSE_PORT) if args.mouse_host else MouseClient(None, None)
        run_extraction(args.stream, mouse, args.type)
