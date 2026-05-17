# 🌐 라즈베리파이 외부 접속 설정 가이드

> 집 안 Pi(`192.168.x.x:8000`)를 가족 핸드폰(외부 인터넷)에서도 접속 가능하게 만드는 방법.
> 두 가지 길 중에 골라요:
>
> - **방법 A — 포트포워딩 + DuckDNS + Caddy** (전통 방식, 무료, 약간 어려움)
> - **방법 B — Cloudflare Tunnel** (포트포워딩 불필요, 무료, 더 안전, **권장 ⭐**)

---

## 🚨 시작 전 — 이중 NAT 체크

한국 인터넷은 ISP 가 공유기 위에 또 NAT를 거는 경우가 많아요 (특히 KT 기가/SK 기가 인터넷).
이 경우 **방법 A 가 안 됩니다**. 미리 확인:

```bash
# Pi 에서 실행
curl -s ifconfig.me
# 결과: 예) 121.157.xx.xx
```

그 IP 를 본인 공유기 관리자 페이지 ("외부 IP" 또는 "WAN IP")의 값과 비교:
- **같으면** → 단일 NAT, 방법 A 가능
- **다르면** → 이중 NAT, **방법 B (Cloudflare Tunnel) 강제**

또는 ISP 콜센터에 "공인 IP / 포트개방 요청"하면 무료로 풀어주는 경우 많음.

---

# 방법 A: 포트포워딩 + DuckDNS + Caddy

## A-1. DuckDNS 무료 도메인 (3분)

1. https://www.duckdns.org → GitHub 계정으로 로그인
2. 원하는 서브도메인 입력 (예: `myfundpi`) → **Add domain**
3. 결과: **`myfundpi.duckdns.org`** 가 본인 것
4. 토큰(token) 메모 — 페이지 상단

### Pi 에서 IP 자동 갱신 등록
집 IP는 가끔 바뀌니까 Pi가 5분마다 DuckDNS에 "내 IP 이거야" 알리도록:

```bash
mkdir -p ~/duckdns
cat > ~/duckdns/duck.sh <<'EOF'
#!/bin/bash
DOMAIN="myfundpi"           # ← 본인 서브도메인
TOKEN="여기에-DuckDNS-토큰"  # ← 본인 토큰
echo url="https://www.duckdns.org/update?domains=${DOMAIN}&token=${TOKEN}&ip=" | curl -k -o ~/duckdns/duck.log -K -
EOF
chmod +x ~/duckdns/duck.sh
~/duckdns/duck.sh        # 한번 테스트
cat ~/duckdns/duck.log   # "OK" 나오면 성공

# crontab 등록 (5분마다)
( crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1" ) | crontab -
```

---

## A-2. 공유기 포트포워딩 (10분)

공유기 관리자 페이지 (보통 `192.168.0.1` 또는 `192.168.1.1`) 접속.

**필요한 포트포워딩:**

| 외부 포트 | 내부 IP | 내부 포트 | 프로토콜 | 용도 |
|-----------|---------|-----------|----------|------|
| 80 | Pi의 IP (예: 192.168.0.50) | 80 | TCP | HTTP (Caddy 인증서 발급용) |
| 443 | Pi의 IP | 443 | TCP | HTTPS |

> ❌ **8000번은 외부에 열지 마세요.** Caddy가 80/443 받아서 내부적으로 8000으로 전달합니다.
> ❌ **22번(SSH)도 외부에 열지 마세요.** 무차별 대입 공격 표적이 됨.

**Pi IP 고정 방법:**
공유기 관리자 → DHCP 설정 → Pi의 MAC 주소를 항상 같은 IP 할당 (예: 192.168.0.50)

브랜드별 메뉴 위치:
- **KT (GiGA WiFi home)**: 고급설정 → NAT → 포트포워딩
- **iptime**: 고급설정 → NAT/라우터 → 포트포워드
- **유플러스/SK**: 고급설정 → 포트포워딩

