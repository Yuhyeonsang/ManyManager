# FundManager 모바일 앱 (Expo + React Native)

4단계까지 만든 FastAPI 서버와 통신하는 갤럭시 S24용 앱입니다.

## 폴더 구조

```
fund-manager-app/
├── App.js                      # 진입점 (네비게이션)
├── app.json                    # Expo 설정
├── package.json                # 의존성
├── babel.config.js
├── src/
│   ├── components/
│   │   ├── GradeBadge.js       # 매수/관망/매도 뱃지
│   │   └── StockCard.js        # 종목 카드
│   ├── screens/
│   │   ├── DashboardScreen.js  # ① 메인 대시보드 (핫 종목 리스트)
│   │   └── DetailScreen.js     # ② 상세 분석 + ④ 클립보드 복사 버튼
│   ├── services/
│   │   ├── api.js              # FastAPI 통신
│   │   └── database.js         # ③ SQLite 오프라인 캐시
│   └── utils/
│       └── clipboard.js        # 클립보드 + 프롬프트 포맷팅
└── README.md
```

## 0. 사전 준비 (PC)

1. **Node.js 설치** — https://nodejs.org (LTS 권장)
2. **갤럭시 S24 에 Expo Go 앱 설치** — Play 스토어에서 "Expo Go" 검색
3. **PC와 갤럭시 S24를 같은 와이파이에 연결**

## 1. 패키지 설치

PC에서 cmd/PowerShell을 열고:

```bash
cd C:\Users\dbgus\Desktop\ai\펀드매니저만들기\fund-manager-app
npm install
```

## 2. PC IP 주소 확인 후 코드에 입력

핸드폰에서 `localhost`는 핸드폰 자기 자신을 가리키므로, PC IP를 적어줘야 합니다.

```bash
ipconfig
```

출력에서 **IPv4 주소** 를 찾기 (예: `192.168.0.10`).

`src/services/api.js` 파일을 열어서 한 줄만 바꿉니다:

```js
export const BASE_URL = 'http://192.168.0.10:8000';
//                              ↑ 여기를 본인 PC IP로
```

## 3. FastAPI 서버 실행 (외부 접속 허용)

4단계에서 만든 파이썬 서버를 **0.0.0.0** 으로 실행해야 핸드폰이 접속 가능합니다.

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

방화벽 팝업이 뜨면 "허용".

## 4. Expo 개발 서버 실행

`fund-manager-app` 폴더에서:

```bash
npm start
```

터미널에 QR 코드가 뜹니다.

## 5. 갤럭시 S24에서 실행

1. 갤럭시 S24에서 **Expo Go** 앱 실행
2. "Scan QR code" 로 PC 화면의 QR 스캔
3. 잠시 기다리면 앱이 자동으로 다운로드되어 실행됨

## 기능 매핑

| 요구사항 | 구현 위치 |
|---|---|
| ① 핫 종목 리스트 + 매수/관망 카드 | `DashboardScreen.js` + `StockCard.js` + `GradeBadge.js` |
| ② 상세 페이지 (뉴스 요약 + 재무 수치) | `DetailScreen.js` |
| ③ SQLite 오프라인 캐시 | `services/database.js` (자동 호출) |
| ④ 클립보드 복사 버튼 | `DetailScreen.js` 하단 고정 버튼 → `utils/clipboard.js` |

오프라인일 때 SQLite 캐시가 자동으로 사용되며, 화면 상단에 노란 배너로 알려줍니다.

## FastAPI 서버에 필요한 엔드포인트 (참고)

`src/services/api.js` 가 호출하는 3개의 엔드포인트입니다. 4단계에서 만든 서버에 이 시그니처가 있는지 확인하세요.

### `GET /api/hot-stocks`
```json
[
  {
    "ticker": "005930",
    "name": "삼성전자",
    "price": 78500,
    "change_pct": 2.34,
    "grade": "BUY",
    "score": 87,
    "summary": "한 줄 요약"
  }
]
```

### `GET /api/stocks/{ticker}/report`
```json
{
  "ticker": "005930",
  "name": "삼성전자",
  "grade": "BUY",
  "score": 87,
  "news_summary": "AI가 정리한 뉴스 요약…",
  "financials": {
    "per": 12.3, "pbr": 1.4, "roe": 11.2,
    "revenue_growth": 8.7, "operating_margin": 15.3, "debt_ratio": 28.5
  },
  "updated_at": "2026-05-03T09:30:00"
}
```

### `GET /api/stocks/{ticker}/clipboard` (선택)
```json
{ "text": "Claude 웹에 그대로 붙여넣을 텍스트…" }
```
이 엔드포인트가 없어도 앱이 자동으로 클라이언트 측에서 폴백 포맷을 만듭니다.

## 자주 막히는 부분

- **"Network Error" 가 뜨는 경우**: 99% 확률로 ① BASE_URL 의 IP 가 안 맞거나 ② 서버가 `--host 0.0.0.0` 없이 실행됐거나 ③ 윈도우 방화벽이 막은 경우
- **PC와 핸드폰이 다른 와이파이에 연결되어 있으면 절대 안 됩니다** (예: 핸드폰이 5G 모바일 데이터)
- **회사/기숙사 와이파이는 기기 간 통신을 차단**하기도 합니다 → 핫스팟으로 대체

