# 🚀 Oracle Cloud + GitHub Actions 자동 배포 가이드

목표:
```
Cowork에서 코드 수정 → git push → 1분 후 가족 핸드폰에 반영
```

소요시간: 첫 세팅 약 1시간. 이후로는 코드 변경 시 `git push` 한 번이면 끝.

---

## 📋 전체 플로우

```
[너의 PC (Cowork)]
       │ git push
       ▼
[GitHub 저장소] ─trigger─▶ [GitHub Actions]
                                │ SSH 접속
                                ▼
                         [Oracle Cloud VM]
                                │
                                ├─ git pull
                                ├─ pip install (변경 시만)
                                ├─ systemctl restart fund-manager
                                │
                                ▼
                         [Caddy] ──HTTPS──▶ [가족 핸드폰]
                                    Let's Encrypt 자동
```

---

## 1️⃣ Oracle Cloud Free Tier 가입 + VM 생성 (15분)

### 1-1. 계정 생성
1. https://www.oracle.com/cloud/free/ → "Start for free"
2. 가입 시 신용카드 인증 필요 (과금 없음, 본인 확인용)
3. **Home region** 은 한 번 정하면 못 바꾸니까 신중히 — `Seoul (ICN)` 또는 `Tokyo (NRT)` 추천 (한국에서 빠름)

### 1-2. ARM Ampere 인스턴스 생성 (Always Free)

**중요**: x86 (E2.1.Micro) 가 아니라 **ARM Ampere A1 Flex** 를 골라야 4 vCPU + 24GB RAM 다 받음.

1. 좌측 메뉴 → **Compute > Instances** → **Create Instance**
2. 이미지: **Canonical Ubuntu 24.04** (Always Free 표시 있는 거)
3. Shape: **VM.Standard.A1.Flex** 선택 → OCPU 4개, RAM 24GB 로 슬라이더 올리기
4. Networking: 기본 VCN 자동 생성 (체크박스 그대로)
5. SSH keys: **Generate a key pair for me** → **Save Private Key** 다운로드 (이 .key 파일은 잃어버리면 끝장이니 잘 보관)
6. **Create** 클릭

> 만약 "Out of host capacity" 가 뜨면 ARM 자원 부족이라 그래. Tokyo 리전으로 옮겨보거나 30분 후 재시도.

### 1-3. Public IP 확인 + 포트 열기

생성된 VM 의 **Public IP Address** 를 메모 (예: `132.226.45.67`).

VCN > Security Lists > Default Security List 에서 **Ingress Rules**:
- 기존 SSH 22번은 그대로
- **추가**: TCP 80, Source `0.0.0.0/0`
- **추가**: TCP 443, Source `0.0.0.0/0`

---

## 2️⃣ DuckDNS 무료 도메인 발급 (3분)

도메인을 사지 않아도 무료 HTTPS 가능하게 해주는 트릭.

1. https://www.duckdns.org → GitHub 계정으로 로그인
2. 원하는 서브도메인 입력 (예: `myfund`) → Add
3. 결과: **`myfund.duckdns.org`** 가 본인 것
4. 그 줄의 IP 칸에 위 1-3에서 받은 Public IP 붙여넣고 **update**
5. (선택) 자동 IP 갱신: 페이지 하단의 install 가이드 참고. 오라클 IP 는 거의 안 바뀌므로 옵션.

---

## 3️⃣ GitHub 저장소 만들기 (5분)

### 3-1. 저장소 생성
1. https://github.com/new
2. Repository name: `fund-manager` (자유)
3. **Private** 체크 (.env 안 올리지만 보수적으로)
4. Create

### 3-2. 로컬에서 푸시

PC (Windows) 의 cmd / PowerShell:
```powershell
cd C:\Users\dbgus\Desktop\ai\펀드매니저만들기
git init
git branch -M main
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/<본인계정>/fund-manager.git
git push -u origin main
```

> 처음 git 쓰면: https://git-scm.com/download/win 설치 → `git config --global user.name "이름"` / `user.email "이메일"`

> ⚠ `.gitignore` 에 `.env` 가 등록되어 있어 절대 안 올라감. 안 올라간 게 정상.

---

## 4️⃣ Oracle VM 에 1회 설치 (15분)

### 4-1. SSH 접속

PowerShell 에서 (1-2에서 받은 .key 파일을 `C:\Users\dbgus\.ssh\oracle.key` 로 옮겼다고 가정):
```powershell
ssh -i C:\Users\dbgus\.ssh\oracle.key ubuntu@132.226.45.67
```

### 4-2. 코드 클론

```bash
sudo mkdir -p /opt/fund-manager
sudo chown $USER:$USER /opt/fund-manager
git clone https://github.com/<본인계정>/fund-manager.git /opt/fund-manager
cd /opt/fund-manager
```

