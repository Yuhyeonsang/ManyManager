#!/bin/bash
# ============================================================
# ManyManager — Raspberry Pi 3B+ 전용 셋업 (RAM 1GB 제약 대응)
# ============================================================
# Pi 4/5 용 setup_pi.sh 와의 차이:
#   - 스왑: 2GB → 4GB (RAM 1GB 보완)
#   - zram 추가 (압축 메모리, 스왑보다 빠름)
#   - MemoryMax: 1500M → 600M
#   - GPU 메모리: 64M → 16M (서버라 GPU 안 씀)
#   - piwheels 필수 (컴파일하면 1시간+)
#   - 백그라운드 작업(cache_refresher) 기본 비활성화
#   - 시스템 서비스 다이어트 (블루투스/aviahi 등 끔)
#
# 사용법:
#   ./test_pi3.sh    # 먼저 사양 테스트 통과 확인
#   ./setup_pi3.sh   # 그 다음 셋업
# ============================================================
set -e

REPO_URL="https://github.com/Yuhyeonsang/ManyManager.git"
PROJECT_DIR="/home/pi/ManyManager"
SERVICE_NAME="manymanager"
PI_USER="pi"

echo "============================================================"
echo " ManyManager — Pi 3B+ Setup (RAM 1GB 최적화)"
echo "============================================================"

# ─────────────────────────────────────────────
# 0. Pi 3B+ 확인
# ─────────────────────────────────────────────
PI_MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "Unknown")
echo "감지된 모델: $PI_MODEL"

TOTAL_MB=$(($(grep MemTotal /proc/meminfo | awk '{print $2}') / 1024))
echo "총 RAM: ${TOTAL_MB} MB"

if [ "$TOTAL_MB" -gt 1500 ]; then
    echo "⚠️  RAM이 1GB보다 큰 Pi 인 것 같아요. setup_pi.sh 쓰는 게 나을 수 있음"
    read -p "그래도 Pi 3B+ 모드(MemoryMax 600M)로 진행할까요? (y/N): " yn
    [[ "$yn" != "y" && "$yn" != "Y" ]] && exit 1
fi

# ─────────────────────────────────────────────
# 1. GPU 메모리 줄이기 (서버라 그래픽 안 씀)
# ─────────────────────────────────────────────
echo ""
echo "[1/11] GPU 메모리 16MB로 줄이기 (RAM 확보)..."
if ! grep -q "^gpu_mem=" /boot/firmware/config.txt 2>/dev/null && ! grep -q "^gpu_mem=" /boot/config.txt 2>/dev/null; then
    CONFIG_PATH="/boot/firmware/config.txt"
    [ ! -f "$CONFIG_PATH" ] && CONFIG_PATH="/boot/config.txt"
    echo "gpu_mem=16" | sudo tee -a "$CONFIG_PATH" > /dev/null
    echo "  ✔ gpu_mem=16 추가 (재부팅 후 적용, 약 48MB RAM 확보)"
else
    echo "  ✔ 이미 설정됨"
fi

# ─────────────────────────────────────────────
# 2. 시스템 패키지 + 의존성
# ─────────────────────────────────────────────
echo ""
echo "[2/11] 시스템 패키지 설치 중 (5분)..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-venv python3-pip python3-dev \
    git curl ufw \
    libatlas-base-dev gfortran \
    build-essential zram-tools

# ─────────────────────────────────────────────
# 3. 안 쓰는 서비스 끄기 (RAM 절약)
# ─────────────────────────────────────────────
echo ""
echo "[3/11] 불필요 서비스 비활성화..."
for svc in bluetooth hciuart triggerhappy avahi-daemon ModemManager; do
    if systemctl is-enabled --quiet "$svc" 2>/dev/null; then
        sudo systemctl disable --now "$svc" > /dev/null 2>&1 || true
        echo "  ✔ $svc 끔"
    fi
done

# ─────────────────────────────────────────────
# 4. 스왑 4GB (Pi OS 기본 100M → 4G)
# ─────────────────────────────────────────────
echo ""
echo "[4/11] 스왑 4GB 설정..."
# Pi OS 기본 dphys-swapfile 끄기
if systemctl is-active --quiet dphys-swapfile 2>/dev/null; then
    sudo dphys-swapfile swapoff || true
    sudo systemctl disable dphys-swapfile || true
