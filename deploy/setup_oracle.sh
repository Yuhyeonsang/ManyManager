#!/usr/bin/env bash
# ============================================================
# Oracle Cloud Ubuntu 22.04/24.04 ARM 인스턴스 1회 설치 스크립트
#
# 사용법 (오라클 VM 에 SSH 접속 후):
#   1) git clone https://github.com/<USER>/<REPO>.git /opt/fund-manager
#   2) cd /opt/fund-manager
#   3) bash deploy/setup_oracle.sh <DUCKDNS_도메인>
#
# 예: bash deploy/setup_oracle.sh myfund.duckdns.org
# ============================================================
set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
    echo "사용법: $0 <DUCKDNS_도메인>"
    echo "예:    $0 myfund.duckdns.org"
    exit 1
fi

REPO_DIR="/opt/fund-manager"
SERVICE_USER="ubuntu"

echo "▶ [1/8] APT 패키지 설치"
sudo apt-get update -y
sudo apt-get install -y \
    python3.12 python3.12-venv python3-pip \
    git curl ufw \
    debian-keyring debian-archive-keyring apt-transport-https \
    iptables-persistent

echo "▶ [2/8] Caddy 설치 (자동 HTTPS 리버스 프록시)"
if ! command -v caddy &>/dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
        sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
        sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    sudo apt-get update -y
    sudo apt-get install -y caddy
fi

echo "▶ [3/8] 저장소 권한 설정"
sudo chown -R "$SERVICE_USER:$SERVICE_USER" "$REPO_DIR"

cd "$REPO_DIR"

echo "▶ [4/8] Python 가상환경 + 의존성"
if [[ ! -d ".venv" ]]; then
    python3.12 -m venv .venv
fi
source .venv/bin/activate
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
deactivate

echo "▶ [5/8] .env 확인"
if [[ ! -f ".env" ]]; then
    cat <<EOF

⚠ .env 파일이 없습니다. 다음 키들을 채워서 만드세요:
    nano $REPO_DIR/.env

GEMINI_API_KEY=...
NAVER_CLIENT_ID=...
NAVER_CLIENT_SECRET=...
DART_API_KEY=...
FUND_DB=/opt/fund-manager/fund_manager.db

(작업 후 다시 이 스크립트를 실행하면 됩니다)
EOF
    exit 1
fi

echo "▶ [6/8] systemd 서비스 등록"
sudo cp deploy/fund-manager.service /etc/systemd/system/fund-manager.service
sudo touch /var/log/fund-manager.log
sudo chown "$SERVICE_USER:$SERVICE_USER" /var/log/fund-manager.log
sudo systemctl daemon-reload
sudo systemctl enable fund-manager
sudo systemctl restart fund-manager
sleep 2
sudo systemctl status fund-manager --no-pager | head -8

echo "▶ [7/8] Caddy 설정 (도메인=$DOMAIN)"
sudo sed "s|myfund.duckdns.org|$DOMAIN|g" deploy/Caddyfile.example | \
    sudo tee /etc/caddy/Caddyfile >/dev/null
sudo mkdir -p /var/log/caddy
sudo chown caddy:caddy /var/log/caddy
sudo systemctl restart caddy
sleep 2

echo "▶ [8/8] 방화벽 — 80, 443 포트 허용"
# Oracle VM 의 iptables 기본 규칙 우회
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT 2>/dev/null || true
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT 2>/dev/null || true
sudo netfilter-persistent save || true

cat <<EOF

==========================================================
✔ 설치 완료!

테스트:
   curl -I https://$DOMAIN/

오라클 콘솔의 VCN > Security List 에서 Ingress 규칙도 추가하세요:
   • TCP 80  (0.0.0.0/0)
   • TCP 443 (0.0.0.0/0)

서비스 상태:
   sudo systemctl status fund-manager
   sudo systemctl status caddy
   tail -f /var/log/fund-manager.log
==========================================================
EOF
