# 🤖 GitHub Actions → 라즈베리파이 자동 배포 가이드

> "코드 수정 → `git push` → 1분 뒤 가족 핸드폰에 반영" 을 Pi에서도 그대로 구현하는 방법.

---

## 🥇 4가지 방식 비교

| 방식 | 보안 | 난이도 | 공유기 설정 | 비고 |
|------|------|--------|------------|------|
| **A. Self-hosted Runner** ⭐ | 🟢 최고 | 🟡 중 | 🟢 불필요 | **이 가이드 권장** |
| B. SSH 직결 (Oracle 방식) | 🔴 약함 | 🟢 쉬움 | 🔴 22번 노출 | 비추 |
| C. Cloudflare Tunnel + SSH | 🟡 보통 | 🔴 어려움 | 🟢 불필요 | 복잡함 |
| D. Cron 폴링 (GitHub Actions X) | 🟢 좋음 | 🟢 매우 쉬움 | 🟢 불필요 | GitHub Actions 안 씀, 즉시성↓ |

### 왜 Self-hosted Runner 가 답인가?

**핵심: Pi 가 GitHub에 outbound 연결**만 함.
- 외부에서 Pi로 들어오는 포트 = 0
- 공유기 NAT / 이중 NAT / 동적 IP / 방화벽 다 무관
- GitHub Secrets는 GitHub에 그대로, Pi는 받기만 함
- 보안 사고 표면적이 매우 작음

```
┌──────────┐                    ┌──────────┐
│  GitHub  │ ← long-polling ←─ │   Pi     │
│  job 큐  │   (HTTPS 443)       │  runner  │
└──────────┘                    └──────────┘
   inbound 0개                   inbound 0개
```

---

# A. Self-hosted Runner 셋업 (15분)

## A-1. GitHub에서 Runner 토큰 발급

1. https://github.com/Yuhyeonsang/ManyManager → **Settings**
2. 좌측 → **Actions** → **Runners**
3. **New self-hosted runner** 클릭
4. OS: **Linux**, Architecture: **ARM64** (Pi 4/5) 또는 **ARM** (Pi 3B+)

   > 💡 Pi 3B+: 64-bit Pi OS Lite 깔았으면 **ARM64**, 32-bit이면 **ARM**

5. 페이지에 나오는 명령어 4줄 (`curl ... config.sh ... run.sh`) — **메모해두기**

## A-2. Pi에 Runner 설치

Pi에 SSH 접속 후:

```bash
# 1. 작업 폴더
mkdir -p ~/actions-runner && cd ~/actions-runner

# 2. GitHub에서 복사한 curl + tar 명령어 실행
# 예시 (실제 토큰/버전은 GitHub 화면에서):
curl -o actions-runner-linux-arm64-2.319.1.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.319.1/actions-runner-linux-arm64-2.319.1.tar.gz
tar xzf ./actions-runner-linux-arm64-2.319.1.tar.gz

# 3. 의존성 (Ubuntu/Debian)
sudo ./bin/installdependencies.sh

# 4. Runner 등록 (대화형)
./config.sh \
  --url https://github.com/Yuhyeonsang/ManyManager \
  --token <GitHub_화면의_토큰> \
  --name "fundpi" \
  --labels "fundpi" \
  --work "_work" \
  --unattended

# 5. 서비스로 등록 (부팅 시 자동 시작)
sudo ./svc.sh install pi
sudo ./svc.sh start

# 6. 상태 확인
sudo ./svc.sh status
```

이 시점에서 GitHub Runners 페이지에 **"fundpi 🟢 Idle"** 로 보여야 합니다.

## A-3. sudo 권한 (systemd 재시작용)

Runner가 `sudo systemctl restart manymanager` 를 비밀번호 없이 실행할 수 있게:

```bash
sudo tee /etc/sudoers.d/runner-systemctl > /dev/null <<'EOF'
pi ALL=(ALL) NOPASSWD: /bin/systemctl restart manymanager
pi ALL=(ALL) NOPASSWD: /bin/systemctl status manymanager
pi ALL=(ALL) NOPASSWD: /bin/systemctl is-active manymanager
pi ALL=(ALL) NOPASSWD: /bin/journalctl -u manymanager *
EOF
sudo chmod 440 /etc/sudoers.d/runner-systemctl
```

## A-4. GitHub Secrets 등록

https://github.com/Yuhyeonsang/ManyManager/settings/secrets/actions

다음 5개 추가:
- `GEMINI_API_KEY`
- `GROQ_API_KEY` (있으면)
- `NAVER_CLIENT_ID`
- `NAVER_CLIENT_SECRET`
- `DART_API_KEY`

> 💡 Oracle 시절에 이미 등록되어 있다면 그대로 사용 가능. ORACLE_HOST/USER/SSH_KEY 는 이제 안 씀.

## A-5. 첫 배포 테스트

```bash
# 본인 PC에서
echo "# 첫 Pi 배포 테스트 $(date)" >> README.md
git add README.md && git commit -m "test pi deploy"
git push
```