---

## A-3. Caddy 설치 + HTTPS 자동 인증 (5분)

Caddy는 Let's Encrypt 인증서를 자동으로 발급/갱신해주는 마법의 웹서버.

```bash
# Pi 에서 실행
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

### Caddyfile 작성

```bash
sudo nano /etc/caddy/Caddyfile
```

내용:
```
myfundpi.duckdns.org {
    # 모바일 앱 → Caddy → 내부 FastAPI
    reverse_proxy localhost:8000

    # 보안 헤더
    header {
        Strict-Transport-Security "max-age=31536000;"
        X-Content-Type-Options nosniff
        Referrer-Policy strict-origin-when-cross-origin
    }

    # 로그 (선택)
    log {
        output file /var/log/caddy/access.log
        format console
    }
}
```

```bash
sudo systemctl restart caddy
sudo systemctl status caddy
```

**확인:**
- 본인 핸드폰(LTE/5G로) 에서 `https://myfundpi.duckdns.org` 접속 → JSON 응답 + 자물쇠 🔒 보이면 성공
- 인증서 발급 1~2분 걸림. 안 되면: `sudo journalctl -u caddy -n 50`

---

## A-4. 8000 포트 외부 차단

Caddy 가 처리하니까 8000은 이제 외부 접근 막아야 함:

```bash
sudo ufw delete allow 8000/tcp
sudo ufw status
```

8000은 `localhost:8000` 으로만 접근 (Caddy 내부에서만 호출).

---

# 방법 B: Cloudflare Tunnel (권장 ⭐)

포트포워딩도, DuckDNS도, Caddy도 다 필요 없어요.
공유기 설정 한 글자도 안 건드리고 안전한 HTTPS 외부 접속 가능.

**장점:**
- 이중 NAT 든 뭐든 다 통과
- 공인 IP 불필요
- DDoS / 무차별 공격 Cloudflare 가 막아줌
- 무료 (개인 사용)
- HTTPS 자동

**단점:**
- Cloudflare 계정 필요
- 도메인이 있으면 그대로 쓰고, 없으면 도메인 하나 사야 함 (`.com` 1년 ~1.5만원, 또는 `.xyz` 1.5천원)

---

## B-1. Cloudflare 가입 + 도메인 등록

1. https://dash.cloudflare.com → 가입
2. 도메인이 있으면 추가, 없으면 https://dash.cloudflare.com/?to=/:account/domains 에서 구매
3. 도메인이 Cloudflare에서 관리되도록 네임서버 변경 (도메인 산 곳에서 NS 레코드 바꿈)

> 💡 도메인 없이 무료로 쓰고 싶으면: **Cloudflare Tunnel + TryCloudflare** 사용 가능
> (단, 매 재시작마다 URL 바뀜 — 운영용으론 비추)

---

## B-2. cloudflared 설치

```bash
# Pi 에서 실행 (ARM64)
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
sudo dpkg -i cloudflared.deb
cloudflared --version
```

---

## B-3. 터널 생성

```bash
# Cloudflare 로그인 (브라우저 자동 열림)
cloudflared tunnel login

# 터널 생성
cloudflared tunnel create manymanager

# 결과로 나온 터널 ID 메모 (예: 7d2f8a3b-xxxx-xxxx-xxxx-xxxxxxxxxxxx)
```

### 설정 파일 작성
```bash
mkdir -p ~/.cloudflared
nano ~/.cloudflared/config.yml
```

```yaml
tunnel: 7d2f8a3b-xxxx-xxxx-xxxx-xxxxxxxxxxxx
credentials-file: /home/pi/.cloudflared/7d2f8a3b-xxxx-xxxx-xxxx-xxxxxxxxxxxx.json

ingress:
  - hostname: fund.yourdomain.com    # ← 본인 도메인의 서브도메인
    service: http://localhost:8000
  - service: http_status:404
```