> Private 저장소면 GitHub Personal Access Token 으로 clone:
> `git clone https://<USER>:<TOKEN>@github.com/<USER>/fund-manager.git /opt/fund-manager`
> 토큰: GitHub > Settings > Developer settings > Personal access tokens > "repo" 권한만 체크

### 4-3. .env 파일 생성

```bash
nano /opt/fund-manager/.env
```
아래 내용 붙여넣고 본인 키로 채우기:
```env
GEMINI_API_KEY=...
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
DART_API_KEY=...
FUND_DB=/opt/fund-manager/fund_manager.db
```
저장: `Ctrl+O`, `Enter`, `Ctrl+X`

### 4-4. 자동 설치 스크립트 실행

```bash
cd /opt/fund-manager
bash deploy/setup_oracle.sh myfund.duckdns.org
```

스크립트가 알아서:
- Python 3.12 + Caddy 설치
- venv + pip install
- systemd 서비스 등록 + 시작
- Caddy 가 Let's Encrypt 인증서 자동 발급
- 방화벽 규칙

5~10분 후 끝나면:
```bash
curl -I https://myfund.duckdns.org/
# HTTP/2 200 이 보이면 성공!
```

---

## 5️⃣ GitHub Actions 자동 배포 연결 (10분)

### 5-1. 서버에서 SSH 키 만들기

오라클 VM 안에서:
```bash
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# 개인키 출력 → 복사해두기
cat ~/.ssh/github_deploy
```

이 출력 (`-----BEGIN OPENSSH PRIVATE KEY-----` ~ `-----END OPENSSH PRIVATE KEY-----`) 전체 복사.

### 5-2. GitHub 에 비밀 등록

GitHub 저장소 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

| Name | Value |
|---|---|
| `ORACLE_HOST` | `132.226.45.67` (또는 `myfund.duckdns.org`) |
| `ORACLE_USER` | `ubuntu` |
| `ORACLE_SSH_KEY` | 5-1에서 복사한 개인키 전체 |

### 5-3. 첫 배포 트리거

PC 에서 아무 파일 수정 후:
```powershell
git add .
git commit -m "Test deploy"
git push
```

GitHub 저장소 → **Actions** 탭에서 워크플로우 진행 상황 확인. 1~2분 후 ✔ 초록 체크.

---

## 6️⃣ 모바일 앱 BASE_URL 갱신 (1회)

이제 서버가 영구 HTTPS URL 로 살아있음. 모바일 앱이 이 주소를 가리키게:

### 옵션 A — 빌드에 박기 (재빌드 필요)
`fund-manager-app/src/services/api.js`:
```js
export const BASE_URL = 'https://myfund.duckdns.org';
```
그 후 `build_apk.bat` 으로 .apk 다시 빌드.

### 옵션 B — 앱 안에서 한 번만 (재빌드 X) ✨ 추천
1. 갤럭시 S24 의 FundManager 앱 켜기
2. 우측 상단 ⚙ 탭
3. URL 입력란에 `https://myfund.duckdns.org` 입력
4. **🔌 연결 테스트** → ✅ 확인
5. **💾 저장**

→ 가족이 여러 명이면 각자 핸드폰에서 한 번씩만.

---

## ✅ 완성! 이후의 워크플로우

```
[Cowork 에서 코드 수정]
       ↓
git add . && git commit -m "..." && git push
       ↓
GitHub Actions 가 변경 종류를 자동 감지:
   ├─ 서버 코드(*.py)        → SSH로 Oracle 에 pull + systemctl restart   (1~2분)
   ├─ 앱 JS 코드(src/, App.js) → EAS Update OTA 발행                       (10~30초)
   └─ 앱 네이티브(app.json 등) → 수동 .apk 재빌드 필요 (Actions 탭에서 클릭)
       ↓
가족 핸드폰: 앱을 다시 켤 때 자동으로 새 코드 적용
```

새벽 3시에 코드 고쳐도, 출장 중이어도, 가족이 자고 있을 때도 자동.

---

## 🔁 3개 워크플로우 매핑표

| 너가 바꾼 것 | 어떤 워크플로우가 도나? | 가족 폰에 반영되는 데 걸리는 시간 |
|---|---|---|
| `main.py`, `analyzer.py`, `data_collector.py` | **deploy.yml** (Oracle 자동 배포) | 1~2분 |
| `fund-manager-app/src/**`, `App.js` | **eas-update.yml** (OTA 자동 발행) | 30초 ~ 1분 (앱 재실행 시 적용) |
| `fund-manager-app/app.json`, `package.json` | **build-apk.yml** (자동, 새 .apk 생성) | 10~15분, 핸드폰 재설치 필요 |
| `fund-manager-app/eas.json` | **build-apk.yml** | 동일 |
| `requirements.txt` | **deploy.yml** | 1~2분 |
| `DEPLOYMENT.md`, `README.md` 등 문서 | (워크플로우 안 돔) | — |

