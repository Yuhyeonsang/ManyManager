#!/bin/bash
# ============================================================
# ManyManager — Pi 3B+ 사양 테스트 (Feasibility Check)
# ============================================================
# 셋업 전에 "내 Pi 3B+가 진짜 이거 돌릴 수 있나?" 확인하는 진단 스크립트.
#
# 실행 시간: 약 15~25분 (pandas/yfinance 처음 설치하느라 오래 걸림)
#
# 사용법:
#   git clone https://github.com/Yuhyeonsang/ManyManager.git
#   cd ManyManager
#   chmod +x test_pi3.sh
#   ./test_pi3.sh
#
# 결과: ~/pi3_feasibility_report.txt 에 점수/판정 저장
# ============================================================
set -e

REPORT="$HOME/pi3_feasibility_report.txt"
TEMP_VENV="/tmp/pi3_test_venv"

# 색깔
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

mb() { echo "$(($1 / 1024)) MB"; }

# 리포트 초기화
{
    echo "============================================================"
    echo " ManyManager — Pi 3B+ Feasibility Report"
    echo " 생성: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "============================================================"
    echo ""
} > "$REPORT"

log() { echo -e "$@" | tee -a "$REPORT"; }
logfile() { echo -e "$@" >> "$REPORT"; }

# ─────────────────────────────────────────────
# 1. 모델 확인
# ─────────────────────────────────────────────
log ""
log "[1/7] 하드웨어 확인"
PI_MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo "Unknown")
log "  모델: $PI_MODEL"

if echo "$PI_MODEL" | grep -qi "3 Model B Plus"; then
    log "  ✔ Pi 3B+ 확인됨"
elif echo "$PI_MODEL" | grep -qi "raspberry pi"; then
    log "  ⚠️  Pi 3B+가 아닙니다. 그래도 테스트는 진행됨"
else
    log "  ❌ Pi가 아닌 것 같습니다"
fi

# CPU
CPU_CORES=$(nproc)
CPU_FREQ=$(cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq 2>/dev/null | awk '{print $1/1000 " MHz"}' || echo "N/A")
log "  CPU: ${CPU_CORES}코어 @ ${CPU_FREQ}"

# RAM
TOTAL_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_MB=$((TOTAL_KB / 1024))
log "  RAM: ${TOTAL_MB} MB"
if [ "$TOTAL_MB" -lt 900 ]; then
    log "    ${RED}⚠️  1GB 미만 — 운영 어려움${NC}"
fi

# SD 카드
SD_SIZE=$(df -h / | tail -1 | awk '{print $2}')
SD_AVAIL=$(df -h / | tail -1 | awk '{print $4}')
log "  SD: ${SD_SIZE} (사용가능 ${SD_AVAIL})"

# CPU 온도
TEMP=$(vcgencmd measure_temp 2>/dev/null | sed 's/temp=//' || echo "N/A")
log "  현재 CPU 온도: $TEMP"

# ─────────────────────────────────────────────
# 2. 베이스라인 메모리
# ─────────────────────────────────────────────
log ""
log "[2/7] 베이스라인 메모리 측정 (Pi OS 자체 사용량)"
BASELINE_USED_KB=$(free | grep Mem | awk '{print $3}')
BASELINE_USED_MB=$((BASELINE_USED_KB / 1024))
BASELINE_FREE_MB=$(( (TOTAL_KB - BASELINE_USED_KB) / 1024 ))
log "  OS 사용중: ${BASELINE_USED_MB} MB"
log "  사용가능: ${BASELINE_FREE_MB} MB"

# ─────────────────────────────────────────────
# 3. 시스템 패키지 + 의존성 설치
# ─────────────────────────────────────────────
log ""
log "[3/7] 필수 패키지 설치 중 (3~5분)..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3 python3-venv python3-pip python3-dev \
    libatlas-base-dev gfortran build-essential >> "$REPORT" 2>&1
log "  ✔ 설치 완료"

