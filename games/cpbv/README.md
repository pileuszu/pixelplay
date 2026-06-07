# 컴프야 V26 (CPBV) — 선수 도감 자동 수집 가이드

> **2PC 구성**: 게임 PC (Windows, 컴프야 실행) + 개발 PC (스크립트 실행)  
> 두 PC는 **Tailscale**로 연결됩니다.

---

## 목차

1. [전체 구조](#전체-구조)
2. [공통 환경 설정](#공통-환경-설정)
3. [게임 PC 설정](#게임-pc-설정)
4. [개발 PC 설정](#개발-pc-설정)
5. [좌표 보정 (최초 1회)](#좌표-보정-최초-1회)
6. [데이터 수집 실행](#데이터-수집-실행)
7. [설정 파일 설명](#설정-파일-설명)

---

## 전체 구조

```
games/cpbv/
├── config_cpbv.py          # 창 좌표, 스트림 설정, UI 영역 비율
├── config_override.json    # 캘리브레이션 저장값 (자동 생성)
├── calibrate_gui.py        # 좌표 보정 GUI (개발 PC에서 실행)
├── calibrate.py            # 텍스트 기반 보정 도구 (구버전)
├── capture_player_data.py  # 선수 데이터 자동 수집 메인 스크립트
├── team_templates/         # 팀 로고 템플릿 이미지 (캘리브레이션 시 생성)
└── calibration_output/     # 캘리브레이션 캡처 이미지 (자동 생성)
```

```
core/
└── mouse_server.py         # 게임 PC에서 실행하는 원격 마우스 서버
tools/
└── get_window_rect.ps1     # 게임 창 좌표 확인 PowerShell 스크립트
```

---

## 공통 환경 설정

### Tailscale 설치 (양쪽 PC 모두)

1. https://tailscale.com/download 에서 설치
2. 동일 계정으로 로그인
3. 각 PC의 Tailscale IP 확인:
   ```
   게임 PC IP: 100.118.216.59  (예시)
   개발 PC IP: 100.115.5.57    (예시)
   ```

---

## 게임 PC 설정

> 게임 PC에서 실행. 한 번 설정 후 컴프야 실행 전마다 켜두면 됩니다.

### 1. 저장소 클론 (최초 1회)

```powershell
git clone https://github.com/pileuszu/pixelplay.git
cd pixelplay
```

### 2. Python 의존성 설치 (최초 1회)

```powershell
pip install pyautogui
```

### 3. 마우스 서버 실행

```powershell
python core/mouse_server.py
```

출력:
```
[*] 마우스 서버 시작: 0.0.0.0:9999
[*] 개발 PC에서 이 PC의 Tailscale IP로 접속하세요
```

> **계속 켜둬야 합니다.** 개발 PC가 마우스/키보드를 원격 제어합니다.

### 4. OBS 스트림 설정

1. OBS 실행 → 장면에 **컴프야 V26 창** 소스 추가
2. 소스 우클릭 → **변환** → **화면에 맞추기 (중앙)**
3. OBS 메뉴 → **도구** → **가상 카메라** → **시작**
4. ffmpeg로 MJPEG 스트림 시작:

```powershell
ffmpeg -f dshow -i video="OBS Virtual Camera" -q:v 5 -f mjpeg -listen 1 http://0.0.0.0:8080/video
```

> ffmpeg가 없으면: https://ffmpeg.org/download.html

### 5. 게임 창 좌표 확인 (최초 1회)

컴프야를 **창 모드**로 실행한 상태에서:

```powershell
# 개발 PC에 push된 후 pull
git pull

powershell -ExecutionPolicy Bypass -File tools\get_window_rect.ps1
```

출력 예시:
```
=== 컴프야V26 창 좌표 ===
Left   = 912
Top    = 140
Right  = 1648
Bottom = 1459
```

→ 이 값을 개발 PC의 `config_cpbv.py`에 입력합니다.

---

## 개발 PC 설정

> 스크립트를 실행하는 PC. Windows/macOS/Linux 모두 가능.

### 1. 저장소 클론 (최초 1회)

```bash
git clone https://github.com/pileuszu/pixelplay.git
cd pixelplay
```

### 2. 가상환경 생성 및 활성화 (최초 1회)

```bash
python -m venv venv

# Windows (Git Bash)
source venv/Scripts/activate

# Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# macOS/Linux
source venv/bin/activate
```

### 3. 의존성 설치 (최초 1회)

```bash
pip install torch==2.3.1 torchvision==0.18.1 --index-url https://download.pytorch.org/whl/cpu
pip install easyocr opencv-python pillow
```

> **주의**: torch는 CPU 버전으로 설치합니다 (GPU 불필요).

### 4. config_cpbv.py 업데이트

게임 PC에서 확인한 창 좌표를 `games/cpbv/config_cpbv.py`에 입력:

```python
WINDOW_LEFT   = 912   # ← 게임 PC 값으로 변경
WINDOW_TOP    = 140
WINDOW_RIGHT  = 1648
WINDOW_BOTTOM = 1459

STREAM_URL  = "http://100.118.216.59:8080/video"  # ← 게임 PC Tailscale IP
MOUSE_HOST  = "100.118.216.59"                     # ← 게임 PC Tailscale IP
```

---

## 좌표 보정 (최초 1회)

게임 PC에서 마우스 서버와 ffmpeg 스트림이 실행 중인 상태에서:

```bash
source venv/Scripts/activate
python games/cpbv/calibrate_gui.py
```

### GUI 조작법

| 키 / 동작 | 기능 |
|-----------|------|
| **마우스 드래그** | 영역 박스 이동 |
| **모서리 핸들 드래그** | 영역 박스 리사이즈 |
| **우클릭 on 점(●)** | 해당 버튼 클릭 테스트 (게임 PC 클릭) |
| **좌클릭 on 점(●)** | 클릭 포인트 선택 후 드래그로 이동 |
| **T** | 선택된 클릭 포인트 테스트 |
| **N / TAB** | 다음 모드 (타자P1→P2→P3→투수P1→P2→P3) |
| **P** | 이전 모드 |
| **R** | 프레임 새로고침 |
| **L** | 라이브 모드 토글 (자동 갱신) |
| **A** | OCR 자동 영역 감지 (EasyOCR) |
| **S** | 저장 (`config_override.json`) |
| **C** | OCR 오버레이 지우기 |
| **Q / ESC** | 종료 |

### 보정 순서

1. 게임에서 **타자 카드 P1** 열기
2. GUI에서 **R** → 화면 캡처 확인
3. **A** → OCR 자동 감지 (초기값 자동 설정)
4. 박스가 정확하지 않으면 **드래그**로 조정
5. **다음 선수 버튼** (next_player 점) **우클릭** → 실제로 클릭되는지 확인
6. **S** → 저장
7. N키로 모드 전환 후 같은 과정 반복

### 팀 로고 템플릿 저장

각 팀 카드를 열어두고 `calibrate.py`에서:

```bash
python games/cpbv/calibrate.py
# → [t] 팀 로고 템플릿 저장 선택
# → 팀 이름 입력 (두산, 삼성, 한화, 롯데, KIA, 키움, SSG, LG, NC, KT)
```

---

## 데이터 수집 실행

### 타자 수집

```bash
python games/cpbv/capture_player_data.py \
  --type batter \
  --card-type 골든에이스
```

### 투수 수집

```bash
python games/cpbv/capture_player_data.py \
  --type pitcher \
  --card-type 골든에이스
```

### 실행 전 체크리스트

- [ ] 게임 PC: `mouse_server.py` 실행 중
- [ ] 게임 PC: ffmpeg MJPEG 스트림 실행 중 (`http://IP:8080/video`)
- [ ] 게임에서 **조건 검색** 완료 → 첫 번째 선수 카드 열어둠
- [ ] 개발 PC: 가상환경 활성화 (`source venv/Scripts/activate`)
- [ ] 좌표 보정 완료 (`config_override.json` 존재)

---

## 설정 파일 설명

### `config_cpbv.py`

직접 편집하는 설정 파일:

```python
# 게임 PC 창 절대 좌표 (get_window_rect.ps1로 확인)
WINDOW_LEFT, WINDOW_TOP, WINDOW_RIGHT, WINDOW_BOTTOM

# 스트림 URL과 마우스 서버 IP (게임 PC Tailscale IP)
STREAM_URL = "http://IP:8080/video"
MOUSE_HOST = "IP"

# OBS 출력 해상도
STREAM_WIDTH, STREAM_HEIGHT = 1280, 720
```

### `config_override.json`

`calibrate_gui.py`에서 **S** 저장 시 자동 생성. 직접 편집 가능:

```json
{
  "regions": {
    "batter_p1": {
      "name_area": [0.15, 0.455, 0.65, 0.055],
      ...
    }
  },
  "click_pts": {
    "next_player": [0.94, 0.25],
    ...
  }
}
```

`config_cpbv.py`를 import할 때 자동으로 반영됩니다.

---

## 트러블슈팅

| 증상 | 원인 | 해결 |
|------|------|------|
| 스트림 연결 실패 | ffmpeg 미실행 또는 IP 오류 | 게임 PC에서 ffmpeg 실행 확인 |
| 마우스 클릭 안 됨 | mouse_server.py 미실행 | 게임 PC에서 서버 실행 |
| 화면이 검게 나옴 | 창 좌표 오류 | `get_window_rect.ps1` 재실행 후 config 업데이트 |
| ??? 텍스트 표시 | PIL 폰트 없음 | `C:/Windows/Fonts/malgun.ttf` 존재 확인 |
| OCR 안 됨 | easyocr 미설치 | `pip install easyocr` |
| torch 버전 충돌 | GPU 버전 설치됨 | CPU 버전으로 재설치 (위 명령어 참조) |
