#!/bin/bash
# ============================================================
# ManyManager 서버 한 방 셋업 스크립트
# ============================================================
# 사용법 (새 Oracle Ubuntu 인스턴스 SSH 접속 후):
#   curl -sSL https://raw.githubusercontent.com/Yuhyeonsang/ManyManager/main/setup_server.sh | bash
#
# 또는 git clone 후:
#   bash setup_server.sh
# ============================================================
set -e

REPO_URL="https://github.com/Yuhyeonsang/ManyManager.git"
PROJECT_DIR="/home/ubuntu/ManyManager"
SERVICE_NAME="manymanager"

echo "============================================================"
echo " ManyManager Server Setup"
echo "============================================================"

# ─────────────────────────────────────────────
# 1. 시스템 패키지 업데이트 + 필수 도구 설치
# ─────────────────────────────────────────────
echo ""
echo "[1/8] 시스템 패키지 설치 중..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-venv python3-pip \
    git curl ufw

# ─────────────────────────────────────────────
# 2. 1GB 스왑 추가 (메모리 안전장치)
# ─────────────────────────────────────────────
echo ""
echo "[2/8] 스왑 1GB 설정..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 1G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    if ! grep -q "/swapfile" /etc/fstab; then
        echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab > /dev/null
    fi
    echo "  ✔ 스왑 활성화"
else
    echo "  ✔ 스왑 이미 존재"
fi

# ─────────────────────────────────────────────
# 3. 깃 클론 (또는 이미 있으면 pull)
# ─────────────────────────────────────────────
echo ""
echo "[3/8] 코드 다운로드..."
if [ ! -d "$PROJECT_DIR" ]; then
    git clone "$REPO_URL" "$PROJECT_DIR"
else
    cd "$PROJECT_DIR" && git pull origin main
fi
cd "$PROJECT_DIR"

# ─────────────────────────────────────────────
# 4. Python 가상환경 + 의존성
# ─────────────────────────────────────────────
echo ""
echo "[4/8] Python venv + 의존성 설치 (시간 좀 걸림)..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# ─────────────────────────────────────────────
# 5. .env 템플릿 생성 (키는 사용자가 채워야 함)
# ─────────────────────────────────────────────
echo ""
echo "[5/8] .env 파일 확인..."
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
else
    echo "  ✔ .env 이미 존재"
fi

# ─────────────────────────────────────────────
# 6. systemd 서비스 등록 (reboot 해도 자동 시작)
# ─────────────────────────────────────────────
echo ""
echo "[6/8] systemd 서비스 등록..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=ManyManager FastAPI Server
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=${PROJECT_DIR}
EnvironmentFile=${PROJECT_DIR}/.env
ExecStart=${PROJECT_DIR}/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5
# 메모리 안전장치 — 800MB 넘으면 OOM 방지로 재시작
MemoryMax=800M

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}.service > /dev/null 2>&1

# ─────────────────────────────────────────────
# 7. 방화벽 (Ubuntu 내부 ufw — Oracle 보안 규칙은 별개)
# ─────────────────────────────────────────────
echo ""
echo "[7/8] 방화벽 설정..."
sudo ufw --force enable > /dev/null 2>&1
sudo ufw allow 22/tcp > /dev/null 2>&1   # SSH
sudo ufw allow 8000/tcp > /dev/null 2>&1 # FastAPI
echo "  ✔ 22, 8000 포트 개방"

# ─────────────────────────────────────────────
# 8. 서비스 시작
# ─────────────────────────────────────────────
echo ""
echo "[8/8] uvicorn 시작..."
sudo systemctl restart ${SERVICE_NAME}.service
sleep 5

# 헬스체크
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
    echo "  ✔ uvicorn 정상 응답 (HTTP 200)"
else
    echo "  ⚠️  헬스체크 실패 (HTTP $HTTP_CODE) — .env 키 확인 필요할 수 있음"
fi

# ─────────────────────────────────────────────
# 완료 안내
# ─────────────────────────────────────────────
PUBLIC_IP=$(curl -s ifconfig.me 2>/dev/null || echo "확인불가")

echo ""
echo "============================================================"
echo " ✔ 셋업 완료"
echo "============================================================"
echo ""
echo " 서버 IP : ${PUBLIC_IP}"
echo " URL     : http://${PUBLIC_IP}:8000/"
echo ""
echo " 다음 할 일:"
echo "   1) .env 에 API 키 4개 채우기:"
echo "      nano ${PROJECT_DIR}/.env"
echo ""
echo "   2) 서비스 재시작:"
echo "      sudo systemctl restart ${SERVICE_NAME}"
echo ""
echo "   3) 상태 확인:"
echo "      sudo systemctl status ${SERVICE_NAME}"
echo "      curl http://localhost:8000/api/diagnostics"
echo ""
echo "   4) 로그 보기:"
echo "      sudo journalctl -u ${SERVICE_NAME} -f"
echo ""
echo " 🔒 GitHub Secrets 의 ORACLE_HOST 값을 ${PUBLIC_IP} 로 업데이트하세요"
echo " 📱 앱 설정의 BASE_URL 도 http://${PUBLIC_IP}:8000 으로 변경하세요"
echo "============================================================"
