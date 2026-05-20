# 🥧 Raspberry Pi 3B+ 운영 노트

> Pi 4/5 가 아닌 **Pi 3B+ (1GB RAM, Cortex-A53 1.4GHz)** 에 ManyManager 서버를 올릴 때 알아둘 것들.
> 가능은 하지만 "취미용/가족용 소규모 운영" 한정. 본격 운영은 Pi 5 또는 Oracle 권장.

---

## 📊 사양 비교

| 항목 | Pi 3B+ | Pi 4 (4GB) | Pi 5 (4GB) | Oracle Free |
|------|--------|------------|------------|-------------|
| CPU | A53 1.4GHz × 4 | A72 1.5GHz × 4 | A76 2.4GHz × 4 | Ampere × 4 |
| RAM | **1GB** ⚠️ | 4GB | 4GB | 24GB |
| 상대 속도 | 1× (기준) | 2× | 4~5× | 5~6× |
| 펀드매니저 적합도 | 🟠 빠듯 | 🟢 양호 | 🟢 좋음 | 🟢 최고 |

---

## 🎯 절차 — 한 번에 보기

```bash
# 1. 사양 테스트 (필수)
chmod +x test_pi3.sh
./test_pi3.sh
# 결과 보고 점수 60점 이상이면 진행, 미만이면 Pi 5 고려

# 2. 셋업 (Pi 3B+ 전용)
chmod +x setup_pi3.sh
./setup_pi3.sh

# 3. .env 채우기
nano /home/pi/ManyManager/.env

# 4. 재시작 + 재부팅 (gpu_mem 적용 위해)
sudo systemctl restart manymanager
sudo reboot

# 5. 외부 접속은 EXTERNAL_ACCESS_PI.md 참고
```

---

## 🧠 Pi 3B+ 특화 최적화 (setup_pi3.sh 에 다 포함)

### 1. GPU 메모리 16MB
```
gpu_mem=16  → /boot/firmware/config.txt
```
서버는 그래픽 안 쓰니까 GPU에 줬던 64MB를 CPU에 돌려받음.
**효과: 약 +48MB RAM 확보**

### 2. 4GB 스왑 + 적극적 사용
```
vm.swappiness=80  (기본 60 → 80)
```
RAM 부족하면 빨리 스왑으로 밀어내라는 뜻. Pi 5라면 60이 적당하지만, Pi 3B+ 는 RAM 부족 빈도가 높아 적극적으로 가는 게 유리.

### 3. zram (압축 메모리)
```
ALGO=zstd, PERCENT=50
```
RAM의 50%를 압축된 가상 RAM으로 씀. SD카드 스왑보다 **10~50배 빠르고** SD 마모 없음.
**Pi 3B+에선 이게 진짜 결정적**.

### 4. systemd 메모리 제한
```ini
MemoryMax=600M
MemoryHigh=500M
```
앱이 600M 넘으면 자동 재시작. 시스템 전체가 OOM으로 다운되는 것 방지.

### 5. 안 쓰는 서비스 비활성
- bluetooth, hciuart, triggerhappy, avahi-daemon, ModemManager 끔
- **효과: 약 +30~50MB RAM**

### 6. uvicorn 단일 워커 + 동시성 10
```
--workers 1 --limit-concurrency 10
```
멀티 워커 쓰면 RAM 두 배. Pi 3B+는 1워커가 정답.

### 7. piwheels 강제
```
~/.config/pip/pip.conf
extra-index-url = https://www.piwheels.org/simple
```
pandas / numpy / yfinance를 컴파일하면 Pi 3B+에서 1~2시간 걸림.
piwheels는 미리 빌드된 ARMv7 휠 제공. **셋업 시간 1시간 → 15분**.

### 8. 백그라운드 작업 기본 끔
```
.env: CACHE_WARM_ENABLED=0
.env: LOOP_INTERVAL_MIN=60  (기존 15분 → 60분)
```
`cache_refresher.py` / `monitor_loop.py` 가 백그라운드에서 돌면 RAM 더 먹음.
Pi 3B+ 운영 안정화될 때까지는 꺼두기 권장.

---

## ⚠️ 알려진 한계

### 1. yfinance 동시 다운로드 → OOM 위험
```python
# ❌ 위험 (Pi 3B+ 에서 OOM 가능)
data = yf.download(['AAPL','MSFT','GOOG','TSLA','NVDA','META','AMZN','NFLX'], period='1y')

# ✔ 안전
for ticker in tickers:
    data = yf.download(ticker, period='1y')
    process(data)
    del data  # 명시적 해제
```
`data_collector.py` 코드를 한 번 살펴보고 동시 다운로드 부분 있으면 직렬화 권장.

