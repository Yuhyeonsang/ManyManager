# 🖱️ GitHub 설정 — 클릭 바이 클릭 가이드

> Pi로 옮긴 뒤 GitHub 저장소에서 손봐야 할 모든 클릭을 순서대로 정리.
> 메뉴 위치까지 그대로 안내합니다.
>
> **저장소 URL:** https://github.com/Yuhyeonsang/ManyManager

---

## 📅 진행 타이밍

```
[지금]                  [Pi 셋업]              [1주일 후]
 │                        │                      │
 ▼                        ▼                      ▼
1. 코드 push          2. Runner 등록         3. Oracle 정리
   (이미 됨)             (Pi 셋업 완료 후)      (Pi 안정화 확인 후)
```

**중요**: Pi 가 1주일 안정적으로 돌아가는 걸 본 뒤에 Oracle Secrets 지우세요.
그래야 문제 생겼을 때 Oracle 워크플로 수동 실행으로 빠르게 롤백 가능.

---

# 단계 1️⃣ — Pi 셋업 후: Self-hosted Runner 등록 (15분)

> Pi에서 `./setup_pi3.sh` 가 끝난 시점에 진행.

## 1-1. GitHub Runner 토큰 발급

**브라우저에서 다음 순서로 클릭:**

1. 본인 저장소 페이지로 이동
   ```
   https://github.com/Yuhyeonsang/ManyManager
   ```

2. 상단 탭 중 **"Settings"** 클릭
   - 화면 위쪽 가로 메뉴: `Code | Issues | Pull requests | Actions | Projects | Security | Insights | Settings`
   - 본인이 owner라야 보임. 안 보이면 권한 확인.

3. 좌측 사이드바에서 **"Actions"** 펼치기 → **"Runners"** 클릭
   - 좌측 메뉴 구조:
     ```
     Settings
       ├─ General
       ├─ Access
       ├─ Code and automation
       │   ├─ Branches
       │   ├─ Tags
       │   ├─ Rules
       │   ├─ Actions ⬅ 펼치기
       │   │   ├─ General
       │   │   ├─ Runners ⬅ 클릭
       │   │   └─ Secrets and variables
       │   └─ ...
       └─ ...
     ```

4. 우측 상단의 초록색 **"New self-hosted runner"** 버튼 클릭

5. 다음 페이지에서 선택:
   - **Runner image** 라디오 버튼: `Linux` 선택
   - **Architecture** 드롭다운:
     - Pi 3B+ (64-bit Pi OS Lite) → `ARM64`
     - Pi 3B+ (32-bit Pi OS) → `ARM`
     - Pi 4/5 (64-bit, 권장) → `ARM64`

6. 페이지 중간에 **"Download"** 와 **"Configure"** 두 박스 나옴.
   각 박스의 명령어 4~5줄을 **한 번에 복사**:

   ```bash
   # Download 박스 (예시 — 실제 URL/버전은 GitHub 화면 따라):
   mkdir actions-runner && cd actions-runner
   curl -o actions-runner-linux-arm64-2.319.1.tar.gz -L https://github.com/actions/runner/releases/download/v2.319.1/actions-runner-linux-arm64-2.319.1.tar.gz
   tar xzf ./actions-runner-linux-arm64-2.319.1.tar.gz

   # Configure 박스:
   ./config.sh --url https://github.com/Yuhyeonsang/ManyManager --token AXXXXXXXX...
   ```

   > 💡 **토큰은 1시간 후 만료**되니까 바로 Pi에 SSH 들어가서 붙여넣으세요.

## 1-2. Pi 에서 Runner 설치

**SSH로 Pi 접속해서 위에서 복사한 명령어 실행:**

```bash
# 본인 PC 터미널에서
ssh pi@fundpi.local      # 또는 ssh pi@<Pi의_IP>
```

**Pi에 들어와서:**

```bash
cd ~
# GitHub에서 복사한 Download 박스 명령어들 순서대로
mkdir -p actions-runner && cd actions-runner

curl -o actions-runner-linux-arm64-2.319.1.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.319.1/actions-runner-linux-arm64-2.319.1.tar.gz

tar xzf ./actions-runner-linux-arm64-2.319.1.tar.gz

# 의존성 설치
sudo ./bin/installdependencies.sh
```

**Configure 단계 — 토큰 붙여넣기:**

```bash
./config.sh \
  --url https://github.com/Yuhyeonsang/ManyManager \
  --token <GitHub 화면에서 복사한 토큰> \
  --name "fundpi" \
  --labels "fundpi" \
  --work "_work" \
  --unattended
```

> ⚠️ `--name "fundpi"` 와 `--labels "fundpi"` 는 그대로 두세요.
> `deploy-pi.yml` 의 `runs-on: [self-hosted, fundpi]` 와 매칭됨.