GitHub Actions 페이지에서 **"Deploy to Raspberry Pi"** 워크플로가 자동 실행 →
1~3분 안에 ✔ 초록불 + Pi에서 `sudo systemctl status manymanager` → active.

---

# B. SSH 직결 (Oracle 방식) — 비추 ⚠️

장점은 익숙한 방식이라는 것뿐.
단점:
- 집 공유기의 22번 포트를 **외부에 노출**해야 함 (전 세계에서 무차별 대입 공격 받음)
- 동적 IP면 GitHub Secrets의 호스트 주소를 매번 업데이트
- fail2ban / SSH 키 인증 등 추가 보안 작업 필요

정말 이 방식 쓸 거면, 22번 대신 22122 같은 비표준 포트 + 키 인증 only + IP 화이트리스트 (GitHub Actions IP 대역) 권장.

---

# C. Cloudflare Tunnel + SSH

Cloudflare가 SSH 트래픽 터널링 → 공유기 포트포워딩 없이 외부 SSH 가능.
설정 복잡도가 self-hosted runner 보다 높은데, runner 보다 이점도 없어요. **non-runner**.

---

# D. Cron 폴링 (가장 단순, GitHub Actions 안 씀)

GitHub Actions 설정 다 귀찮으면 이거 하나로 끝:

```bash
# Pi 에서
crontab -e
```

다음 줄 추가:
```cron
# 5분마다 GitHub에 변경사항 있는지 확인하고 있으면 재시작
*/5 * * * * cd /home/pi/ManyManager && /usr/bin/git fetch origin main 2>&1 | grep -q "main" && /usr/bin/git reset --hard origin/main && /home/pi/ManyManager/.venv/bin/pip install --quiet -r requirements.txt && /usr/bin/sudo /bin/systemctl restart manymanager
```

**장점:** GitHub Actions, runner, secrets 다 필요 없음.
**단점:** 최대 5분 지연. 배포 결과 알림 없음. `.env` 는 수동으로 Pi에 미리 넣어야 함.

> 💡 가족용 소규모 서비스라면 D 도 충분합니다. 즉시 반영이 필요한 경우만 A.

---

# 🆘 트러블슈팅

### "Runner가 Offline으로 떠요"
```bash
cd ~/actions-runner
sudo ./svc.sh status
sudo ./svc.sh start
# 안 되면:
sudo journalctl -u actions.runner.* -n 50
```

### "Permission denied (systemctl restart)"
- A-3 sudoers 설정 다시 확인
- `sudo cat /etc/sudoers.d/runner-systemctl` 로 권한 정확한지 검증

### "pip install 이 매번 오래 걸려요"
- Pi 3B+ 인데 piwheels 안 쓰면 컴파일 → 워크플로의 `--extra-index-url https://www.piwheels.org/simple/` 확인
- 또는 `requirements.txt` 변경 없으면 step 건너뛰기:
  ```yaml
  - name: Check if requirements changed
    id: req_check
    run: |
      if git diff HEAD~1 HEAD --name-only | grep -q requirements.txt; then
        echo "changed=true" >> $GITHUB_OUTPUT
      else
        echo "changed=false" >> $GITHUB_OUTPUT
      fi
  - name: pip install
    if: steps.req_check.outputs.changed == 'true'
    run: ...
  ```

### "헬스체크가 항상 실패해요"
- Pi 3B+ 는 첫 부팅 후 uvicorn 시작에 30~60초 걸림. `sleep 10` → `sleep 30` 으로
- `.env` 의 API 키 유효한지: `cat .env` 후 마스킹된 값 확인

### "Runner를 삭제하고 싶어요"
```bash
cd ~/actions-runner
sudo ./svc.sh stop
sudo ./svc.sh uninstall
./config.sh remove --token <GitHub_화면_새_토큰>
```

---

# 🔒 보안 권고

Self-hosted runner는 GitHub에서 코드를 받아 실행합니다.
**Public 저장소** 의 PR을 자동 실행하면 악성 코드 실행 위험.

본인 저장소가 **Private** 이면 OK. 만약 Public이면:
- Settings → Actions → General → "Fork pull request workflows" → **Require approval for all outside collaborators** 체크

---

# 📋 최종 체크리스트

A 방식 (권장) 셋업 완료 기준:

- [ ] Pi 에 setup_pi3.sh (또는 setup_pi.sh) 완료
- [ ] `~/actions-runner/` 폴더 생성 + Runner 데몬 active
- [ ] GitHub Runners 페이지에 "fundpi 🟢 Idle"
- [ ] `/etc/sudoers.d/runner-systemctl` 권한 설정
- [ ] GitHub Secrets 5개 등록 완료
- [ ] 테스트 push → workflow 자동 실행 → ✔
- [ ] Pi에서 `curl http://localhost:8000/` → 200
- [ ] 모바일 앱 BASE_URL을 새 도메인으로 변경

이제 코드 수정 → `git push` 한 줄이면 끝. 🚀
