"""
백그라운드 캐시 갱신 워커
cron 으로 정기 실행 → 모든 종목을 미리 분석해서 SQLite 캐시에 저장
사용자가 앱 켜면 캐시된 결과를 즉시 받아옴

사용법:
  python cache_refresher.py        # 1회 실행

cron 등록 예 (30분마다):
  # Oracle (ubuntu 사용자):
  */30 * * * * /home/ubuntu/ManyManager/.venv/bin/python /home/ubuntu/ManyManager/cache_refresher.py >> /home/ubuntu/cache_refresher.log 2>&1
  # Raspberry Pi (pi 사용자):
  */30 * * * * /home/pi/ManyManager/.venv/bin/python /home/pi/ManyManager/cache_refresher.py >> /home/pi/cache_refresher.log 2>&1
"""
import os
import sys
import json
import time
import sqlite3
import logging
from datetime import datetime, timedelta

# LLM RPM 보호 — Groq 는 분당 30 RPM 이라 짧게, Gemini fallback 은 길게
# 환경변수로 조정 가능
GEMINI_RPM_SLEEP_SEC = int(os.getenv("LLM_RPM_SLEEP_SEC", os.getenv("GEMINI_RPM_SLEEP_SEC", "4")))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# .env 자동 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# main 에서 모든 헬퍼·상수를 그대로 가져옴 (FastAPI 서버는 import 시 자동 시작 안 됨)
from main import (
    analyze_one,
    build_watchlist,
    cache_set,
    HOT_STOCKS_CACHE_KEY,
    report_builder,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("cache-refresher")


def cleanup_expired_cache():
    """1시간 이상 된 캐시 삭제 + DB 공간 회수.
    호출당 한 번 실행되므로 매 cron마다 정리됨."""
    # 우선순위: 환경변수 FUND_DB > 스크립트 옆 fund_manager.db
    # (Oracle: ubuntu, Pi: pi 등 어느 환경이든 자동 동작)
    _here = os.path.dirname(os.path.abspath(__file__))
    db_path = os.getenv("FUND_DB", os.path.join(_here, "fund_manager.db"))
    cutoff = (datetime.now() - timedelta(hours=1)).isoformat()
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.execute(
                "DELETE FROM report_cache WHERE updated_at < ?",
                (cutoff,)
            )
            deleted = cur.rowcount
            conn.commit()

            # 매일 한 번 (자정 ~ 00:30) VACUUM 해서 디스크 회수
            now_h = datetime.now().hour
            now_m = datetime.now().minute
            if now_h == 0 and now_m < 40:
                conn.execute("VACUUM")
                log.info("  ▶ DB VACUUM 완료 (일일 디스크 회수)")

        if deleted > 0:
            log.info(f"  ▶ 만료 캐시 {deleted}건 삭제")
    except Exception as e:
        log.warning(f"  ⚠ 캐시 정리 실패: {e}")


def check_disk_and_memory():
    """디스크/메모리 상태 점검 + 위험 시 경고."""
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        used_pct = used / total * 100
        if used_pct > 85:
            log.warning(f"  🚨 디스크 사용률 {used_pct:.1f}% (위험)")
        elif used_pct > 70:
            log.info(f"  ⚠ 디스크 사용률 {used_pct:.1f}%")
        else:
            log.info(f"  ✔ 디스크 {used_pct:.1f}% / 여유 {free//(1024**3)}GB")
    except Exception as e:
        log.warning(f"  ⚠ 디스크 체크 실패: {e}")


def main():
    log.info("=" * 50)
    log.info("캐시 갱신 시작")
    log.info("=" * 50)
    cleanup_expired_cache()
    check_disk_and_memory()

    wl = build_watchlist()
    log.info(f"분석 대상 {len(wl)}개 종목 (순차 처리)")

    hot_results = []
    for i, w in enumerate(wl, 1):
        # 첫 종목 외에는 Gemini RPM 보호용 sleep
        if i > 1 and GEMINI_RPM_SLEEP_SEC > 0:
            log.info(f"  ⏳ Gemini RPM 보호 — {GEMINI_RPM_SLEEP_SEC}초 대기")
            time.sleep(GEMINI_RPM_SLEEP_SEC)
        try:
            log.info(f"[{i}/{len(wl)}] {w['name']} ({w['code']}) 분석 중...")
            r = analyze_one(w["ticker"], w["code"], w["name"])

            # 핫 종목 리스트용
            hot_results.append({
                "ticker": r["ticker"],
                "name": r["name"],
                "price": r["price"],
                "change_pct": r["change_pct"],
                "grade": r["grade"],
                "score": r["score"],
                "summary": r["summary"],
            })

            # 상세 리포트용 (앱이 종목 누를 때 받는 데이터)
            intr = r["_internals"]
            fin_an = intr["fin_an"]
            picks = intr["picks"]
            bundle = intr.get("bundle") or {}
            mm = bundle.get("market_metrics") or {}
            margins = (fin_an.get("margins") or {}) if not fin_an.get("error") else {}
            growth = (fin_an.get("yoy_growth_pct") or {}) if not fin_an.get("error") else {}

            # 뉴스 구조화 + 중복 제거 (제목 정규화 기준)
            news_items_list = []
            if picks and not picks[0].get("error"):
                seen = set()
                for p in picks:
                    title = (p.get("title") or "").strip()
                    if not title:
                        continue
                    norm = "".join(ch for ch in title if ch.isalnum())[:60].lower()
                    if norm in seen:
                        continue
                    seen.add(norm)
                    news_items_list.append({
                        "title": title,
                        "link": p.get("link"),
                        "pub_date": p.get("pub_date"),
                        "impact": p.get("impact") or "중립",
                    })
                news_summary = "\n".join(
                    f"• [{ni['impact']}] {ni['title']}" for ni in news_items_list
                ) if news_items_list else "최신 뉴스 부족"
            else:
                news_summary = "최신 뉴스 부족"

            report_dict = {
                "ticker": r["ticker"],
                "name": r["name"],
                "grade": r["grade"],
                "score": r["score"],
                "news_summary": news_summary,
                "news_items": news_items_list or None,
                "financials": {
                    "per": None if mm.get("error") else mm.get("per"),
                    "pbr": None if mm.get("error") else mm.get("pbr"),
                    "roe": None if mm.get("error") else mm.get("roe_pct"),
                    "revenue_growth": growth.get("revenue"),
                    "operating_margin": margins.get("operating_margin_pct"),
                    "debt_ratio": fin_an.get("debt_to_equity_pct") if not fin_an.get("error") else None,
                },
                "updated_at": datetime.now().isoformat(),
            }
            # ⭐ 캐시 키 통일 — main.py 가 yf_ticker (.KS 포함) 로 검색하므로 둘 다 저장
            yf_t = w.get("ticker") or r["ticker"]   # 예: "005380.KS"
            code = r["ticker"]                       # 예: "005380"
            keys = list({yf_t, code})                # 중복 제거 (US 종목은 같음)

            for k in keys:
                cache_set(f"report:{k}", report_dict)

            # 클립보드 텍스트 사전 생성 → 양쪽 키 모두 저장
            try:
                clip_text = report_builder.build(
                    ticker=r["ticker"],
                    bundle=bundle,
                    company_name=r["name"],
                    top_k_news=3,
                    max_related=8,
                )
                for k in keys:
                    cache_set(f"clipboard:{k}", {"text": clip_text})
                log.info(f"  ✔ {r['name']} 캐시 저장 완료 (등급 {r['grade']}, 점수 {r['score']}, 클립보드 ✔)")
            except Exception as ex:
                log.warning(f"  ⚠ {r['name']} 클립보드 생성 실패 (다음 cron 재시도): {ex}")
                log.info(f"  ✔ {r['name']} 캐시 저장 완료 (등급 {r['grade']}, 점수 {r['score']})")
        except Exception as e:
            log.error(f"  ✗ {w['name']} 실패: {e}")

    if hot_results:
        cache_set(HOT_STOCKS_CACHE_KEY, hot_results)
        log.info(f"hot_stocks 캐시 {len(hot_results)}건 저장 완료")

    log.info("=" * 50)
    log.info("캐시 갱신 종료")
    log.info("=" * 50)


if __name__ == "__main__":
    main()
