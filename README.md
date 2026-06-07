# pixelplay 🎮

다양한 게임의 화면 기반 자동화 매크로 프레임워크.  
OpenCV + EasyOCR을 이용한 비전 기반 게임 자동화 도구 모음입니다.

## 구조

```
pixelplay/
├── core/
│   ├── mouse_server.py    # 원격 마우스/키보드 제어 서버 (게임 PC용)
│   └── capture.py         # 화면 캡처 & OCR 기반 클래스
├── games/
│   └── cpbv/              # 컴프야 V26
│       ├── README.md      # ← 상세 설치/실행 가이드
│       ├── config_cpbv.py
│       ├── calibrate_gui.py
│       └── capture_player_data.py
├── tools/
│   └── get_window_rect.ps1  # 게임 창 좌표 확인 (게임 PC용)
└── README.md
```

## 지원 게임

| 게임 | 가이드 | 기능 |
|------|--------|------|
| 컴프야 V26 (CPBV) | [games/cpbv/README.md](games/cpbv/README.md) | 선수 도감 자동 수집, 좌표 보정 GUI |

## 빠른 시작

전체 설치 및 실행 방법은 각 게임 폴더의 README를 참조하세요.

- **[컴프야 V26 설치 가이드 →](games/cpbv/README.md)**

## 라이선스

MIT