fi

if [ ! -f /swapfile ]; then
    sudo fallocate -l 4G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    if ! grep -q "/swapfile" /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
    fi
    echo "  ✔ 4GB 스왑 활성화"
else
    CUR_SIZE=$(stat -c %s /swapfile)
    if [ "$CUR_SIZE" -lt 3500000000 ]; then
        echo "  기존 스왑 작음 — 4GB로 키우는 중..."
        sudo swapoff /swapfile
        sudo fallocate -l 4G /swapfile
        sudo mkswap /swapfile
        sudo swapon /swapfile
        echo "  ✔ 4GB로 확장"
    else
        echo "  ✔ 이미 충분한 스왑"
    fi
fi

# 스왑 더 적극적으로 사용 (RAM 부족 Pi 3B+ 특성상)
echo "vm.swappiness=80" | sudo tee /etc/sysctl.d/99-swappiness.conf > /dev/null
sudo sysctl -p /etc/sysctl.d/99-swappiness.conf > /dev/null

# ─────────────────────────────────────────────
# 5. zram (압축 RAM — SD카드 마모 방지에 결정적)
# ─────────────────────────────────────────────
echo ""
echo "[5/11] zram 설정 (RAM 압축 — SD카드 보호)..."
sudo tee /etc/default/zramswap > /dev/null <<'EOF'
ALGO=zstd
PERCENT=50
PRIORITY=100
EOF
sudo systemctl enable --now zramswap > /dev/null 2>&1
echo "  ✔ zram 활성화 (RAM의 50% 압축 가상메모리)"

# ─────────────────────────────────────────────
# 6. 코드 클론
# ─────────────────────────────────────────────
echo ""
echo "[6/11] 코드 다운로드..."
if [ ! -d "$PROJECT_DIR" ]; then
    git clone "$REPO_URL" "$PROJECT_DIR"
else
    cd "$PROJECT_DIR" && git pull origin main
fi
cd "$PROJECT_DIR"

# ─────────────────────────────────────────────
# 7. Python venv + 의존성 (piwheels 필수)
# ─────────────────────────────────────────────
echo ""
echo "[7/11] Python venv + 의존성 (15~25분, Pi 3B+ 라 인내심 필요)..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

# piwheels을 메인 인덱스로 설정 → 컴파일 회피
mkdir -p ~/.config/pip
cat > ~/.config/pip/pip.conf <<EOF
[global]
extra-index-url=https://www.piwheels.org/simple
EOF

pip install --quiet --upgrade pip setuptools wheel
pip install --quiet -r requirements.txt

# ─────────────────────────────────────────────
# 8. .env 템플릿
# ─────────────────────────────────────────────
echo ""
echo "[8/11] .env 파일 확인..."
if [ ! -f ".env" ]; then
    cat > .env <<'EOF'
GEMINI_API_KEY=PUT_YOUR_KEY_HERE
NAVER_CLIENT_ID=PUT_YOUR_KEY_HERE
NAVER_CLIENT_SECRET=PUT_YOUR_KEY_HERE
DART_API_KEY=PUT_YOUR_KEY_HERE
GEMINI_MODEL=gemini-2.5-flash
# Pi 3B+ 최적화: 캐시 워머 끄기 (백그라운드 작업 RAM 절약)
CACHE_WARM_ENABLED=0
# 모니터링 루프 간격 늘리기 (15분 → 60분)
LOOP_INTERVAL_MIN=60
EOF
    chmod 600 .env
    echo "  ⚠️  .env 템플릿 생성 — nano .env 로 키 채우세요"
else
    echo "  ✔ .env 이미 존재"
fi

# ─────────────────────────────────────────────
# 9. systemd 서비스 (Pi 3B+ 최적화)
# ─────────────────────────────────────────────
echo ""
echo "[9/11] systemd 서비스 등록 (Pi 3B+ 메모리 600M 제한)..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=ManyManager FastAPI Server (Pi 3B+ optimized)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${PI_USER}
Group=${PI_USER}
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env

