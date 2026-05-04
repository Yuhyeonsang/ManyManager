"""
FastAPI 서버 - 모바일 앱(fund-manager-app)이 호출하는 REST API
실행:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

엔드포인트:
    GET /api/hot-stocks                       핫 종목 리스트
    GET /api/stocks/{ticker}/report           상세 분석 리포트 (JSON)
    GET /api/stocks/{ticker}/clipboard        클립보드용 텍스트
    GET /                                     헬스체크
"""

import os
import sqlite3
import logging
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# .env 자동 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# 기존 모듈 (analyzer.py, data_collector.py)
from data_collector import (
    StockDataCollector,
    KR_STOCK_UNIVERSE,
    US_STOCK_UNIVERSE,
    search_stocks,
    get_hot_stocks_kr,
    get_hot_stocks_us,
    get_hot_stocks_mixed,
    to_yf_ticker,
)
from analyzer import (
    GeminiNewsFilter,
    RelatedStockInferer,
    SemanticLayer,
    InvestmentGrader,
    ReportBuilder,
)

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
DB_PATH = os.getenv("FUND_DB", "fund_manager.db")

# 대시보드 기본값 (KRX 호출 실패 등 fallback 용도)
DEFAULT_WATCHLIST = [
    {"ticker": "005930.KS", "code": "005930", "name": "삼성전자"},
    {"ticker": "000660.KS", "code": "000660", "name": "SK하이닉스"},
    {"ticker": "035420.KS", "code": "035420", "name": "NAVER"},
    {"ticker": "035720.KS", "code": "035720", "name": "카카오"},
    {"ticker": "005380.KS", "code": "005380", "name": "현대차"},
    {"ticker": "207940.KS", "code": "207940", "name": "삼성바이오로직스"},
    {"ticker": "068270.KS", "code": "068270", "name": "셀트리온"},
    {"ticker": "373220.KS", "code": "373220", "name": "LG에너지솔루션"},
]

