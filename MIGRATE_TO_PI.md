# 🥧 펀드매니저 서버 → 라즈베리파이 이전 가이드

> Oracle Cloud Ubuntu(ARM Ampere A1)에 돌던 ManyManager FastAPI 서버를
> 집에 있는 **Raspberry Pi 4/5** 로 옮기는 전체 절차.
>
> 좋은 소식: 기존 코드는 100% 그대로 돌아갑니다 (ARM 아키텍처 동일, Debian 계열).
> 손볼 곳은 사용자 이름(`ubuntu` → `pi`), 메모리 한도, 그리고 외부 접속(공유기 포트포워딩) 정도예요.

---

## 📋 전체 플로우

```
[기존: Oracle Cloud VM]
         │ DB + .env 백업
         ▼
[너의 PC] ──── SCP/USB ────▶ [Raspberry Pi]
                                    │
                                    ├─ setup_pi.sh 실행
                                    ├─ .env 복원, DB 복원
                                    ├─ systemd 서비스 등록
                                    ▼
                              [집 공유기]
                                    │ 포트포워딩
                                    ▼
                              [DuckDNS + Caddy HTTPS]
                                    │
                                    ▼
                              [가족 핸드폰 앱]
```

소요 시간: **약 1.5시간** (Pi OS 설치 30분 + 셋업 30분 + 외부 접속 30분)

---

## 🛒 준비물 체크리스트

| 항목 | 추천 사양 | 비고 |
|------|----------|------|
| Raspberry Pi | **Pi 4 (4GB↑)** 또는 **Pi 5 (4GB↑)** | yfinance + pandas + Gemini 호출이라 RAM 4GB 권장. Zero 2 W는 비추 (1GB 부족) |
| microSD | 32GB↑, **A2 class** (SanDisk Extreme 등) | Class10/A1 쓰면 DB 쓰기가 느려 짜증남 |
| 전원 어댑터 | Pi 4: 5V/3A USB-C, Pi 5: 5V/5A 공식 어댑터 | 저전력 어댑터 쓰면 USB 죽음 |
| 케이스 + 방열판 | Pi 5는 **쿨링팬 필수** (스로틀링 심함) | 24/7 가동이라 발열 무시 못 함 |
| 이더넷 케이블 | 가능하면 유선 | WiFi 써도 되지만 끊기면 골치 |

---

## 1️⃣ Raspberry Pi OS 설치 (15분)

### 1-1. Imager 다운로드
1. https://www.raspberrypi.com/software/ 에서 **Raspberry Pi Imager** 받기
2. microSD를 PC에 꽂고 Imager 실행

### 1-2. OS 굽기
1. **Device**: 본인 Pi 모델 선택
2. **OS**: `Raspberry Pi OS Lite (64-bit)` ← **반드시 64-bit Lite**
   - GUI 안 쓸 거니까 Lite로 (RAM 아낌)
   - 32-bit는 메모리 4GB 제한 걸려 비추
3. **Storage**: microSD 선택
4. **NEXT** 누른 뒤 **"Edit Settings"** 클릭 (이게 중요)

### 1-3. 사전 설정 (Imager 내장)
**General 탭:**
- ✅ Hostname: `fundpi` (원하는 이름)
- ✅ Username: `pi`, Password: 강한 비밀번호로
- ✅ Wireless LAN: 집 WiFi SSID/비번 입력 (유선이면 건너뛰기)
- ✅ Locale: `Asia/Seoul`, 키보드 `us`

**Services 탭:**
- ✅ **Enable SSH** → "Use password authentication"

저장하고 **Write** 클릭. 5~10분 대기.

### 1-4. 첫 부팅 + SSH 접속
1. microSD를 Pi에 꽂고 전원 연결
2. 2~3분 뒤 PC에서:
   ```bash
   ssh pi@fundpi.local
   ```
   - 안 되면 공유기 관리 페이지에서 Pi의 IP 확인 후 `ssh pi@192.168.x.x`
3. 첫 로그인 후 시스템 업데이트:
   ```bash
   sudo apt update && sudo apt full-upgrade -y
   sudo reboot
   ```

---

## 2️⃣ 기존 서버에서 데이터 백업 (10분)

Oracle VM에 SSH 접속한 상태에서:

```bash
cd /home/ubuntu/ManyManager

# DB + .env 묶어서 백업
tar czf ~/manymanager_backup.tar.gz fund_manager.db .env

# 본인 PC로 다운로드 (PC 터미널에서 실행)
scp -i your_oracle_key.key ubuntu@<ORACLE_IP>:~/manymanager_backup.tar.gz ./
```

> 💡 GitHub에 코드는 이미 올라가 있으니 코드 자체는 안 옮겨도 됨. Pi에서 `git clone` 하면 끝.
> .env(API 키)와 DB만 안전하게 옮기면 됨.

---

## 3️⃣ Pi에 셋업 스크립트 실행 (20분)

### 3-1. setup_pi.sh 다운로드 및 실행
Pi에 SSH 접속한 상태에서:

```bash
# 본인 GitHub 저장소에서 클론 (URL은 setup_server.sh 와 동일)
cd ~
git clone https://github.com/Yuhyeonsang/ManyManager.git
cd ManyManager

# Pi 전용 셋업 스크립트 실행
chmod +x setup_pi.sh
./setup_pi.sh
```