**서비스로 등록 (부팅 시 자동 시작):**

```bash
sudo ./svc.sh install pi
sudo ./svc.sh start
sudo ./svc.sh status
# active (running) 보이면 성공
```

## 1-3. sudo 권한 부여 (systemctl 재시작용)

**Pi 에서:**

```bash
sudo tee /etc/sudoers.d/runner-systemctl > /dev/null <<'EOF'
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart manymanager
pi ALL=(ALL) NOPASSWD: /bin/systemctl status manymanager
pi ALL=(ALL) NOPASSWD: /bin/systemctl is-active manymanager
pi ALL=(ALL) NOPASSWD: /bin/journalctl -u manymanager *
EOF
sudo chmod 440 /etc/sudoers.d/runner-systemctl
```

## 1-4. 등록 확인

브라우저에서 GitHub Runners 페이지 새로고침:
```
Settings → Actions → Runners
```

리스트에 다음이 보여야 함:
```
🟢 fundpi    Self-hosted Linux ARM64    fundpi    Idle
```

---

# 단계 2️⃣ — Pi 셋업 후: Secrets 확인 (5분)

## 2-1. 기존 Secrets 보기

1. 좌측 메뉴 **Settings → Secrets and variables → Actions** 클릭
2. 페이지 중간 **"Repository secrets"** 섹션에 현재 등록된 Secrets 리스트 보임:
   ```
   DART_API_KEY              Updated XX days ago
   GEMINI_API_KEY            Updated XX days ago
   GROQ_API_KEY              Updated XX days ago
   NAVER_CLIENT_ID           Updated XX days ago
   NAVER_CLIENT_SECRET       Updated XX days ago
   ORACLE_HOST               Updated XX days ago
   ORACLE_SSH_KEY            Updated XX days ago
   ORACLE_USER               Updated XX days ago
   ```

## 2-2. 없는 Secret 추가 (있으면 건너뜀)

API 키 5개 중 빠진 게 있으면:

1. 우측 상단 초록색 **"New repository secret"** 클릭
2. 입력:
   - **Name**: 예) `GEMINI_API_KEY`
   - **Secret**: 본인 API 키 값 그대로 붙여넣기
3. 초록 **"Add secret"** 클릭

> 💡 한 번 등록한 Secret 의 값은 **다시 볼 수 없음** (보안). 변경하려면 새 값으로 덮어쓰기만 가능.

---

# 단계 3️⃣ — Pi 셋업 후: Actions 권한 확인 (3분)

1. 좌측 메뉴 **Settings → Actions → General** 클릭

2. 페이지 내려가면서 다음 확인:

### "Actions permissions" 섹션
- ✅ **"Allow all actions and reusable workflows"** 라디오 선택
- (또는 보안 강하게: "Allow Yuhyeonsang, and select non-Yuhyeonsang, actions and reusable workflows")

### "Fork pull request workflows from outside collaborators" 섹션
저장소가 **Private** 이면 신경 안 써도 됨. **Public** 이면:
- ⚠️ **"Require approval for all outside collaborators"** 라디오 선택 ← 강력 권장
- (악성 PR이 self-hosted runner에서 임의 코드 실행하는 걸 방지)

### "Workflow permissions" 섹션
- ✅ **"Read and write permissions"** 라디오 선택
- ✅ **"Allow GitHub Actions to create and approve pull requests"** 체크 (선택)

### 저장
- 페이지 하단 **"Save"** 버튼 클릭

---

# 단계 4️⃣ — Pi 첫 배포 테스트 (5분)

## 4-1. 본인 PC 에서 더미 push

```bash
cd 펀드매니저만들기
echo "# Pi 배포 테스트 $(date)" >> README.md
git add README.md
git commit -m "test: trigger pi deployment"
git push
```

## 4-2. 브라우저에서 결과 확인

1. 저장소 페이지 상단 **"Actions"** 탭 클릭
2. 좌측 워크플로 리스트에서 **"Deploy to Raspberry Pi"** 클릭
3. 최상단 워크플로 run 클릭 (방금 push한 거)
4. 다음을 순서대로 확인:
   ```
   ⏱ Set up job                       (수 초)
   ✅ Checkout
   ✅ 코드를 운영 디렉토리에 동기화
   ✅ .env 생성
   ✅ 의존성 설치
   ✅ systemd 서비스 재시작
   ✅ 헬스체크
   ✅ 상태 요약            ← 여기서 Pi 온도/메모리 출력
   ```
5. 전체 ✅ 초록불이면 배포 성공

## 4-3. 실패 시 디버깅

각 step 클릭하면 로그 펼쳐짐. 자주 보는 에러:

