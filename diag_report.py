"""
텍스트 리포트 내부 진단 — KR/US/ETF 각각 데이터 수집 + 리포트 빌드 점검.
실행: cd ~/ManyManager && ./venv/bin/python diag_report.py
"""
import time
import logging
logging.basicConfig(level=logging.WARNING)

from data_collector import StockDataCollector
from analyzer import (
    GeminiNewsFilter, RelatedStockInferer, InvestmentGrader, ReportBuilder,
)

col = StockDataCollector()
rb = ReportBuilder(GeminiNewsFilter(), RelatedStockInferer(), InvestmentGrader())

TESTS = [
    ("KR 대형주 삼성전자", "005930.KS", "삼성전자", "005930"),
    ("US Apple",          "AAPL",      "Apple",   "AAPL"),
    ("KR ETF KODEX200",   "069500.KS", "KODEX 200", "069500"),
]

for label, ticker, name, code in TESTS:
    print("=" * 60)
    print(f"[{label}] ticker={ticker} code={code}")
    try:
        t0 = time.time()
        b = col.collect_all(ticker=ticker, news_query=name, stock_code=code)
        t_collect = time.time() - t0
    except Exception as e:
        print("  collect_all 예외:", repr(e))
        continue

    price = b.get("price") or {}
    mm = b.get("market_metrics") or {}
    fin = b.get("financials") or {}
    news = b.get("news") or {}
    ma = (price.get("moving_averages") or {})
    print(f"  수집 {t_collect:.1f}s")
    print(f"  price: current={price.get('current_price')} MA20={ma.get('MA20')} "
          f"recent10d={len(price.get('recent_10d') or [])} err={price.get('error')}")
    print(f"  market_metrics: PER={mm.get('per')} PBR={mm.get('pbr')} ROE={mm.get('roe_pct')} "
          f"opmargin={mm.get('operating_margin_pct')} err={mm.get('error')}")
    print(f"  financials: err={fin.get('error')} ratios={fin.get('ratios')} "
          f"has_indicators={bool(fin.get('indicators'))}")
    print(f"  news: count={len(news.get('items') or [])} err={news.get('error')}")
    print(f"  etf_info: {'있음' if b.get('etf_info') else '없음'}")

    try:
        t1 = time.time()
        txt = rb.build(ticker=ticker, bundle=b, company_name=name,
                       top_k_news=3, max_related=8)
        t_build = time.time() - t1
        print(f"  리포트 빌드 {t_build:.1f}s | 글자수 {len(txt)}")
        print("  ── 리포트 본문 전체 ──")
        print(txt)
    except Exception as e:
        print("  report build 예외:", repr(e))
    print()
