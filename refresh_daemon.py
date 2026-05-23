"""
캐시 갱신 데몬 — cache_refresher 를 무한 루프로 돌림.

작동 방식
─────────
1) cache_refresher.main() 한 사이클 실행 (모든 종목 분석 → DB 덮어쓰기)
2) CYCLE_REST_SEC 만큼 대기 (Gemini RPM/일일 quota 보호)
3) 반복

cron 대신 이걸 쓰는 이유
────────────────────────
- 사이클이 5분 넘게 걸리면 cron 이 겹쳐 돌 위험 (DB 락, API 두 배)
- 단일 프로세스라 메모리 효율적 (pykrx fundamental 캐시 재사용)
- SIGTERM 받으면 현재 사이클 끝나고 정상 종료

실행
────
  python3 refresh_daemon.py

systemd 로 띄우는 게 권장 (재부팅·크래시 자동 복구):
  sudo systemctl enable --now fund-refresh.service

환경변수
───────
  REFRESH_REST_SEC      사이클 사이 휴식 (초). 기본 900 (15분)
  LLM_RPM_SLEEP_SEC     종목 사이 Gemini 보호 sleep. 기본 4
                        (cache_refresher 가 사용)
"""
import os
import sys
import time
import signal
import logging
import traceback
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from cache_refresher import main as run_one_cycle

CYCLE_REST_SEC = int(os.getenv("REFRESH_REST_SEC", "900"))   # 사이클 후 휴식 (기본 15분)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("refresh-daemon")

_running = True


def _stop(signum, frame):
    """SIGINT/SIGTERM 받으면 현재 사이클 끝나고 종료."""
    global _running
    _running = False
    log.info(f"정지 신호({signum}) 수신 — 현재 사이클 완료 후 종료 예정")


signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


def _sleep_interruptible(total_sec: int) -> None:
    """초 단위로 끊어 sleep — 정지 신호에 즉시 반응."""
    end = time.monotonic() + total_sec
    while _running and time.monotonic() < end:
        time.sleep(min(1.0, end - time.monotonic()))


def main():
    log.info("=" * 50)
    log.info(f"refresh_daemon 시작 (사이클 후 휴식 {CYCLE_REST_SEC}초)")
    log.info("=" * 50)

    cycle_count = 0
    while _running:
        cycle_count += 1
        t0 = time.monotonic()
        log.info(f"━━━ 사이클 #{cycle_count} 시작 ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ━━━")

        try:
            run_one_cycle()
        except SystemExit:
            log.info("cache_refresher 가 SystemExit — 데몬 종료")
            break
        except Exception:
            log.error(f"사이클 #{cycle_count} 실패:\n{traceback.format_exc()}")

        elapsed = time.monotonic() - t0
        log.info(f"━━━ 사이클 #{cycle_count} 종료 (소요 {elapsed:.0f}초) ━━━")

        if not _running:
            break

        log.info(f"⏸  다음 사이클까지 {CYCLE_REST_SEC}초 대기")
        _sleep_interruptible(CYCLE_REST_SEC)

    log.info("refresh_daemon 종료")


if __name__ == "__main__":
    main()