| 에러 메시지 | 원인 | 해결 |
|------------|------|------|
| `No runner matching the specified labels` | Pi runner 가 offline | Pi 에서 `sudo ./svc.sh status` |
| `Permission denied (systemctl)` | sudoers 미설정 | 단계 1-3 다시 |
| `ModuleNotFoundError` | pip 설치 실패 | piwheels 확인 또는 venv 재생성 |
| `HTTP 000` | 서비스 시작 안 됨 | `sudo journalctl -u manymanager -n 50` |

---

# 단계 5️⃣ — 1주일 후: Oracle 잔재 정리

## 5-1. 1주일 운영 안정성 확인 체크리스트

다음 모두 ✅ 라면 정리 가능:
- [ ] Pi가 1주일 내내 다운 없이 동작
- [ ] 모바일 앱 → 가족이 정상 사용
- [ ] `git push` 시 자동 배포 5번 이상 성공
- [ ] CPU 온도 60°C 이하 유지
- [ ] OOM 없이 시스템 안정

## 5-2. Oracle Secrets 삭제

**Settings → Secrets and variables → Actions** 페이지에서:

1. `ORACLE_SSH_KEY` 오른쪽 **휴지통 🗑️** 아이콘 클릭 → **"I understand, delete this secret"** 확인 ← **이게 가장 중요**
2. `ORACLE_HOST` 삭제
3. `ORACLE_USER` 삭제
4. `ORACLE_PORT` 삭제 (있으면)

## 5-3. Oracle 워크플로 삭제 (선택)

Oracle 완전히 안 쓸 거면 `.github/workflows/deploy.yml` 통째로 삭제:

```bash
# 본인 PC 터미널에서
cd 펀드매니저만들기
git rm .github/workflows/deploy.yml
git commit -m "remove: legacy oracle deploy workflow"
git push
```

또는 GitHub 웹에서 파일 페이지 → 우측 휴지통 아이콘.

## 5-4. Oracle VM 종료

1. https://cloud.oracle.com 로그인
2. **Compute → Instances** → 본인 인스턴스
3. **"More actions"** → **"Terminate"** → 디스크 함께 삭제 체크
4. 확인 → 5분 후 완전 삭제

---

# 단계 6️⃣ — 모바일 앱 BASE_URL 새 도메인 등록

> 코드는 이미 빈 문자열로 수정해놨어요 (api.js:14).
> 앱 첫 실행 시 사용자가 직접 입력하도록.

## 6-1. 가족 핸드폰에서

1. 펀드매니저 앱 실행
2. **"설정"** 화면 진입
3. **"서버 URL"** 입력란에:
   ```
   https://myfundpi.duckdns.org
   ```
   또는 (Cloudflare Tunnel 쓰면):
   ```
   https://fund.yourdomain.com
   ```
4. **"연결 테스트"** 버튼 → 응답시간 ms 보이면 성공
5. **"저장"** 버튼

## 6-2. .apk 다시 빌드해서 배포

원래 .apk에 박혀있던 Oracle IP가 빠졌으니, 새 .apk 빌드 + 가족 핸드폰에 재설치:

```bash
cd fund-manager-app
eas build --platform android --profile preview
# 또는 build_apk.bat 실행
```

빌드 완료된 .apk URL 을 가족에게 카톡으로 보내면 끝.

---

# 🆘 자주 묻는 질문

### Q. Pi가 정전으로 꺼졌어. 다시 켜면 자동으로 다 복구되나?
**A.** systemd + actions runner svc 다 부팅 시 자동 시작됩니다. 단:
- DDNS 갱신은 cron 등록되어 있어야 IP 바뀐 거 GitHub/사용자에게 알림
- SD 카드가 정전으로 손상됐을 수 있으니 부팅 후 `dmesg | grep -i error` 확인

### Q. Pi runner와 GitHub Actions의 "ubuntu-latest" 동시에 쓸 수 있어?
**A.** 네. deploy-pi.yml 은 `runs-on: [self-hosted, fundpi]` 라 Pi만, 다른 워크플로(build-apk.yml 등)는 그대로 `ubuntu-latest` 사용.

### Q. 자동 배포가 너무 자주 실행돼서 Pi 부담돼.
**A.** deploy-pi.yml 의 `on: push: paths:` 에서 트리거할 파일 패턴을 좁히세요. 또는 main 브랜치 말고 `release` 브랜치 push 시에만 트리거하게.

### Q. 모바일 앱에서 한국 외 (해외 여행 중) 접속이 안 돼.
**A.** ISP 동적 IP가 바뀌었는데 DDNS 갱신이 안 됐을 가능성. Pi에서 `~/duckdns/duck.sh` 수동 실행 또는 cron 확인.

---

이제 모든 GitHub 설정이 정리됐어요. 다음은 실제 Pi 셋업이에요. 🥧
