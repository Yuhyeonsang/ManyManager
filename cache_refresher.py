"""
백그라운드 캐시 갱신 워커
cron 으로 정기 실행 → 모든 종목을 미리 분석해서 SQLite 캐시에 저장
사용자가 앱 켜면 캐시된 결과를 즉시 받아옴

사용법:
  python cache_refresher.py        # 1회 실행

cron 등록 예 (30분마다):
  */30 * * * * /home/ubuntu/ManyManager/.venv/bin/python /home/ubuntu/ManyManager/cache_refresher.py >> /home/ubuntu/cache_refresher.log 2>&1
"""
import os
import sys
import json
import time
import logging
from datetime import datetime

# Gemini 2.5 Flash 무료 티어 RPM(분당 호출수) 보호
# 종목당 ~2회 Gemini 호출 → 분당 10회 한도 안에서 안전한 간격
GEMINI_RPM_SLEEP_SEC = int(os.getenv("GEMINI_RPM_SLEEP_SEC", "12"))

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


def main():
    log.info("=" * 50)
    log.info("캐시 갱신 시작")
    log.info("=" * 50)

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

            if picks and not picks[0].get("error"):
                news_summary = "\n".join(f"• [{p.get('impact','-')}] {p.get('title','')}" for p in picks)
            else:
                news_summary = "최신 뉴스 부족"

            report_dict = {
                "ticker": r["ticker"],
                "name": r["name"],
                "grade": r["grade"],
                "score": r["score"],
                "news_summary": news_summary,
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
            cache_set(f"report:{r['ticker']}", report_dict)

            # 클립보드 텍스트 사전 생성 → 캐시 (사용자가 누르면 즉시 응답)
            try:
                clip_text = report_builder.build(
                    ticker=r["ticker"],
                    bundle=bundle,
                    company_name=r["name"],
                    top_k_news=3,
                    max_related=8,
                )
                cache_set(f"clipboard:{r['ticker']}", {"text": clip_text})
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
