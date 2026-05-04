#!/usr/bin/env bash
# 수동 배포 스크립트 (GitHub Actions 가 막힐 때 백업용)
# 사용: ssh ubuntu@<server> "bash /opt/fund-manager/deploy/deploy.sh"
set -euo pipefail

cd /opt/fund-manager
echo "▶ git pull"
git fetch origin main
git reset --hard origin/main

echo "▶ pip install"
source .venv/bin/activate
pip install -r requirements.txt --quiet

echo "▶ systemctl restart"
sudo systemctl restart fund-manager
sleep 2

echo "▶ 헬스체크"
curl -sS -o /dev/null -w "HTTP %{http_code} - %{time_total}s\n" http://localhost:8000/
echo "✔ Deployed at $(date '+%F %T')"