# ─────────────────────────────────────────────
# 4. venv + pandas/yfinance 설치 (가장 오래 걸리는 단계)
# ─────────────────────────────────────────────
log ""
log "${YELLOW}[4/7] Python venv + pandas/yfinance 설치 (10~20분, 인내심 필요)${NC}"
log "  ${YELLOW}→ Pi 3B+ 는 piwheels 안 쓰면 컴파일로 1시간+ 걸릴 수 있음${NC}"

START=$(date +%s)
rm -rf "$TEMP_VENV"
python3 -m venv "$TEMP_VENV"
source "$TEMP_VENV/bin/activate"
pip install --quiet --upgrade pip setuptools wheel >> "$REPORT" 2>&1

# piwheels 강제 — ARM7 휠 미리 빌드된 거 받아옴
pip install --quiet \
    --extra-index-url https://www.piwheels.org/simple/ \
    pandas yfinance fastapi 'uvicorn[standard]' requests python-dotenv >> "$REPORT" 2>&1

END=$(date +%s)
INSTALL_SEC=$((END - START))
log "  ✔ 설치 완료 (${INSTALL_SEC}초)"
if [ "$INSTALL_SEC" -gt 1800 ]; then
    log "    ${YELLOW}⚠️  30분 초과 — piwheels 미사용 가능성${NC}"
fi

# ─────────────────────────────────────────────
# 5. 임포트 메모리 코스트 측정
# ─────────────────────────────────────────────
log ""
log "[5/7] 라이브러리 임포트 메모리 측정"

