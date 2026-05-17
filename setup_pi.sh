#!/bin/bash
# ============================================================
# ManyManager 서버 — Raspberry Pi 셋업 스크립트
# ============================================================
# 사용법 (Pi OS Lite 64-bit 첫 부팅 후):
#   git clone https://github.com/Yuhyeonsang/ManyManager.git
#   cd ManyManager
#   chmod +x setup_pi.sh
#   ./setup_pi.sh
#
# 기존 setup_server.sh (Oracle용) 와 차이점:
#   - 사용자: ubuntu → pi
#   - 스왑: 1GB → 2GB (Pi RAM이 더 작음)
#   - apt: libatlas/gfortran 추가 (yfinance/pandas 빌드용)
#   - 메모리 한도: 800M → 1500M (Pi 4 4GB 기준)
#   - CPU 거버너: ondemand → performance (응답속도 우선)
# ============================================================
set -e

REPO_URL="https://github.com/Yuhyeonsang/ManyManager.git"
PROJECT_DIR="/home/pi/ManyManager"
SERVICE_NAME="manymanager"
PI_USER="pi"

echo "============================================================"
echo " ManyManager — Raspberry Pi Setup"
echo "============================================================"

# ─────────────────────────────────────────────
# 0. Pi 확인
# ─────────────────────────────────────────────
if [ ! -f /proc/device-tree/model ] || ! grep -qi "raspberry pi" /proc/device-tree/model; then
    echo "⚠️  이 스크립트는 Raspberry Pi 전용입니다."
    echo "    Oracle/일반 서버는 setup_server.sh 사용하세요."
    read -p "그래도 계속할까요? (y/N): " yn
    [[ "$yn" != "y" && "$yn" != "Y" ]] && exit 1
fi

PI_MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "Unknown")
echo "감지된 모델: $PI_MODEL"

# ─────────────────────────────────────────────
# 1. 시스템 패키지 업데이트 + 필수 도구
# ─────────────────────────────────────────────
echo ""
echo "[1/9] 시스템 패키지 설치 중 (5~10분)..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-venv python3-pip python3-dev \
    git curl ufw \
    libatlas-base-dev gfortran \
    build-essential

# ─────────────────────────────────────────────
# 2. 2GB 스왑 추가 (Pi RAM 부족 대비)
# ─────────────────────────────────────────────
echo ""
echo "[2/9] 스왑 2GB 설정..."
# Pi OS 기본 dphys-swapfile 비활성화 후 직접 관리
if systemctl is-active --quiet dphys-swapfile 2>/dev/null; then
    sudo dphys-swapfile swapoff || true
    sudo systemctl disable dphys-swapfile || true
fi

if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    if ! grep -q "/swapfile" /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
    fi
    echo "  ✔ 2GB 스왑 활성화"
else
    echo "  ✔ 스왑 이미 존재 ($(swapon --show=NAME,SIZE --noheadings))"
fi

# ─────────────────────────────────────────────
# 3. CPU 거버너 → performance (응답 빠르게)
# ─────────────────────────────────────────────
echo ""
echo "[3/9] CPU 거버너 설정..."
if [ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]; then
    echo 'GOVERNOR="performance"' | sudo tee /etc/default/cpufrequtils > /dev/null
    for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        echo "performance" | sudo tee "$cpu" > /dev/null 2>&1 || true
    done
    echo "  ✔ performance 거버너 적용 (재부팅 후에도 유지)"
else
    echo "  ⚠️  cpufreq 미지원 — 건너뜀"
fi

# ─────────────────────────────────────────────
# 4. 깃 클론 (또는 이미 있으면 pull)
# ─────────────────────────────────────────────
echo ""
echo "[4/9] 코드 다운로드..."
if [ ! -d "$PROJECT_DIR" ]; then
    git clone "$REPO_URL" "$PROJECT_DIR"
else
    cd "$PROJECT_DIR" && git pull origin main
fi
cd "$PROJECT_DIR"

# ─────────────────────────────────────────────
# 5. Python 가상환경 + 의존성
# ─────────────────────────────────────────────
echo ""
echo "[5/9] Python venv + 의존성 설치 (10~20분, Pi라 좀 걸림)..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --quiet --upgrade pip setuptools wheel
# pandas/yfinance가 ARM 휠 없으면 컴파일하느라 오래 걸림. piwheels 사용 권장
pip install --quiet --extra-index-url https://www.piwheels.org/simple/ -r requirements.txt