### 2. Gemini API 응답이 큰 경우
긴 답변(8K 토큰 이상)은 Pi 3B+ 메모리 빠듯. `GEMINI_MODEL=gemini-2.5-flash` 유지하고, 프롬프트도 짧게.

### 3. 응답 시간
- API 단순 조회: 0.5~2초
- yfinance 호출 포함: 5~15초
- Gemini 분석 포함: 10~30초
- → 가족 핸드폰 앱에서 로딩 스피너 잘 보이게 UI 손볼 것

### 4. 동시 사용자
- 1명: 무난
- 2명: 가능, 약간 대기
- 3명 이상: OOM 위험, 동시성 늘리지 말고 큐잉 추천

### 5. SD 카드 수명
- 1년 24/7 가동 + 잦은 쓰기 → SD 카드 수명 단축 가능
- 대책: zram, 로그 제한 (setup_pi3.sh 다 적용됨), 그리고:
  ```bash
  # 매주 1회 백업 (cron)
  0 4 * * 0 cd /home/pi/ManyManager && tar czf /home/pi/backup-$(date +%Y%m%d).tar.gz fund_manager.db .env
  ```

---

## 🔧 모니터링 명령어 (자주 봐야 함)

```bash
# RAM/스왑/zram 현황
free -h
zramctl

# 서비스 상태
sudo systemctl status manymanager

# 로그 (실시간)
sudo journalctl -u manymanager -f

# CPU 온도
vcgencmd measure_temp

# 쓰로틀링 발생 여부
vcgencmd get_throttled
# 결과 0x0 = 정상, 다른 값 = 쓰로틀링 또는 저전압

# 메모리 많이 먹는 프로세스 TOP 5
ps aux --sort=-%mem | head -6

# SD 카드 쓰기량 (마모 체크)
sudo iotop -ao
```

---

## 🆘 자주 발생하는 문제

### "서비스가 자꾸 재시작돼요"
```bash
sudo journalctl -u manymanager -n 100
```
- `Killed` 또는 `OOM` 보이면 → 메모리 부족. `MemoryMax` 키우거나 백그라운드 작업 끄기
- `ImportError` → piwheels 설치 실패. `pip install -r requirements.txt` 재시도

### "외부에서 접속하면 응답이 30초 넘게 걸려요"
- Caddy 또는 Cloudflare Tunnel 의 timeout 늘리기
- 모바일 앱의 HTTP 타임아웃도 60초 이상으로

### "SD 카드가 read-only 됐어요"
- 마모로 인한 보호 모드 → SD 교체 시점
- 다음번엔 **Industrial 등급 SD** (예: SanDisk High Endurance) 사용

### "전원 끄고 다시 켰는데 안 켜져요"
- 정전으로 파일시스템 손상 가능
- Pi 키보드 + 모니터 연결 후 `sudo fsck /dev/mmcblk0p2`
- 다음번엔 UPS (보조배터리 + USB-C 출력) 사용

---

## 🎯 운영 1주일 체크리스트

| Day | 할 일 |
|-----|------|
| 1 | 셋업 + 가족 핸드폰에서 외부 접속 확인 |
| 2 | `journalctl -u manymanager -f` 1시간 보기 — 에러 없는지 |
| 3 | `free -h` 확인 — 스왑 사용량 추이 |
| 4 | `vcgencmd measure_temp` 확인 — 60°C 이하 유지? |
| 5 | 가족이 실제 사용 — 응답 시간 만족스러운지 물어보기 |
| 6 | DB 백업 + Oracle 비교 — 데이터 일치하는지 |
| 7 | OK면 Oracle 종료. 문제 있으면 Oracle 유지 + Pi 보조용 |

---

## 🛒 Pi 3B+ 살리는 액세서리

- **방열판 + 팬**: 24/7 가동 필수. ~5천원 케이스에 포함된 거 충분
- **A2 microSD 32GB**: SanDisk Extreme A2 (속도 + 내구성)
- **공식 5V/2.5A 어댑터**: 마이크로USB. 저전력 어댑터 쓰면 USB 죽고 SD 손상 가능
- **이더넷 케이블**: WiFi 보다 안정적, 발열도 적음
- **(선택) UPS HAT**: 정전 시 안전 종료. 1.5~3만원

---

## 💡 솔직한 권고

Pi 3B+ 로 펀드매니저 돌리는 건 **"학습 + 만족감"** 이 주목적이면 충분합니다.
하지만 진짜 24/7 가족 서비스로 쓸 거면:

1. **현재 Pi 3B+ 운영 → 1달 안정성 평가** (이 가이드대로)
2. **그 사이 Pi 5 (8GB) 살까 고민** (~13만원)
3. **Pi 5 오면 SD 그대로 옮겨 꽂으면 끝** (Pi OS 호환)

Pi 3B+ 는 이 프로젝트의 좋은 연습대 역할로 두고, 안정 운영은 Pi 5 가 정답입니다.
