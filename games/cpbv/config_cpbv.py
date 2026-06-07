"""
CPBV 게임 창 좌표 설정
get_window_rect.ps1 실행 결과를 여기에 입력하세요.
"""

# ─── 게임 창 절대 좌표 (게임 PC 스크린 기준) ─────────────────────────
WINDOW_LEFT   = 912
WINDOW_TOP    = 140
WINDOW_RIGHT  = 1648
WINDOW_BOTTOM = 1459

WINDOW_WIDTH  = WINDOW_RIGHT  - WINDOW_LEFT   # 736
WINDOW_HEIGHT = WINDOW_BOTTOM - WINDOW_TOP    # 1319

# ─── 스트림 설정 ─────────────────────────────────────────────────────
# OBS 출력 해상도 (ffmpeg 스트림 해상도)
STREAM_WIDTH  = 1920
STREAM_HEIGHT = 1080

# 스트림 내 게임창 위치 (OBS 중앙 맞춤 기준, 자동 계산)
# 게임창 비율: 736/1319 = 0.558
# 720px 높이에 맞추면: 너비 = 720 * (736/1319) ≈ 402px
# 좌여백: (1280 - 402) / 2 ≈ 439px
_game_h = STREAM_HEIGHT
_game_w = int(_game_h * WINDOW_WIDTH / WINDOW_HEIGHT)
_game_x = (STREAM_WIDTH - _game_w) // 2
STREAM_GAME_X1 = _game_x          # ≈ 439
STREAM_GAME_Y1 = 0
STREAM_GAME_X2 = _game_x + _game_w  # ≈ 841
STREAM_GAME_Y2 = STREAM_HEIGHT     # 720

# ─── 네트워크 설정 ────────────────────────────────────────────────────
STREAM_URL  = "http://100.118.216.59:8080/video"
MOUSE_HOST  = "100.118.216.59"
MOUSE_PORT  = 9999

# ─── UI 요소 좌표 (창 내 비율 0.0~1.0) ───────────────────────────────
# calibrate.py 실행 후 실제 값으로 업데이트됩니다.

UI = {
    # 선수 이동 버튼 (카드 이미지 양쪽)
    "next_player":  (0.94, 0.25),   # 우측 >> (다음 선수)
    "prev_player":  (0.06, 0.25),   # 좌측 << (이전 선수)

    # 페이지 이동 버튼 (하단)
    "next_page":    (0.87, 0.908),  # 하단 우측 >> (다음 페이지)
    "prev_page":    (0.13, 0.908),  # 하단 좌측 << (이전 페이지)

    # 닫기
    "close":        (0.50, 0.960),  # 하단 중앙 X
}

# ─── OCR 영역 (창 내 비율: x, y, w, h) ───────────────────────────────
# 타자 Page 1
BATTER_P1 = {
    "name_area":     (0.15, 0.455, 0.65, 0.055),  # 선수명
    "overall_area":  (0.68, 0.455, 0.10, 0.055),  # 오버롤 숫자
    "position_area": (0.78, 0.455, 0.18, 0.055),  # 포지션 배지 (SS, SP...)
    "team_logo":     (0.04, 0.452, 0.10, 0.060),  # 팀 로고 영역
    "setdeck_area":  (0.55, 0.618, 0.15, 0.050),  # 세트덱 스코어 숫자
    "launch_area":   (0.78, 0.618, 0.18, 0.050),  # 평균 발사각 숫자
    # 능력치 6개 (파워/인내/정확/주루/선구/수비)
    "stat_power":    (0.48, 0.668, 0.16, 0.040),
    "stat_endure":   (0.78, 0.668, 0.16, 0.040),
    "stat_contact":  (0.48, 0.710, 0.16, 0.040),
    "stat_run":      (0.78, 0.710, 0.16, 0.040),
    "stat_eye":      (0.48, 0.752, 0.16, 0.040),
    "stat_defense":  (0.78, 0.752, 0.16, 0.040),
}