---

## 📡 Expo Go 로 핸드폰에 배포하기 (3가지 방법)

`Expo Go` 는 갤럭시 S24의 **Play 스토어에서 무료로 받을 수 있는 컨테이너 앱**입니다. 우리 앱(fund-manager-app)을 별도의 .apk 빌드 없이 이 컨테이너 위에서 그대로 돌릴 수 있어요. 상황별로 3가지 길이 있습니다.

### 방법 ① 같은 Wi-Fi (가장 빠름, 5초 시작)

집에서 PC와 핸드폰이 같은 와이파이에 있을 때.

```bash
cd C:\Users\dbgus\Desktop\ai\펀드매니저만들기\fund-manager-app
npx expo start
```

→ 터미널의 QR 을 갤럭시 S24의 **Expo Go > Scan QR code** 로 찍으면 끝.

### 방법 ② 터널 모드 (밖에서도 됨, PC만 켜져 있으면 OK)

학교/카페/모바일 데이터 등 PC와 핸드폰이 다른 네트워크에 있을 때.

```bash
npx expo start --tunnel
```

처음 실행할 때 한 번 `@expo/ngrok` 패키지 설치 동의가 뜹니다 → `Y`. 같은 Wi-Fi 가 아니어도 인터넷만 되면 동작합니다.

### 방법 ③ EAS Update — Expo 클라우드에 영구 게시 (PC 꺼도 됨)

**진짜 "배포"** 입니다. JS 번들을 Expo 서버에 올려두고, 핸드폰의 Expo Go 앱이 영구 링크로 다운받아 실행해요. PC 안 켜져 있어도 됩니다.

#### 0) 한 번만 — Expo 계정 + EAS CLI

```bash
npm install -g eas-cli
eas login          # 처음이면 expo.dev 에서 무료 계정 만들고 로그인
```

#### 1) 프로젝트 EAS 초기화

`fund-manager-app` 폴더에서:

```bash
eas init           # Expo 계정 아래 프로젝트 생성, app.json 에 projectId 자동 추가
eas update:configure
```

#### 2) 배포 (게시)

```bash
eas update --branch production --message "v1 첫 배포"
```

배포가 끝나면 콘솔에 **Expo Go용 QR 코드 + URL** (`exp.host/@본인계정/fund-manager-app/...`) 이 출력됩니다.

#### 3) 갤럭시 S24 에서 받기

- **Expo Go** 앱 실행 → 우측 상단 메뉴 → **Enter URL manually** → 위 URL 입력
- 또는 그 URL 의 QR 을 카메라/Expo Go 로 스캔
- 첫 실행 후엔 Expo Go 메인 화면의 **Recently opened** 탭에 자동으로 남아서, 다음부터는 한 번에 켤 수 있습니다.

> 무료 티어로 월 EAS Update 1,000회까지 가능 — 개인 사용엔 충분.

---

## 🌍 FastAPI 서버도 어디서나 접근 가능하게 하기

위 방법 ② / ③ 은 **앱**을 어디서나 받을 수 있게 해주지만, 앱이 호출할 **FastAPI 서버**도 인터넷에서 접근 가능해야 합니다 (그렇지 않으면 집 밖에선 "Network Error"). 두 가지 길:

### A) 임시 공개 — `ngrok` (5분, 무료)

```bash
# 1) https://ngrok.com 에서 무료 가입 → authtoken 발급
# 2) 설치
choco install ngrok      # 또는 https://ngrok.com/download
ngrok config add-authtoken <발급받은_토큰>

# 3) FastAPI 가 8000번 포트로 떠 있는 상태에서:
ngrok http 8000
```

콘솔에 `https://xxxx-xx-xx.ngrok-free.app` 같은 공개 URL 이 출력됨.
이 URL 을 `src/services/api.js` 의 `BASE_URL` 에 넣고 다시 `eas update` 하면 끝.

```js
export const BASE_URL = 'https://xxxx-xx-xx.ngrok-free.app';
```

### B) 영구 배포 — Render / Railway / Fly.io (무료 티어)

PC 꺼도 24시간 작동하길 원하면 클라우드에 올리기. 가장 쉬운 곳: **[Render.com](https://render.com)**
1. GitHub 저장소에 FastAPI 코드 push
2. Render → New Web Service → 저장소 선택
3. `Build Command`: `pip install -r requirements.txt`
4. `Start Command`: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. 배포되면 받는 `https://yourname.onrender.com` 을 `BASE_URL` 에 입력

---

## 🛣️ 추천 흐름 (초보자용)

| 단계 | 무엇을 | 왜 |
|---|---|---|
| 1 | 방법 ① (같은 Wi-Fi) 로 일단 켜본다 | 코드/서버가 정상인지 빠르게 확인 |
| 2 | 방법 ② (`--tunnel`) 로 밖에서 테스트 | 외부망에서 동작하는지 |
| 3 | API 서버를 ngrok 으로 공개 | 핸드폰이 집 밖에서도 데이터 받게 |
| 4 | EAS Update 로 게시 | PC 꺼도 핸드폰에서 영구 사용 |
