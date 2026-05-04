# 🚀 펀드매니저 시작 가이드

처음 받았으면 이 문서만 보고 따라하면 됩니다.

---

## ⚡ 빠른 실행 (3단계)

### ① 자동 설치 (한 번만)

**옵션 A. 처음 PC에서 — Python/Node 자동 설치 포함**
```
install_all.bat 더블클릭
```
→ Python 3.12 + Node.js LTS 까지 winget으로 자동 설치됩니다.

**옵션 B. Python/Node가 이미 깔려있는 PC**
```
setup.bat 더블클릭
```

설치가 끝나면 자동으로 `.env` 파일이 만들어집니다.

### ② API 키 입력

`.env` 파일을 메모장으로 열어서 3개 키를 채워넣으세요.

| 키 | 발급 사이트 | 비고 |
|---|---|---|
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey | 무료, Flash 1500회/일 |
| `NAVER_CLIENT_ID` / `NAVER_CLIENT_SECRET` | https://developers.naver.com/apps | "검색" API 체크 |
| `DART_API_KEY` | https://opendart.fss.or.kr/uss/umt/cnfirmKey.do | 무료, 즉시 발급 |

### ③ 실행

| 더블클릭 | 무엇이 실행되나 |
|---|---|
| `run_server.bat` | FastAPI 백엔드 (포트 8000) — **모바일 앱 쓰려면 필수** |
| `run_app.bat` | Expo 개발 서버 — QR 코드가 뜸 |
| `run_monitor.bat` | (선택) 15분마다 자동 분석 + DB 저장 |

갤럭시 S24에서 **Expo Go** 앱으로 QR 스캔하면 끝.

---

## 📁 폴더 구조

```
펀드매니저만들기/
├── 📜 START_HERE.md          ← 지금 보는 파일
├── 📜 README.md              ← 모바일 앱 상세 설명
│
├── ⚙ install_all.bat / .ps1  ← 다른 PC 이식용 (Python+Node 자동설치)
├── ⚙ setup.bat               ← 같은 PC 재설치용 (간단 버전)
├── ⚙ run_server.bat          ← FastAPI 서버 실행
├── ⚙ run_app.bat             ← Expo 개발 서버 실행
├── ⚙ run_monitor.bat         ← 모니터링 루프 실행
│
├── 📦 requirements.txt       ← Python 의존성 목록
├── 🔐 .env.example           ← API 키 템플릿
├── 🔐 .env                   ← 실제 키 (수동 입력 필요, git 제외)
│
├── 🐍 main.py                ← FastAPI 서버 (모바일 앱 백엔드)
├── 🐍 data_collector.py      ← 주가/뉴스/재무 데이터 수집
├── 🐍 analyzer.py            ← Gemini AI 분석 + 등급 산정
├── 🐍 monitor_loop.py        ← 15분 모니터링 루프
│
└── 📱 fund-manager-app/       ← React Native + Expo 모바일 앱
    ├── App.js
    ├── package.json
    └── src/
```

---

## 🆘 문제 해결

### "python을 인식할 수 없습니다"
→ Python 설치 시 **"Add python.exe to PATH"** 체크 안 했을 가능성 99%.
재설치하거나, 환경변수 PATH에 `C:\Users\<사용자>\AppData\Local\Programs\Python\Python312\` 추가.

### "Network Error" (모바일 앱)
1. PC와 핸드폰이 **같은 와이파이** 연결 확인 (핸드폰이 5G 모바일 데이터면 안 됨)
2. `fund-manager-app/src/services/api.js` 의 `BASE_URL` 이 PC IP와 일치하는지 확인
   → `install_all.bat` 사용 시 자동으로 IP 설정됨
3. Windows 방화벽이 uvicorn 차단했는지 확인 (첫 실행 시 팝업에서 "허용")
4. 회사/기숙사 와이파이는 기기 간 통신 차단함 → 핸드폰 핫스팟 사용

### "missing GEMINI_API_KEY" / "missing dart api key"
→ `.env` 파일이 없거나 키가 비어있음. `.env.example` 보고 채워넣기.

### 다른 PC로 옮기고 싶을 때
1. 이 폴더 전체를 USB나 클라우드로 복사
   - 단, `.venv/`, `node_modules/`, `__pycache__/`, `.env` 는 **제외** (용량/보안)
2. 새 PC에서 `install_all.bat` 더블클릭
3. `.env` 파일만 다시 채워넣기
4. 끝.

---

## 📊 동작 흐름

```
[갤럭시 S24]                    [PC]
   Expo Go          ─HTTP─▶   uvicorn (포트 8000)
                                    │
                                    ▼
                              main.py (FastAPI)
                              ├─▶ data_collector.py
                              │    ├─▶ yfinance (주가)
                              │    ├─▶ 네이버 뉴스 API
                              │    └─▶ DART (재무)
                              └─▶ analyzer.py
                                   └─▶ Gemini 1.5 Flash
                                        (뉴스 선별, 관련주 추론)
```

---

문제 생기면 README.md 의 "자주 막히는 부분" 섹션도 참고하세요.