# 타자 Page 3 - 핫/콜드존
# 9칸(3행×3열) 포인트 픽커로 감지 (F키)
HOTZONE_ROWS = 3
HOTZONE_COLS = 3

BATTER_P3 = {}  # 영역 없음 - pt_pts 기반 감지

# 타자 Page 2 - 잠재력 (5개 항목, 2열 배치)
# 슬롯 총 개수는 pt_pts 픽셀 포인트로 감지 (F키 픽커)
# 항목 순서: 풀스윙, 번트, 클러치, 송구, 도루
POTENTIAL_BAR_TOTAL = 4
POTENTIAL_NAMES_BATTER  = ["fullswing", "bunt", "clutch", "throw", "steal"]
POTENTIAL_NAMES_PITCHER = ["suppress_hr", "fastball", "composure", "breaking", "pickoff"]

BATTER_P2 = {}  # 영역 없음 - pt_pts 기반 감지

# 투수 Page 1
PITCHER_P1 = {
    "name_area":     (0.15, 0.455, 0.65, 0.055),
    "overall_area":  (0.68, 0.455, 0.10, 0.055),  # 오버롤 숫자
    "position_area": (0.78, 0.455, 0.18, 0.055),
    "team_logo":     (0.04, 0.452, 0.10, 0.060),
    "setdeck_area":  (0.55, 0.618, 0.15, 0.050),
    "stamina_bar":   (0.50, 0.628, 0.45, 0.030),  # 현재 체력 바
    "stat_speed":    (0.48, 0.668, 0.16, 0.040),  # 구속
    "stat_control":  (0.78, 0.668, 0.16, 0.040),  # 제구
    "stat_break":    (0.48, 0.710, 0.16, 0.040),  # 변화
    "stat_stamina":  (0.78, 0.710, 0.16, 0.040),  # 지구력
    "stat_stuff":    (0.48, 0.752, 0.16, 0.040),  # 구위
    "stat_defense":  (0.78, 0.752, 0.16, 0.040),  # 수비
}

# 투수 Page 3 - 체력바 상세 + 구종
PITCHER_P3 = {
    "stamina_bar_detail": (0.15, 0.510, 0.75, 0.040),  # 체력 바 (상세)
    "pitches_area":        (0.08, 0.580, 0.85, 0.320),  # 구종 전체 영역
}

# 투수 Page 2 - 잠재력 (5개 항목, 2열 배치)
# 항목 순서: 장타억제력, 속구구종, 침착성, 변화구구종, 견제
PITCHER_P2 = {}  # 영역 없음 - pt_pts 기반 감지

# ─── 팀 로고 템플릿 경로 ─────────────────────────────────────────────
import os
TEAM_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "team_templates")
TEAMS = ["두산", "삼성", "한화", "롯데", "KIA", "키움", "SSG", "LG", "NC", "KT"]

# ─── 캘리브레이션 오버라이드 자동 로드 ───────────────────────────────
# calibrate_gui.py에서 S키로 저장하면 config_override.json에 기록됨
# 이후 import 시 자동으로 반영됨
import json as _json
_override_path = os.path.join(os.path.dirname(__file__), 'config_override.json')
if os.path.exists(_override_path):
    with open(_override_path, encoding='utf-8') as _f:
        _ovr = _json.load(_f)
    _mode_map = {
        'batter_p1': BATTER_P1, 'batter_p2': BATTER_P2, 'batter_p3': BATTER_P3,
        'pitcher_p1': PITCHER_P1, 'pitcher_p2': PITCHER_P2, 'pitcher_p3': PITCHER_P3,
    }
    for _mode, _rgs in _ovr.get('regions', {}).items():
        if _mode in _mode_map:
            _mode_map[_mode].update({k: tuple(v) for k, v in _rgs.items()})
    UI.update({k: tuple(v) for k, v in _ovr.get('click_pts', {}).items()})