### DNS 라우팅
```bash
cloudflared tunnel route dns manymanager fund.yourdomain.com
```

### systemd로 자동 실행
```bash
sudo cloudflared --config ~/.cloudflared/config.yml service install
sudo systemctl enable cloudflared
sudo systemctl start cloudflared
sudo systemctl status cloudflared
```

**확인:** 핸드폰 LTE 로 `https://fund.yourdomain.com` 접속 → 동작!

---

# 🔒 공통: 보안 강화 권장 사항

## 1. SSH 키 인증 (비번 끄기)

```bash
# 본인 PC 에서 SSH 키 생성 (이미 있으면 건너뜀)
ssh-keygen -t ed25519

# Pi 에 등록
ssh-copy-id pi@fundpi.local

# Pi 에서 비번 로그인 비활성화
sudo nano /etc/ssh/sshd_config
# PasswordAuthentication no
# PermitRootLogin no
sudo systemctl restart sshd
```

## 2. fail2ban (무차별 대입 방어)

```bash
sudo apt install -y fail2ban
sudo systemctl enable --now fail2ban
```

## 3. 자동 보안 업데이트

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

## 4. .env 권한 확인

```bash
ls -la /home/pi/ManyManager/.env
# -rw------- 1 pi pi  (600 권한, pi만 읽기 가능)
chmod 600 /home/pi/ManyManager/.env
```

## 5. FastAPI 자체 인증 추가 (선택)

`main.py` 에 API 키 미들웨어 추가 — 가족만 아는 토큰 헤더로 보호:

```python
from fastapi import HTTPException, Header

API_TOKEN = os.getenv("APP_TOKEN", "")

async def verify_token(x_app_token: str = Header(...)):
    if x_app_token != API_TOKEN:
        raise HTTPException(status_code=401, detail="unauthorized")
```

모바일 앱이 매 요청마다 `X-App-Token` 헤더 전송하면 끝.

---

# 🆘 트러블슈팅

### "DuckDNS는 갱신되는데 외부에서 안 됨"
1. **이중 NAT 의심** — 위쪽 "시작 전" 섹션 다시 확인
2. 방화벽: `sudo ufw status` → 80, 443 allow 되어 있나?
3. 공유기 포트포워딩이 Pi IP 정확한지? `hostname -I` 결과와 비교

### "Caddy가 인증서 발급 실패"
- 80 포트가 ISP에 의해 차단된 경우 (KT/SK 흔함). 콜센터에 "80, 443 포트 개방 요청"
- 또는 **방법 B (Cloudflare Tunnel)** 로 전환

### "Cloudflare Tunnel은 동작하는데 느림"
- 무료 플랜도 충분히 빠른데, Pi의 위치(한국) ↔ 가장 가까운 Cloudflare PoP 거리에 영향받음
- 보통 50~150ms 추가됨

### "Pi가 정전 후 안 켜져요"
- SD 카드가 정전으로 깨졌을 수 있음. 다음번엔 **UPS 또는 보조배터리** 권장
- 정기적으로 SD 카드 백업: `sudo dd if=/dev/mmcblk0 of=/path/to/backup.img bs=4M`

---

# ✅ 최종 체크리스트

배포 끝나면 이거 다 됐는지 확인:

- [ ] `sudo systemctl status manymanager` → active (running)
- [ ] `curl http://localhost:8000/` → 200
- [ ] `curl https://본인도메인` (PC에서) → 200 + 자물쇠
- [ ] 핸드폰 LTE 로 도메인 접속 → 정상
- [ ] 모바일 앱 `BASE_URL` 새 도메인으로 변경 → 동작
- [ ] Pi 재부팅 후에도 자동 시작 (`sudo reboot` 테스트)
- [ ] 1주일 안정성 확인 후 Oracle 인스턴스 종료

행운을 빌어요 🥧🚀
