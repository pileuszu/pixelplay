# pixelplay 🎮

다양한 게임의 화면 기반 자동화 매크로 프레임워크.  
OpenCV + EasyOCR을 이용한 비전 기반 게임 자동화 도구 모음입니다.

## 구조

```
pixelplay/
├── core/                  # 공통 유틸리티
│   ├── mouse_server.py    # 원격 마우스/키보드 제어 서버 (게임 PC용)
│   └── capture.py        # 화면 캡처 & OCR 기반 클래스
├── games/
│   └── cpbv/             # 컴프야 V26 (Com2uS Pro Baseball V)
│       ├── capture_player_data.py  # 선수 카드 데이터 자동 추출
│       ├── close_mutex.py          # 멀티클라이언트용 뮤텍스 해제
│       └── test_multiclient.py    # 다중 게임 창 관리
└── README.md
```

## 사용 방법

### 원격 2PC 구성 (게임 PC + 개발 PC)

1. **Tailscale 설치** (양쪽 PC, 같은 계정으로 로그인)  
   → https://tailscale.com/download

2. **게임 PC** — 마우스 서버 실행:
   ```bash
   pip install pyautogui
   python core/mouse_server.py
   ```

3. **개발 PC** — 캡처 + OCR 실행:
   ```bash
   pip install -r requirements.txt
   python games/cpbv/capture_player_data.py \
     --stream http://GAME_PC_IP:8080/video \
     --mouse-host GAME_PC_TAILSCALE_IP \
     --type bat
   ```

## 필요 라이브러리

```bash
pip install -r requirements.txt
```

## 지원 게임

| 게임 | 기능 |
|---|---|
| 컴프야 V26 (CPBV) | 선수 카드 데이터 추출, 멀티클라이언트 |

## 라이선스

MIT