이 스크립트가 자동으로 해주는 것:
1. 시스템 패키지 + Python 설치
2. **2GB 스왑 활성화** (Pi 4 4GB도 yfinance + pandas 동시 작업 시 빠듯)
3. Python 가상환경 + requirements.txt 설치
4. `.env` 템플릿 생성
5. `manymanager.service` systemd 등록 (부팅 시 자동 실행)
6. ufw 방화벽: 22 (SSH), 8000 (FastAPI) 개방
7. 서비스 시작 + 헬스체크

### 3-2. .env / DB 복원
백업 파일을 Pi로 옮기고:

```bash
# 본인 PC 터미널에서
scp manymanager_backup.tar.gz pi@fundpi.local:~/

# 다시 Pi SSH 에서
cd ~/ManyManager
tar xzf ~/manymanager_backup.tar.gz
sudo systemctl restart manymanager
```

### 3-3. 상태 확인
```bash
sudo systemctl status manymanager
curl http://localhost:8000/api/diagnostics
```

200 응답 + 정상 JSON 나오면 성공.

---

## 4️⃣ 외부 접속 설정 (30분)

집 인터넷은 동적 IP라 Oracle Cloud처럼 고정 IP가 없어요. 그래서 추가 작업이 필요해요.

자세한 절차는 **`EXTERNAL_ACCESS_PI.md`** 참고. 요약하면:

1. **공유기 포트포워딩**: 외부 80/443 → 내부 Pi:8000
2. **DuckDNS**: 무료 도메인 + Pi가 5분마다 IP 갱신 신호 전송
3. **Caddy**: Let's Encrypt 자동 HTTPS

---

## 5️⃣ 옮긴 뒤 정리 (5분)

### 5-1. 앱 BASE_URL 변경
모바일 앱에서 서버 주소를 새 도메인으로:
```
이전: http://<ORACLE_IP>:8000
변경: https://yourname.duckdns.org
```

### 5-2. GitHub Actions 자동 배포 (선택)
`DEPLOYMENT.md` 에 있던 Oracle용 GitHub Actions를 Pi용으로 바꾸려면:
- Secrets의 `ORACLE_HOST` → 집 공인 IP 또는 DuckDNS 주소
- Secrets의 `ORACLE_SSH_KEY` → Pi의 SSH 키
- 단, 집 공유기에서 **SSH 22번도 포트포워딩** 해야 함 (보안 약화 — 비추)
- **권장**: GitHub Actions 안 쓰고 Pi에서 5분마다 `git pull` 하는 cron 으로 대체

```bash
# crontab -e
*/5 * * * * cd ~/ManyManager && git pull && sudo systemctl restart manymanager
```

### 5-3. Oracle VM 종료
모든 게 잘 돌면 Oracle 인스턴스 **Terminate** (요금 부과 방지).

---

## 🛠️ 트러블슈팅

### "메모리 부족으로 서비스가 죽어요"
- `free -h` 로 스왑 확인. 1GB 부족하면 setup_pi.sh가 만든 swap을 2GB로 키우세요:
  ```bash
  sudo swapoff /swapfile
  sudo fallocate -l 2G /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  ```
- `manymanager.service` 의 `MemoryMax=800M` 을 `1500M` 로 조정

### "Pi가 발열로 느려져요"
- `vcgencmd measure_temp` 로 온도 확인
- 80°C 넘으면 쓰로틀링 — 쿨링팬 또는 더 큰 방열판 장착
- 케이스 안에서 도는 거면 **케이스 열고** 잠시 돌려서 차이 보세요

### "yfinance가 ARM에서 안 깔려요"
- 보통 잘 깔리는데, 실패하면:
  ```bash
  sudo apt install -y libatlas-base-dev gfortran
  pip install --upgrade pip setuptools wheel
  pip install -r requirements.txt
  ```

### "공유기 포트포워딩 했는데 외부에서 안 됨"
- 한국 인터넷은 **이중 NAT** 가 많아서 ISP에 "공인 IP 요청" 필요할 수 있음
- KT/SK/LG 모두 "포트개방" 또는 "공인IP 요청" 가능 (전화로 요청)
- 또는 **Cloudflare Tunnel** 로 우회 (포트포워딩 없이 외부 노출, 무료)

---

## 📊 Oracle vs Pi 비교

| 항목 | Oracle Free | Raspberry Pi 4/5 |
|------|-------------|------------------|
| 비용 | 무료 (단, 90일 미사용 시 회수) | 초기 ~15만원, 전기료 월 ~1500원 |
| 성능 | 4 vCPU, 24GB RAM | 4 cores, 4~8GB RAM |
| 네트워크 | 1Gbps, 공인IP | 집 인터넷 (보통 1Gbps인데 업로드 제한) |
| 안정성 | 99.9% | 정전 / 가족이 코드 뽑으면 끝 |
| 학습 | 클라우드 경험 | 하드웨어 손맛 + 데브옵스 |
| **결론** | 안정성 우선이면 Oracle 유지가 낫고, Pi는 "내 손에 있는 서버"의 만족감 + 학습 |

> 💡 권장: 처음엔 **Oracle + Pi 둘 다 돌려놓고** Pi가 1주일 안정적이면 Oracle 끄기.

---

## 다음 단계

1. `setup_pi.sh` 실행 → 기본 셋업 완료
2. `EXTERNAL_ACCESS_PI.md` 보고 외부 접속 설정
3. 1주일간 `sudo journalctl -u manymanager -f` 로 로그 모니터링
4. 안정적이면 Oracle 종료

질문 있으면 언제든 물어봐주세요 🥧