# ─────────────────────────────────────────────
# 6. .env 템플릿 생성
# ─────────────────────────────────────────────
echo ""
echo "[6/9] .env 파일 확인..."
if [ ! -f ".env" ]; then
    cat > .env <<'EOF'
GEMINI_API_KEY=PUT_YOUR_KEY_HERE
NAVER_CLIENT_ID=PUT_YOUR_KEY_HERE
NAVER_CLIENT_SECRET=PUT_YOUR_KEY_HERE
DART_API_KEY=PUT_YOUR_KEY_HERE
GEMINI_MODEL=gemini-2.5-flash
CACHE_WARM_ENABLED=0
EOF
    chmod 600 .env
    echo "  ⚠️  .env 템플릿 생성됨 — nano .env 로 키 4개 채우세요"
    echo "       (또는 기존 백업: tar xzf ~/manymanager_backup.tar.gz)"
else
    echo "  ✔ .env 이미 존재"
fi

# ─────────────────────────────────────────────
# 7. systemd 서비스 등록
# ─────────────────────────────────────────────
echo ""
echo "[7/9] systemd 서비스 등록..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=ManyManager FastAPI Server (Raspberry Pi)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${PI_USER}
Group=${PI_USER}
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${PROJECT_DIR}/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=10
# Pi 4 (4GB) 기준 — 8GB면 2500M 까지 올려도 OK
MemoryMax=1500M
# OOM 방어
OOMScoreAdjust=-100
# 로그 양 제한 (Pi SD 카드 보호)
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}.service > /dev/null 2>&1

# ─────────────────────────────────────────────
# 8. 방화벽 (ufw)
# ─────────────────────────────────────────────
echo ""
echo "[8/9] 방화벽 설정..."
sudo ufw --force enable > /dev/null 2>&1
sudo ufw allow 22/tcp > /dev/null 2>&1   # SSH
sudo ufw allow 8000/tcp > /dev/null 2>&1 # FastAPI
sudo ufw allow 80/tcp > /dev/null 2>&1   # HTTP (Caddy용)
sudo ufw allow 443/tcp > /dev/null 2>&1  # HTTPS (Caddy용)
echo "  ✔ 22, 80, 443, 8000 포트 개방"

# ─────────────────────────────────────────────
# 9. 서비스 시작 + 헬스체크
# ─────────────────────────────────────────────
echo ""
echo "[9/9] uvicorn 시작..."
sudo systemctl restart ${SERVICE_NAME}.service
sleep 8

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✔ uvicorn 정상 응답 (HTTP 200)"
else
    echo "  ⚠️  헬스체크 실패 (HTTP $HTTP_CODE)"
    echo "       → .env API 키 확인 후 'sudo systemctl restart ${SERVICE_NAME}'"
    echo "       → 로그: sudo journalctl -u ${SERVICE_NAME} -n 50"
fi

# ─────────────────────────────────────────────
# 완료 안내
# ─────────────────────────────────────────────
LOCAL_IP=$(hostname -I | awk '{print $1}')
TEMP=$(vcgencmd measure_temp 2>/dev/null | sed 's/temp=//' || echo "N/A")

echo ""
echo "============================================================"
echo " ✔ Raspberry Pi 셋업 완료"
echo "============================================================"
echo ""
echo " Pi 모델  : ${PI_MODEL}"
echo " 로컬 IP  : ${LOCAL_IP}"
echo " CPU 온도 : ${TEMP}"
echo " URL      : http://${LOCAL_IP}:8000/"
echo ""
echo " 다음 할 일:"
echo "   1) .env 에 API 키 채우기 (또는 기존 백업 복원):"
echo "      nano ${PROJECT_DIR}/.env"
echo "      # 또는: tar xzf ~/manymanager_backup.tar.gz -C ${PROJECT_DIR}"
echo ""
echo "   2) 서비스 재시작:"
echo "      sudo systemctl restart ${SERVICE_NAME}"
echo ""
echo "   3) 상태 확인:"
echo "      sudo systemctl status ${SERVICE_NAME}"
echo "      curl http://localhost:8000/api/diagnostics"
echo ""
echo "   4) 로그 보기 (Ctrl+C 로 나가기):"
echo "      sudo journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "   5) 외부 접속 설정 → EXTERNAL_ACCESS_PI.md 참고"
echo ""
echo " 🥧 가족이 코드 뽑지 않게 케이블 잘 정리하세요"
echo "============================================================"