# 환경변수로 켤 수 있는 옵션
INCLUDE_US = os.getenv("INCLUDE_US", "1") not in ("0", "false", "False")
HOT_KR_LIMIT = int(os.getenv("HOT_KR_LIMIT", "6"))
HOT_US_LIMIT = int(os.getenv("HOT_US_LIMIT", "4"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("fund-api")


def build_watchlist() -> List[Dict]:
    """매 호출마다 동적으로 핫 종목 리스트를 만든다."""
    items: List[Dict] = []
    try:
        kr = get_hot_stocks_kr(limit=HOT_KR_LIMIT)
    except Exception as e:
        log.warning(f"get_hot_stocks_kr failed: {e}")
        kr = []
    if not kr:
        kr = [
            {"code": w["code"], "name": w["name"], "ticker": w["ticker"], "region": "KR", "market": "KOSPI"}
            for w in DEFAULT_WATCHLIST
        ]

    items.extend(kr[:HOT_KR_LIMIT])

    if INCLUDE_US:
        try:
            us = get_hot_stocks_us(limit=HOT_US_LIMIT)
        except Exception as e:
            log.warning(f"get_hot_stocks_us failed: {e}")
            us = []
        items.extend(us[:HOT_US_LIMIT])

    # 정규화
    out = []
    for s in items:
        code = s.get("code") or s.get("ticker", "")
        ticker = s.get("ticker") or to_yf_ticker(code)
        out.append({
            "code": code,
            "name": s.get("name") or code,
            "ticker": ticker,
            "region": s.get("region") or ("KR" if code.isdigit() and len(code) == 6 else "US"),
            "market": s.get("market"),
        })
    return out

# ─────────────────────────────────────────────
# FastAPI 앱
# ─────────────────────────────────────────────
app = FastAPI(title="FundManager API", version="1.0.0")

# 모바일 앱이 다른 IP에서 접속하므로 CORS 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 공유 인스턴스 (한 번만 생성)
collector = StockDataCollector()
gemini_filter = GeminiNewsFilter()
related_inferer = RelatedStockInferer()
grader = InvestmentGrader()
sem = SemanticLayer()
report_builder = ReportBuilder(gemini_filter, related_inferer, grader)


# ─────────────────────────────────────────────
# 등급 매핑 (한글 → 앱이 쓰는 영문)
# ─────────────────────────────────────────────
GRADE_MAP = {
    "적극 매수": "STRONG_BUY",
    "매수": "BUY",
    "보유": "HOLD",
    "관망": "WATCH",
    "비중 축소": "SELL",
}


def map_grade(grade_kr: str) -> str:
    return GRADE_MAP.get(grade_kr, "HOLD")


def score_to_100(total_score: int) -> int:
    """-6 ~ +6 → 0 ~ 100 으로 변환."""
    return max(0, min(100, int((total_score + 6) / 12 * 100)))


# ─────────────────────────────────────────────
# SQLite 캐시 (선택적, monitor_loop.py 와 같은 DB 사용)
# ─────────────────────────────────────────────
def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS report_cache (
                ticker TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        conn.commit()


init_db()


# ─────────────────────────────────────────────
# 응답 스키마
# ─────────────────────────────────────────────
class HotStock(BaseModel):
    ticker: str
    name: str
    price: float
    change_pct: float
    grade: str
    score: int
    summary: str


class Financials(BaseModel):
    per: Optional[float] = None
    pbr: Optional[float] = None
    roe: Optional[float] = None
    revenue_growth: Optional[float] = None
    operating_margin: Optional[float] = None
    debt_ratio: Optional[float] = None


class StockReport(BaseModel):
    ticker: str
    name: str
    grade: str
    score: int
    news_summary: str
    financials: Financials
    updated_at: str


class ClipboardText(BaseModel):
    text: str


# ─────────────────────────────────────────────
# 핵심 분석 함수
# ─────────────────────────────────────────────
def analyze_one(ticker: str, code: str, name: str) -> Dict:
    """한 종목을 분석해서 등급/점수/요약 반환."""
    bundle = collector.collect_all(
        ticker=ticker,
        news_query=name,
        stock_code=code,
    )

    price = bundle.get("price") or {}
    news = bundle.get("news") or {}
    fin = bundle.get("financials") or {}
    items = news.get("items", []) if news else []

    price_an = sem.analyze_price(price)
    fin_an = sem.analyze_financials(fin)

    # Gemini 호출은 실패해도 계속 진행
    try:
        picks = gemini_filter.filter_news(items, top_k=3) if items else []
        sent_score, sent_counts = gemini_filter.sentiment_score(picks)
    except Exception as e:
        log.warning(f"Gemini news filter failed for {ticker}: {e}")
        picks, sent_score, sent_counts = [], 0.0, {"긍정": 0, "부정": 0, "중립": 0}

    verdict = grader.grade(price_an, fin_an, sent_score, sent_counts)

    # 한 줄 요약
    if picks and not picks[0].get("error"):
        summary = picks[0].get("title", "")[:60]
    else:
        signals = price_an.get("signals", []) if not price_an.get("error") else []
        summary = signals[0] if signals else "최근 데이터 분석 완료"

    return {
        "ticker": code,  # 앱은 6자리 코드를 쓰는 것으로 가정
        "yf_ticker": ticker,
        "name": name,
        "price": price_an.get("current_price", 0) if not price_an.get("error") else 0,
        "change_pct": price_an.get("change_pct", 0) if not price_an.get("error") else 0,
        "grade": map_grade(verdict["grade"]),
        "grade_kr": verdict["grade"],
        "score": score_to_100(verdict["total_score"]),
        "summary": summary,
        "_internals": {
            "price_an": price_an,
            "fin_an": fin_an,
            "picks": picks,
            "sent_score": sent_score,
            "sent_counts": sent_counts,
            "verdict": verdict,
            "bundle": bundle,
        },
    }


def find_watch_entry(ticker_or_code: str) -> Optional[Dict]:
    """앱에서 6자리 코드(005930), yfinance 형식(005930.KS), 미국 티커(AAPL) 모두 지원."""
    t = ticker_or_code.strip().upper()
    # 1) 국장 마스터에서
    for s in KR_STOCK_UNIVERSE:
        if t == s["code"] or t == s["code"] + ".KS" or t == s["code"] + ".KQ":
            return {"ticker": to_yf_ticker(s["code"]), "code": s["code"], "name": s["name"], "region": "KR"}
    # 2) 미국 마스터에서
    for s in US_STOCK_UNIVERSE:
        if t == s["code"]:
            return {"ticker": s["code"], "code": s["code"], "name": s["name"], "region": "US"}
    # 3) 모르는 6자리 → 국장으로 추정
    if t.isdigit() and len(t) == 6:
        return {"ticker": f"{t}.KS", "code": t, "name": t, "region": "KR"}
    # 4) 알파벳만 있으면 미국 티커로 추정
    if t.isalpha() and 1 <= len(t) <= 5:
        return {"ticker": t, "code": t, "name": t, "region": "US"}
    # 5) 점이 들어있으면 그대로 yfinance 티커로
    if "." in t:
        code = t.split(".")[0]
        return {"ticker": t, "code": code, "name": code, "region": "KR"}
    return None


# ─────────────────────────────────────────────
# 엔드포인트
# ─────────────────────────────────────────────
@app.get("/")
def health():
    return {
        "ok": True,
        "service": "FundManager API",
        "time": datetime.now().isoformat(),
        "kr_universe_size": len(KR_STOCK_UNIVERSE),
        "us_universe_size": len(US_STOCK_UNIVERSE),
        "include_us": INCLUDE_US,
    }


@app.get("/api/hot-stocks", response_model=List[HotStock])
def hot_stocks():
    """대시보드용 - 매일 동적으로 핫 종목 리스트를 만들어서 분석."""
    watchlist = build_watchlist()
    out: List[HotStock] = []
    for w in watchlist:
        try:
            r = analyze_one(w["ticker"], w["code"], w["name"])
            out.append(HotStock(
                ticker=r["ticker"],
                name=r["name"],
                price=r["price"],
                change_pct=r["change_pct"],
                grade=r["grade"],
                score=r["score"],
                summary=r["summary"],
            ))
        except Exception as e:
            log.error(f"hot-stocks failed for {w['ticker']}: {e}")
            out.append(HotStock(
                ticker=w["code"],
                name=w["name"],
                price=0, change_pct=0,
                grade="HOLD", score=50,
                summary="분석 일시 오류",
            ))
    out.sort(key=lambda x: x.score, reverse=True)
    return out


# ─────────────────────────────────────────────
# 검색 엔드포인트
# ─────────────────────────────────────────────
class SearchHit(BaseModel):
    code: str
    name: str
    market: Optional[str] = None
    ticker: str
    region: str


@app.get("/api/search", response_model=List[SearchHit])
def api_search(q: str, limit: int = 20):
    """국장 + 미국 통합 종목 검색. q 는 회사명/종목코드 부분일치."""
    if not q or len(q.strip()) < 1:
        return []
    hits = search_stocks(q.strip(), limit=limit)
    return [SearchHit(**h) for h in hits]


@app.get("/api/stocks/{ticker}/report", response_model=StockReport)
def stock_report(ticker: str):
    """상세 페이지용 - 종목 1개의 풀 리포트."""
    entry = find_watch_entry(ticker)
    if not entry:
        raise HTTPException(404, f"unknown ticker: {ticker}")

    try:
        r = analyze_one(entry["ticker"], entry["code"], entry["name"])
    except Exception as e:
        log.exception(f"report failed for {ticker}")
        raise HTTPException(500, f"analysis failed: {e}")

    intr = r["_internals"]
    fin_an = intr["fin_an"]
    picks = intr["picks"]
    bundle = intr["bundle"]
    market_metrics = (bundle or {}).get("market_metrics") or {}

    # 뉴스 요약 텍스트
    if picks and not picks[0].get("error"):
        news_lines = [f"• [{p.get('impact','-')}] {p.get('title','')}" for p in picks]
        news_summary = "\n".join(news_lines)
    else:
        news_summary = "최신 뉴스 부족 또는 Gemini 키 미설정"

    # 재무 지표 추출
    margins = (fin_an.get("margins") or {}) if not fin_an.get("error") else {}
    growth = (fin_an.get("yoy_growth_pct") or {}) if not fin_an.get("error") else {}

    # PER/PBR/ROE - 1순위: DART 기반 계산값(없음), 2순위: yfinance.info
    per = None if market_metrics.get("error") else market_metrics.get("per")
    pbr = None if market_metrics.get("error") else market_metrics.get("pbr")
    roe = None if market_metrics.get("error") else market_metrics.get("roe_pct")

    financials = Financials(
        per=per,
        pbr=pbr,
        roe=roe,
        revenue_growth=growth.get("revenue"),
        operating_margin=margins.get("operating_margin_pct"),
        debt_ratio=fin_an.get("debt_to_equity_pct") if not fin_an.get("error") else None,
    )

    return StockReport(
        ticker=r["ticker"],
        name=r["name"],
        grade=r["grade"],
        score=r["score"],
        news_summary=news_summary,
        financials=financials,
        updated_at=datetime.now().isoformat(),
    )


@app.get("/api/stocks/{ticker}/clipboard", response_model=ClipboardText)
def stock_clipboard(ticker: str):
    """Claude 웹에 그대로 붙여넣을 텍스트."""
    entry = find_watch_entry(ticker)
    if not entry:
        raise HTTPException(404, f"unknown ticker: {ticker}")

    try:
        bundle = collector.collect_all(
            ticker=entry["ticker"],
            news_query=entry["name"],
            stock_code=entry["code"],
        )
        text = report_builder.build(
            ticker=entry["ticker"],
            bundle=bundle,
            company_name=entry["name"],
            top_k_news=3,
            max_related=8,
        )
        return ClipboardText(text=text)
    except Exception as e:
        log.exception(f"clipboard failed for {ticker}")
        raise HTTPException(500, f"clipboard build failed: {e}")


# ─────────────────────────────────────────────
# 직접 실행 (python main.py)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