# uvicorn 단일 워커 (Pi 3B+ 는 동시성 욕심 X)
ExecStart=${PROJECT_DIR}/.venv/bin/uvicorn main:app \\
    --host 0.0.0.0 --port 8000 \\
    --workers 1 \\
    --limit-concurrency 10 \\
    --timeout-keep-alive 30

Restart=on-failure
RestartSec=15

# Pi 3B+ — 600M 초과 시 systemd가 죽이고 재시작 (OOM 방지)
MemoryMax=600M
MemoryHigh=500M

# OOM killer가 이 프로세스 마지막에 죽이게
OOMScoreAdjust=-100

# CPU 한도 (다른 작업 위해 80%만 사용)
CPUQuota=320%

# 로그 양 줄이기 (SD 카드 마모 방지)
StandardOutput=journal
StandardError=journal
LogRateLimitIntervalSec=30s
LogRateLimitBurst=100

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}.service > /dev/null 2>&1

# ─────────────────────────────────────────────
# 10. journald 로그 양 제한 (SD 카드 보호)
# ─────────────────────────────────────────────
echo ""
echo "[10/11] journald 로그 제한 (SD 카드 보호)..."
sudo mkdir -p /etc/systemd/journald.conf.d
sudo tee /etc/systemd/journald.conf.d/00-pi3-limits.conf > /dev/null <<EOF
[Journal]
SystemMaxUse=100M
SystemKeepFree=200M
RuntimeMaxUse=50M
MaxFileSec=1day
EOF
sudo systemctl restart systemd-journald

# ─────────────────────────────────────────────
# 11. 방화벽 + 시작
# ─────────────────────────────────────────────
echo ""
echo "[11/11] 방화벽 + 서비스 시작..."
sudo ufw --force enable > /dev/null 2>&1
sudo ufw allow 22/tcp > /dev/null 2>&1
sudo ufw allow 8000/tcp > /dev/null 2>&1
sudo ufw allow 80/tcp > /dev/null 2>&1
sudo ufw allow 443/tcp > /dev/null 2>&1

sudo systemctl restart ${SERVICE_NAME}.service
sleep 15  # Pi 3B+ 는 startup 느림

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✔ uvicorn 정상 응답 (HTTP 200)"
else
    echo "  ⚠️  헬스체크 실패 (HTTP $HTTP_CODE) — 30초 더 기다리세요"
    sleep 30
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ || echo "000")
    echo "    재시도: HTTP $HTTP_CODE"
fi

# ─────────────────────────────────────────────
# 완료
# ─────────────────────────────────────────────
LOCAL_IP=$(hostname -I | awk '{print $1}')
TEMP=$(vcgencmd measure_temp 2>/dev/null | sed 's/temp=//' || echo "N/A")
MEM=$(free -h | grep Mem | awk '{print $3 " / " $2}')
SWAP=$(free -h | grep Swap | awk '{print $3 " / " $2}')

echo ""
echo "============================================================"
echo " ✔ Pi 3B+ 셋업 완료"
echo "============================================================"
echo ""
echo " 모델     : ${PI_MODEL}"
echo " 로컬 IP  : ${LOCAL_IP}"
echo " CPU 온도 : ${TEMP}"
echo " RAM 사용 : ${MEM}"
echo " 스왑 사용: ${SWAP}"
echo " URL      : http://${LOCAL_IP}:8000/"
echo ""
echo " ⚠️  Pi 3B+ 한계 안내:"
echo "    - 응답 속도: Pi 5의 3~5배 느림"
echo "    - 동시 처리: 한 명이 무난, 2명 이상은 대기 발생"
echo "    - 종목 수: 한 번에 5~10개 권장 (그 이상은 OOM 위험)"
echo "    - 자세히: PI3_NOTES.md 참고"
echo ""
echo " 다음 단계:"
echo "    1) .env 키 채우기:   nano ${PROJECT_DIR}/.env"
echo "    2) 또는 백업 복원:    tar xzf ~/manymanager_backup.tar.gz"
echo "    3) 재시작:           sudo systemctl restart ${SERVICE_NAME}"
echo "    4) 로그:             sudo journalctl -u ${SERVICE_NAME} -f"
echo "    5) ⚠️  재부팅 1회 권장 (gpu_mem 변경 적용)"
echo ""
echo " 🥧 Pi 3B+ 에서 잘 돌아가길 빌어요"
echo "============================================================"