---

## 🛠 EAS Update — 1회 셋업

OTA 업데이트가 작동하려면 .apk 가 `expo-updates` 를 포함하고 있어야 함. **`build_apk.bat` 이 자동으로** `expo-updates` 설치 + `eas update:configure` 까지 다 해주니까, 첫 .apk 빌드만 정상적으로 받으면 그 이후로는 평생 자동.

### 한 번만 GitHub Secrets 에 등록

`EXPO_TOKEN` 이 GitHub Actions 에 필요해:

1. https://expo.dev/settings/access-tokens → **Create token** → 복사
2. GitHub 저장소 → **Settings** → **Secrets and variables** → **Actions**
3. **New repository secret**
   - Name: `EXPO_TOKEN`
   - Value: 위에서 복사한 토큰
4. **Add secret**

이게 끝. 다음에 `git push` 할 때부터 자동 OTA.

### 첫 .apk 빌드는 1번만

```
build_apk.bat 더블클릭
```
첫 실행에서 `eas login` 1회 + `eas build` 1회. 결과 .apk 를 갤럭시 S24 에 설치.

이 .apk 는 OTA 지원이 박혀있어서, 이후로는 절대 다시 빌드/재설치 안 해도 됨 — `git push` 만 하면 자동 갱신.

### 코드 수정 시 (평소 워크플로우)

```bash
# Cowork 에서 코드 수정
git add .
git commit -m "디자인 개선"
git push
```

→ 30초 후 GitHub Actions 가 OTA 발행
→ 앱 다시 켜면 새 코드 자동 적용

---

## 📲 앱이 OTA 업데이트 받는 방식

설치된 .apk 안의 `expo-updates` 가 **앱 시작 시마다 Expo 서버에 "새 버전 있어?"** 라고 물어봄.

- **있으면**: 백그라운드에서 다운로드 → 다음 실행 시 적용 (또는 즉시 재시작)
- **없으면**: 그냥 캐시된 코드로 실행

가족이 앱을 종료했다 다시 켜면 새 코드 자동 적용. 따로 뭘 누를 필요 없음.

---

## 🆘 자주 막히는 곳

| 증상 | 원인 / 해결 |
|---|---|
| `ssh: connection refused` | Oracle Security List 에서 22번 포트가 닫혀있음 → Ingress 추가 |
| `curl https://...` 가 ssl error | DuckDNS 의 IP 와 VM Public IP 가 다를 가능성. duckdns.org 다시 확인 |
| systemctl status 에서 `Active: failed` | `tail /var/log/fund-manager.log` 로 에러 확인. 99% 는 .env 빈 값 |
| GitHub Actions 가 timeout | SSH key 의 줄바꿈이 깨짐 → Secret 다시 등록 (앞뒤 공백 X) |
| Caddy 가 인증서 발급 실패 | VCN Security List 에 80, 443 둘 다 열려있는지 확인. 80 닫혀있으면 Let's Encrypt 챌린지 실패 |
| 502 Bad Gateway | uvicorn 이 안 떠있음. `sudo systemctl restart fund-manager` |

---

## 🛠 자주 쓰는 명령어 (서버에서)

```bash
# 서비스 상태
sudo systemctl status fund-manager
sudo systemctl restart fund-manager

# 실시간 로그
tail -f /var/log/fund-manager.log

# Caddy
sudo systemctl reload caddy
tail -f /var/log/caddy/access.log

# 수동 배포 (GitHub Actions 안 쓰고 빠르게)
bash /opt/fund-manager/deploy/deploy.sh

# 디스크 사용량 확인 (Oracle Free 50GB)
df -h /
```

---

## 📁 이 프로젝트의 배포 관련 파일

```
펀드매니저만들기/
├── .github/workflows/deploy.yml   ← GitHub Actions 워크플로우
├── deploy/
│   ├── fund-manager.service       ← systemd 유닛 파일
│   ├── Caddyfile.example          ← Caddy 리버스 프록시 설정
│   ├── setup_oracle.sh            ← 1회 설치 스크립트
│   └── deploy.sh                  ← 수동 배포 스크립트 (백업)
├── render.yaml                    ← (대안) Render.com 사용 시
├── runtime.txt                    ← Python 3.12 명시
├── .gitignore                     ← .env 등 제외
└── DEPLOYMENT.md                  ← 이 문서
```