IMPORT_MB=$(python3 -c "
import resource, sys
def mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
base = mb()
import pandas as pd
import yfinance as yf
import fastapi
from fastapi import FastAPI
loaded = mb()
print(loaded - base)
" 2>&1)
log "  pandas+yfinance+fastapi 임포트: +${IMPORT_MB} MB"

# ─────────────────────────────────────────────
# 6. 실제 yfinance 호출 (메모리 + 시간)
# ─────────────────────────────────────────────
log ""
log "[6/7] yfinance 실제 호출 테스트 (네트워크 필요)"

TEST_OUTPUT=$(python3 << 'PYEOF' 2>&1
import resource, time, sys
def mb(): return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024

import pandas as pd
import yfinance as yf

start_mb = mb()
start_t = time.time()
try:
    # 5개 종목 동시 다운로드 — 실제 사용 패턴
    data = yf.download(
        ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA'],
        period='1mo', progress=False, threads=False
    )
    elapsed = time.time() - start_t
    peak = mb()
    rows = len(data) if data is not None else 0
    print(f"PEAK_MB={peak}")
    print(f"DELTA_MB={peak - start_mb}")
    print(f"ELAPSED_SEC={elapsed:.1f}")
    print(f"ROWS={rows}")
    print(f"STATUS=OK")
except Exception as e:
    print(f"STATUS=FAIL")
    print(f"ERROR={e}")
PYEOF
)

echo "$TEST_OUTPUT" >> "$REPORT"

STATUS=$(echo "$TEST_OUTPUT" | grep "STATUS=" | cut -d= -f2)
if [ "$STATUS" = "OK" ]; then
    PEAK=$(echo "$TEST_OUTPUT" | grep "PEAK_MB=" | cut -d= -f2)
    DELTA=$(echo "$TEST_OUTPUT" | grep "DELTA_MB=" | cut -d= -f2)
    ELAPSED=$(echo "$TEST_OUTPUT" | grep "ELAPSED_SEC=" | cut -d= -f2)
    ROWS=$(echo "$TEST_OUTPUT" | grep "ROWS=" | cut -d= -f2)
    log "  ✔ 5개 종목 1개월 데이터 다운로드 성공"
    log "    피크 메모리: ${PEAK} MB (증가분 +${DELTA} MB)"
    log "    소요 시간: ${ELAPSED} 초"
    log "    행 수: ${ROWS}"
else
    log "  ${RED}❌ yfinance 호출 실패${NC}"
    log "  $TEST_OUTPUT"
fi

# ─────────────────────────────────────────────
# 7. CPU 부하 테스트 (1분간 4코어 풀)
# ─────────────────────────────────────────────
log ""
log "[7/7] CPU 부하 테스트 (1분간 4코어 100%)"
TEMP_BEFORE=$(vcgencmd measure_temp 2>/dev/null | sed 's/temp=//' || echo "N/A")
log "  부하 전 온도: $TEMP_BEFORE"

# 4 코어 풀 가동 1분
for i in 1 2 3 4; do
    yes > /dev/null &
done
LOAD_PIDS=$(jobs -p)
sleep 60
kill $LOAD_PIDS 2>/dev/null || true
wait 2>/dev/null || true

sleep 2
TEMP_AFTER=$(vcgencmd measure_temp 2>/dev/null | sed 's/temp=//' || echo "N/A")
log "  부하 후 온도: $TEMP_AFTER"

# 쓰로틀링 확인
THROTTLED=$(vcgencmd get_throttled 2>/dev/null || echo "throttled=0x0")
log "  쓰로틀링 상태: $THROTTLED"
if echo "$THROTTLED" | grep -q "0x0$"; then
    log "  ✔ 쓰로틀링 없음"
else
    log "  ${YELLOW}⚠️  쓰로틀링 발생 — 방열판/팬 권장${NC}"
fi

# ─────────────────────────────────────────────
# 최종 판정
# ─────────────────────────────────────────────
log ""
log "============================================================"
log " 최종 판정"
log "============================================================"

SCORE=0
VERDICT=""

# RAM 점수
if [ "$BASELINE_FREE_MB" -gt 700 ]; then
    SCORE=$((SCORE + 25))
    log "  [✔] OS 후 사용가능 RAM: ${BASELINE_FREE_MB}MB (양호)"
else
    log "  [✗] OS 후 사용가능 RAM: ${BASELINE_FREE_MB}MB (부족)"
fi

# 임포트 메모리
if [ -n "$IMPORT_MB" ] && [ "$IMPORT_MB" -lt 200 ]; then
    SCORE=$((SCORE + 25))
    log "  [✔] 임포트 메모리: ${IMPORT_MB}MB (양호)"
else
    log "  [△] 임포트 메모리: ${IMPORT_MB}MB (높음)"
fi

# 피크 메모리
if [ "$STATUS" = "OK" ]; then
    if [ "$PEAK" -lt 600 ]; then
        SCORE=$((SCORE + 30))
        log "  [✔] 5종목 처리 피크: ${PEAK}MB (1GB RAM에 안전)"
    elif [ "$PEAK" -lt 800 ]; then
        SCORE=$((SCORE + 15))
        log "  [△] 5종목 처리 피크: ${PEAK}MB (스왑 의존 — 느림)"
    else
        log "  [✗] 5종목 처리 피크: ${PEAK}MB (RAM 초과 — 위험)"
    fi
else
    log "  [✗] yfinance 호출 실패"
fi

# 쓰로틀링
if echo "$THROTTLED" | grep -q "0x0$"; then
    SCORE=$((SCORE + 20))
    log "  [✔] 쓰로틀링 없음 (냉각 OK)"
else
    log "  [△] 쓰로틀링 있음 (냉각 부족)"
fi

log ""
log "  점수: ${SCORE} / 100"

if [ "$SCORE" -ge 80 ]; then
    log "  ${GREEN}판정: 🟢 운영 가능 — setup_pi3.sh 실행하세요${NC}"
elif [ "$SCORE" -ge 60 ]; then
    log "  ${YELLOW}판정: 🟡 제한적 운영 가능 — setup_pi3.sh + 백그라운드 작업 비활성화${NC}"
elif [ "$SCORE" -ge 40 ]; then
    log "  ${YELLOW}판정: 🟠 빠듯함 — 종목 수 줄이고 4GB 스왑 필수${NC}"
else
    log "  ${RED}판정: 🔴 비추천 — Pi 5 또는 Oracle Cloud 권장${NC}"
fi

log ""
log "  자세한 리포트: $REPORT"
log "============================================================"

# 정리
deactivate 2>/dev/null || true
rm -rf "$TEMP_VENV"

echo ""
echo "📄 전체 리포트가 ${REPORT} 에 저장됐어요"
echo "   cat ${REPORT} 로 다시 확인 가능"
