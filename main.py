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
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import base64
from fastapi import FastAPI, HTTPException, UploadFile, File
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


try:
    from watchlist_builder import (
        build_watchlist as _build_wl,
        get_setting as _wl_get,
        set_setting as _wl_set,
        DEFAULT_CATEGORIES as _DEFAULT_CATS,
    )
    _WATCHLIST_BUILDER_AVAILABLE = True
except ImportError:
    _WATCHLIST_BUILDER_AVAILABLE = False


def build_watchlist() -> List[Dict]:
    """SQLite 의 watchlist_mode 설정에 따라 종목 리스트 생성.
    - volume          : 거래대금 상위 (기본)
    - news_categories : 카테고리별 핫 뉴스 → Gemini 추론
    - news_keywords   : 사용자 키워드 → Gemini 추론
    - hybrid          : 뉴스 + 거래량 혼합
    """
    if _WATCHLIST_BUILDER_AVAILABLE:
        try:
            items = _build_wl(
                db_path=DB_PATH,
                kr_limit=HOT_KR_LIMIT,
                us_limit=HOT_US_LIMIT,
                include_us=INCLUDE_US,
            )
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
                    "news_inference": s.get("news_inference"),
                })
            return out
        except Exception as e:
            log.warning(f"watchlist_builder failed, fallback to volume: {e}")

    # 폴백 — 기존 거래량 기반
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

# ── 서버 안전장치 (safety.py) ──
# 1) Gemini 호출에 분당 10회 / 일 250회 제한 자동 적용
# 2) cache_set 시 만료 행 주기적 청소
# 3) compute_with_cache 로 cache stampede 방지 (엔드포인트에서 사용)
from safety import (
    install_gemini_rate_limit,
    db_cleaner,
    keyed_lock,
    compute_with_cache,
    gemini_limiter,
)
install_gemini_rate_limit(gemini_filter, related_inferer)


# ─────────────────────────────────────────────
# 백그라운드 캐시 워머 — 사용자 첫 요청을 항상 즉시 응답되게
#   안전장치:
#   - 기본 비활성화 (수동 활성화 시에만 동작)
#   - 워커 수 제한 (메모리 폭주 방지)
#   - 워머 자체 인스턴스 1개만 (중복 실행 방지)
# ─────────────────────────────────────────────
import threading
import time as _time

CACHE_WARM_INTERVAL_SEC = int(os.getenv("CACHE_WARM_INTERVAL_SEC", "300"))   # 5분
CACHE_WARM_ENABLED = os.getenv("CACHE_WARM_ENABLED", "0") in ("1", "true", "True")
CACHE_WARM_PARALLEL = int(os.getenv("CACHE_WARM_PARALLEL", "3"))             # 워머는 3개만 (서버 부담 ↓)


def _warm_hot_stocks_cache():
    """hot_stocks 엔드포인트를 직접 호출해서 캐시 채우기."""
    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac
        watchlist = build_watchlist()
        results: Dict[str, Dict] = {}

        def _job(w):
            try:
                r = analyze_one(w["ticker"], w["code"], w["name"])
                return {
                    "ticker": r["ticker"], "name": r["name"], "price": r["price"],
                    "change_pct": r["change_pct"], "grade": r["grade"],
                    "score": r["score"], "summary": r["summary"],
                }
            except Exception as e:
                log.error(f"warmer failed for {w['ticker']}: {e}")
                return None

        # 워머는 더 적은 워커로 (서버 부담 최소화)
        with _TPE(max_workers=CACHE_WARM_PARALLEL) as ex:
            futs = {ex.submit(_job, w): w for w in watchlist}
            for f in _ac(futs):
                d = f.result()
                if d:
                    results[d["ticker"]] = d

        out = []
        for w in watchlist:
            tk = to_yf_ticker(w["code"]) if w.get("code", "").isdigit() and len(w["code"]) == 6 else w["code"]
            d = results.get(tk) or results.get(w["code"])
            if d:
                out.append(d)
        if out:
            cache_set(HOT_STOCKS_CACHE_KEY, out)
            log.info(f"[warmer] hot_stocks 캐시 갱신 완료 — {len(out)}건")
    except Exception as e:
        log.exception(f"[warmer] 실패: {e}")


def _warmer_loop():
    _time.sleep(8)   # 서버 부팅 직후 잠깐 대기
    while True:
        try:
            _warm_hot_stocks_cache()
        except Exception as e:
            log.error(f"[warmer] loop error: {e}")
        _time.sleep(CACHE_WARM_INTERVAL_SEC)


if CACHE_WARM_ENABLED:
    threading.Thread(target=_warmer_loop, daemon=True, name="cache-warmer").start()
    # 위 import 시점엔 HOT_STOCKS_PARALLEL/HOT_STOCKS_CACHE_KEY 가 아직 정의 안 됐을 수 있어
    # 실제 실행은 sleep 8 초 후 시작되므로 그때까진 모두 정의됨


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
    """-12 ~ +12 → 0 ~ 100 으로 변환."""
    return max(0, min(100, int((total_score + 12) / 24 * 100)))


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


class EtfInfo(BaseModel):
    """ETF 전용 지표. 일반 주식이면 None."""
    is_etf: bool = True
    market: Optional[str] = None               # "KR" / "US"
    fund_name: Optional[str] = None            # ETF 풀네임
    fund_family: Optional[str] = None          # 운용사
    category: Optional[str] = None             # 카테고리/테마
    total_assets_billion: Optional[float] = None  # 총운용자산 (억원 or USD B)
    expense_ratio_pct: Optional[float] = None  # 운용보수 %
    nav: Optional[float] = None                # 순자산가치 (KR)
    nav_diff_pct: Optional[float] = None       # 괴리율 % (KR)
    dividend_yield_pct: Optional[float] = None # 배당수익률
    return_1m: Optional[float] = None          # 1개월 수익률 %
    return_3m: Optional[float] = None          # 3개월 수익률 %
    return_ytd: Optional[float] = None         # YTD 수익률 %
    return_1y: Optional[float] = None          # 1년 수익률 %
    return_3y_ann: Optional[float] = None      # 3년 연평균 % (US)
    return_5y_ann: Optional[float] = None      # 5년 연평균 % (US)
    beta: Optional[float] = None               # 베타 (US)


class Financials(BaseModel):
    per: Optional[float] = None
    pbr: Optional[float] = None
    roe: Optional[float] = None
    revenue_growth: Optional[float] = None
    operating_margin: Optional[float] = None
    debt_ratio: Optional[float] = None
    # 계산 기준 (TTM / 연간 / YoY / 분기말)
    per_basis: Optional[str] = None
    pbr_basis: Optional[str] = None
    roe_basis: Optional[str] = None
    revenue_growth_basis: Optional[str] = None
    operating_margin_basis: Optional[str] = None
    debt_ratio_basis: Optional[str] = None
    # 값이 없을 때 이유 (앱 UI 표시용)
    per_na_reason: Optional[str] = None
    pbr_na_reason: Optional[str] = None
    roe_na_reason: Optional[str] = None
    revenue_growth_na_reason: Optional[str] = None
    operating_margin_na_reason: Optional[str] = None
    debt_ratio_na_reason: Optional[str] = None


class NewsItem(BaseModel):
    title: str
    link: Optional[str] = None
    pub_date: Optional[str] = None      # 원본 형식 (예: "Sat, 24 May 2026 11:30:00 +0900")
    impact: Optional[str] = None        # 긍정/부정/중립


class StockReport(BaseModel):
    ticker: str
    name: str
    grade: str
    score: int
    news_summary: str                   # legacy 호환용 — 줄바꿈 텍스트
    news_items: Optional[List[NewsItem]] = None   # 구조화된 뉴스 (앱이 우선 사용)
    financials: Financials
    etf_info: Optional[EtfInfo] = None  # ETF면 채워짐, 일반 주식이면 None
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
    etf_raw = bundle.get("etf_info")  # ETF면 Dict, 아니면 None

    # Gemini 호출은 실패해도 계속 진행
    try:
        picks = gemini_filter.filter_news(items, top_k=3) if items else []
        sent_score, sent_counts = gemini_filter.sentiment_score(picks)
    except Exception as e:
        log.warning(f"Gemini news filter failed for {ticker}: {e}")
        picks, sent_score, sent_counts = [], 0.0, {"긍정": 0, "부정": 0, "중립": 0}

    # ★ picks가 없거나 에러면 raw 뉴스 상위 3개를 중립으로 직접 표시
    if (not picks or picks[0].get("error")) and items:
        picks = [
            {
                "title": it.get("title", ""),
                "link": it.get("link"),
                "pub_date": it.get("pub_date"),
                "impact": "중립",
                "reason": "raw_fallback",
            }
            for it in items[:3]
            if it.get("title")
        ]
        sent_score, sent_counts = 0.0, {"긍정": 0, "부정": 0, "중립": len(picks)}

    _mm_grade = bundle.get("market_metrics") or {}
    if _mm_grade.get("error"):
        _mm_grade = {}
    verdict = grader.grade(price_an, fin_an, sent_score, sent_counts, is_etf=bool(etf_raw), market_metrics=_mm_grade)

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


# ─────────────────────────────────────────────
# 캐시 헬퍼 (report_cache 테이블 활용)
# ─────────────────────────────────────────────
HOT_STOCKS_CACHE_KEY = "__hot_stocks__"
HOT_STOCKS_CACHE_TTL_SEC = int(os.getenv("HOT_STOCKS_TTL_SEC", "300"))     # 5분
REPORT_CACHE_TTL_SEC = int(os.getenv("REPORT_CACHE_TTL_SEC", "600"))       # 10분
HOT_STOCKS_PARALLEL = int(os.getenv("HOT_STOCKS_PARALLEL", "8"))           # 동시 처리 워커 수


def cache_get(key: str, ttl_sec: Optional[int] = None) -> Optional[Dict]:
    """캐시 조회. ttl_sec=None 이면 만료 검사 없이 무조건 반환 (백그라운드 데몬이
    덮어쓰기 갱신하므로, reader 는 신선도 검사 없이 즉시 응답하는 게 정상 동작).
    ttl_sec 지정 시 그 시간 지나면 None 반환 (legacy 호환)."""
    try:
        with db_conn() as conn:
            row = conn.execute(
                "SELECT payload, updated_at FROM report_cache WHERE ticker = ?",
                (key,),
            ).fetchone()
        if not row:
            return None
        if ttl_sec is not None:
            updated = datetime.fromisoformat(row["updated_at"])
            if (datetime.now() - updated).total_seconds() > ttl_sec:
                return None
        return json.loads(row["payload"])
    except Exception as e:
        log.warning(f"cache_get({key}) failed: {e}")
        return None


def cache_set(key: str, payload: Dict) -> None:
    try:
        with db_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO report_cache(ticker, payload, updated_at) VALUES (?, ?, ?)",
                (key, json.dumps(payload, ensure_ascii=False, default=str), datetime.now().isoformat()),
            )
            conn.commit()
    except Exception as e:
        log.warning(f"cache_set({key}) failed: {e}")
    # 매 N번 set마다 만료 캐시 자동 청소 (DB 무한 증가 방지)
    db_cleaner.maybe_clean(DB_PATH, max(REPORT_CACHE_TTL_SEC, HOT_STOCKS_CACHE_TTL_SEC) * 6)


# ─────────────────────────────────────────────
# 진단 엔드포인트 — 서버 .env / API 키 상태 확인
# ─────────────────────────────────────────────
@app.get("/api/diagnostics")
def diagnostics():
    """서버에 .env 가 잘 박혔는지, 외부 API 가 다 살아있는지 한 방에 확인."""
    import requests

    def mask(s: Optional[str], keep: int = 4) -> str:
        if not s:
            return "(없음)"
        return s[:keep] + "***" + (s[-keep:] if len(s) > keep * 2 else "")

    gem = os.getenv("GEMINI_API_KEY")
    nv_id = os.getenv("NAVER_CLIENT_ID")
    nv_sec = os.getenv("NAVER_CLIENT_SECRET")
    dart = os.getenv("DART_API_KEY")

    results = {
        "env_loaded": {
            "GEMINI_API_KEY": mask(gem),
            "NAVER_CLIENT_ID": mask(nv_id),
            "NAVER_CLIENT_SECRET": mask(nv_sec),
            "DART_API_KEY": mask(dart),
            "GEMINI_MODEL": os.getenv("GEMINI_MODEL", "(default: gemini-2.5-flash)"),
        },
        "api_health": {},
    }

    # Gemini ping
    try:
        if gem:
            r = requests.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={gem}",
                timeout=8,
            )
            results["api_health"]["gemini"] = "OK" if r.status_code == 200 else f"FAIL ({r.status_code})"
        else:
            results["api_health"]["gemini"] = "NO KEY"
    except Exception as e:
        results["api_health"]["gemini"] = f"ERROR: {e}"

    # Naver ping
    try:
        if nv_id and nv_sec:
            r = requests.get(
                "https://openapi.naver.com/v1/search/news.json",
                headers={"X-Naver-Client-Id": nv_id, "X-Naver-Client-Secret": nv_sec},
                params={"query": "삼성전자", "display": 1},
                timeout=8,
            )
            results["api_health"]["naver"] = "OK" if r.status_code == 200 else f"FAIL ({r.status_code})"
        else:
            results["api_health"]["naver"] = "NO KEY"
    except Exception as e:
        results["api_health"]["naver"] = f"ERROR: {e}"

    # DART ping
    try:
        if dart:
            r = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={"crtfc_key": dart, "corp_code": "00126380",
                        "bgn_de": "20250101", "end_de": "20250131", "page_count": "1"},
                timeout=8,
            )
            st = r.json().get("status")
            results["api_health"]["dart"] = "OK" if st in ("000", "013") else f"FAIL ({st})"
        else:
            results["api_health"]["dart"] = "NO KEY"
    except Exception as e:
        results["api_health"]["dart"] = f"ERROR: {e}"

    results["cache_settings"] = {
        "hot_stocks_ttl_sec": HOT_STOCKS_CACHE_TTL_SEC,
        "report_ttl_sec": REPORT_CACHE_TTL_SEC,
        "parallel_workers": HOT_STOCKS_PARALLEL,
    }
    return results


# ─────────────────────────────────────────────
# 종목 선정 모드 설정 API
# ─────────────────────────────────────────────
class WatchlistConfig(BaseModel):
    mode: str  # "volume" | "news_categories" | "news_keywords" | "hybrid"
    keywords: Optional[List[str]] = None
    categories: Optional[List[str]] = None


@app.get("/api/watchlist/config")
def get_watchlist_config():
    if not _WATCHLIST_BUILDER_AVAILABLE:
        return {"mode": "news_hot", "available_modes": ["news_hot"], "error": "watchlist_builder not loaded"}
    mode = _wl_get(DB_PATH, "watchlist_mode", "news_hot")
    kw_raw = _wl_get(DB_PATH, "user_keywords", "[]")
    cats_raw = _wl_get(DB_PATH, "active_categories", json.dumps(list(_DEFAULT_CATS.keys())))
    try:
        keywords = json.loads(kw_raw)
    except Exception:
        keywords = []
    try:
        active_cats = json.loads(cats_raw)
    except Exception:
        active_cats = list(_DEFAULT_CATS.keys())
    return {
        "mode": mode,
        "available_modes": ["news_hot", "news_categories", "news_keywords"],
        "keywords": keywords,
        "active_categories": active_cats,
        "available_categories": list(_DEFAULT_CATS.keys()),
        "category_keywords": _DEFAULT_CATS,
    }


@app.post("/api/watchlist/config")
def set_watchlist_config(cfg: WatchlistConfig):
    if not _WATCHLIST_BUILDER_AVAILABLE:
        raise HTTPException(503, "watchlist_builder not loaded on server")
    valid_modes = {"news_hot", "news_categories", "news_keywords"}
    if cfg.mode not in valid_modes:
        raise HTTPException(400, f"mode must be one of {sorted(valid_modes)}")

    _wl_set(DB_PATH, "watchlist_mode", cfg.mode)
    if cfg.keywords is not None:
        _wl_set(DB_PATH, "user_keywords", json.dumps(cfg.keywords, ensure_ascii=False))
    if cfg.categories is not None:
        _wl_set(DB_PATH, "active_categories", json.dumps(cfg.categories, ensure_ascii=False))

    # 모드 바뀌면 캐시 무효화 → 다음 요청 시 새로 빌드
    try:
        with db_conn() as conn:
            conn.execute("DELETE FROM report_cache WHERE ticker = ?", (HOT_STOCKS_CACHE_KEY,))
            conn.commit()
    except Exception:
        pass

    return {"ok": True, "mode": cfg.mode, "saved_at": datetime.now().isoformat()}


@app.get("/api/hot-stocks", response_model=List[HotStock])
def hot_stocks(refresh: bool = False):
    """대시보드용 - 백그라운드 데몬 캐시 무조건 반환 (TTL 무시).
    refresh=True 인 경우만 즉석 재분석. + 병렬 8개 워커 + stampede 락."""
    # 캐시 확인 (refresh=True 면 강제 새로고침)
    if not refresh:
        cached = cache_get(HOT_STOCKS_CACHE_KEY)  # TTL 없음 - 있으면 무조건 반환
        if cached:
            log.info("hot-stocks: cache HIT")
            return [HotStock(**h) for h in cached]

    # ★ Cache stampede 방지 - 동일 키 동시 분석 차단
    _stampede_lock = keyed_lock.get(HOT_STOCKS_CACHE_KEY)
    with _stampede_lock:
        # 락 획득 후 다시 확인 (그 사이 다른 워커가 채웠을 수 있음)
        if not refresh:
            cached = cache_get(HOT_STOCKS_CACHE_KEY)
            if cached:
                log.info("hot-stocks: stampede 방지 - 다른 워커 결과 사용")
                return [HotStock(**h) for h in cached]

        log.info("hot-stocks: cache MISS → 병렬 분석 시작")
        watchlist = build_watchlist()

        out: List[HotStock] = []
        out_dicts: List[Dict] = []

        def _job(w):
            try:
                r = analyze_one(w["ticker"], w["code"], w["name"])
                return {
                    "ticker": r["ticker"], "name": r["name"], "price": r["price"],
                    "change_pct": r["change_pct"], "grade": r["grade"],
                    "score": r["score"], "summary": r["summary"],
                }
            except Exception as e:
                log.error(f"hot-stocks failed for {w['ticker']}: {e}")
                return {
                    "ticker": w["code"], "name": w["name"], "price": 0.0,
                    "change_pct": 0.0, "grade": "WATCH", "score": 50,
                    "summary": f"분석 실패: {str(e)[:60]}",
                }

        with ThreadPoolExecutor(max_workers=HOT_STOCKS_PARALLEL) as ex:
            futures = {ex.submit(_job, w): w for w in watchlist}
            results_by_ticker: Dict[str, Dict] = {}
            for f in as_completed(futures):
                res = f.result()
                results_by_ticker[res["ticker"]] = res

        # 원래 순서 유지
        for w in watchlist:
            key_ticker = to_yf_ticker(w["code"]) if w.get("code", "").isdigit() and len(w["code"]) == 6 else w["code"]
            d = results_by_ticker.get(key_ticker) or results_by_ticker.get(w["code"])
            if d:
                out_dicts.append(d)
                out.append(HotStock(**d))

        if out_dicts:
            cache_set(HOT_STOCKS_CACHE_KEY, out_dicts)
        log.info(f"hot-stocks: 분석 완료 {len(out)}건, 캐시 저장됨")
        return out


# === 아래는 사용 안 함 (위에서 새 hot_stocks 가 대체) ===
def _legacy_hot_stocks():
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
def stock_report(ticker: str, refresh: bool = False):
    """상세 페이지용 - 백그라운드 데몬 캐시 무조건 반환 (TTL 무시).
    캐시 있으면 데이터 신선도 관계 없이 즉시 응답 (updated_at 으로 신선도 표시).
    refresh=True 인 경우만 즉석 재분석. + stampede 락."""
    entry = find_watch_entry(ticker)
    if not entry:
        raise HTTPException(404, f"unknown ticker: {ticker}")

    if not refresh:
        cached = cache_get(f"report:{entry['ticker']}")  # TTL 없음
        if not cached:
            cached = cache_get(f"report:{entry['code']}")
        if cached:
            return StockReport(**cached)
    cache_key = f"report:{entry['ticker']}"

    # ★ Cache stampede 방지 - 같은 종목 동시 분석 차단 (캐시 미스 첫 1회 또는 refresh)
    with keyed_lock.get(cache_key):
        if not refresh:
            cached = cache_get(cache_key)
            if cached:
                log.info(f"stock_report: stampede 방지 - {cache_key}")
                return StockReport(**cached)

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

        # 뉴스 — 구조화 + 중복 제거 (제목 정규화 기준)
        news_items_out: List[NewsItem] = []
        if picks and not picks[0].get("error"):
            seen_titles = set()
            for p in picks:
                title = (p.get("title") or "").strip()
                if not title:
                    continue
                # 정규화 키: 공백/특수문자 제거 + 앞 60자
                norm = "".join(ch for ch in title if ch.isalnum())[:60].lower()
                if norm in seen_titles:
                    continue
                seen_titles.add(norm)
                news_items_out.append(NewsItem(
                    title=title,
                    link=p.get("link"),
                    pub_date=p.get("pub_date"),
                    impact=p.get("impact") or "중립",
                ))
            news_lines = [
                f"• [{ni.impact or '-'}] {ni.title}" for ni in news_items_out
            ]
            news_summary = "\n".join(news_lines) if news_lines else "최신 뉴스 부족"
        else:
            news_summary = "최신 뉴스 부족 또는 Gemini 키 미설정"

        # 재무 지표 추출 - 1순위 DART (한국), 2순위 yfinance.info (미국)
        margins = (fin_an.get("margins") or {}) if not fin_an.get("error") else {}
        growth = (fin_an.get("yoy_growth_pct") or {}) if not fin_an.get("error") else {}
        mm_safe = market_metrics if not market_metrics.get("error") else {}

        per = mm_safe.get("per")
        pbr = mm_safe.get("pbr")
        roe = mm_safe.get("roe_pct")

        # 매출 성장률 / 영업이익률 / 부채비율
        # ★ KR 종목: mm_safe(TTM/Naver/DART Q1)가 더 최신 → 1순위
        #   US 종목: margins(yfinance annual)이 정확 → 폴백 순서 같음
        revenue_growth = mm_safe.get("revenue_growth_pct")
        if revenue_growth is None:
            revenue_growth = growth.get("revenue")

        # 영업이익률: TTM(mm_safe) 우선, DART annual 폴백
        operating_margin = mm_safe.get("operating_margin_pct")
        if operating_margin is None:
            operating_margin = margins.get("operating_margin_pct")

        # 부채비율: DART Q1(mm_safe = 분기말 최신) 우선, DART annual 폴백
        debt_ratio = mm_safe.get("debt_to_equity_pct")
        if debt_ratio is None:
            debt_ratio = fin_an.get("debt_to_equity_pct") if not fin_an.get("error") else None

        # 부채비율 basis: DART 연간 → "분기말", yfinance → "분기말"
        debt_ratio_basis = "분기말"

        # ── 빈값 이유 계산 ──────────────────────────
        def _na_reason(val, field: str) -> Optional[str]:
            """값이 None일 때 짧은 이유 반환."""
            if val is not None:
                return None

            has_fin_error = bool(fin_an.get("error"))
            has_mm_error  = bool(market_metrics.get("error"))

            # 적자 판정: 확정된 값 기준
            is_loss = (
                (roe is not None and roe < 0)
                or (operating_margin is not None and operating_margin < 0)
            )
            # 자본잠식: PBR 계산 불가
            is_impaired = is_loss and pbr is None and not has_mm_error

            if field == "per":
                if is_loss:        return "적자"        # EPS 음수 → PER 불가
                if has_mm_error:   return "오류"
                return "미제공"

            if field == "pbr":
                if is_impaired:    return "자본잠식 의심"
                if has_mm_error:   return "오류"
                return "미제공"

            if field == "roe":
                if is_loss:        return "적자"        # TTM 순손실
                if has_fin_error:  return "오류"
                return "미제공"

            if field == "revenue_growth":
                # 신규 상장·분기보고서 미제출 등
                if has_fin_error:  return "오류"
                return "전기 비교불가"                  # 과거 1년치 없을 때

            if field == "operating_margin":
                if is_loss:        return "적자"
                if has_fin_error:  return "오류"
                return "미제공"

            if field == "debt_ratio":
                if has_fin_error:  return "오류"
                return "미제공"

            return "미제공"

        financials = Financials(
            per=per,
            pbr=pbr,
            roe=roe,
            revenue_growth=revenue_growth,
            operating_margin=operating_margin,
            debt_ratio=debt_ratio,
            per_basis=mm_safe.get("per_basis"),
            pbr_basis=mm_safe.get("pbr_basis", "분기말"),
            roe_basis=mm_safe.get("roe_basis"),
            revenue_growth_basis=mm_safe.get("revenue_growth_basis", "YoY"),
            operating_margin_basis=mm_safe.get("operating_margin_basis"),
            debt_ratio_basis=debt_ratio_basis,
            per_na_reason=_na_reason(per, "per"),
            pbr_na_reason=_na_reason(pbr, "pbr"),
            roe_na_reason=_na_reason(roe, "roe"),
            revenue_growth_na_reason=_na_reason(revenue_growth, "revenue_growth"),
            operating_margin_na_reason=_na_reason(operating_margin, "operating_margin"),
            debt_ratio_na_reason=_na_reason(debt_ratio, "debt_ratio"),
        )

        # ETF 정보 모델 변환
        etf_raw = r.get("_internals", {}).get("bundle", {}).get("etf_info")
        etf_info_model = EtfInfo(**etf_raw) if etf_raw else None

        report = StockReport(
            ticker=r["ticker"],
            name=r["name"],
            grade=r["grade"],
            score=r["score"],
            news_summary=news_summary,
            news_items=news_items_out or None,
            financials=financials,
            etf_info=etf_info_model,
            updated_at=datetime.now().isoformat(),
        )
        cache_set(cache_key, report.model_dump())
        return report


def _build_beginner_prompt(report_text: str, name: str, ticker: str) -> str:
    """리포트 원문을 초보 투자자용 Claude 질문 프롬프트로 감쌈."""
    return f"""당신은 친절한 주식 투자 선생님입니다.
주식을 처음 접하는 초보 투자자가 쉽게 이해할 수 있도록 아래 종목 리포트를 분석해주세요.
어려운 금융 용어는 반드시 쉬운 말로 풀어서 설명하고, 숫자가 좋은지 나쁜지 꼭 판단해주세요.

[분석 종목] {name} ({ticker})

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

아래 리포트를 보고 다음 3가지를 순서대로 설명해주세요:

【1】 📰 뉴스 해설
뉴스가 있다면 각각 아래 형식으로 설명해주세요:
  ① 이 뉴스가 무슨 일인지 쉬운 말로 2~3줄 설명
  ② 이 뉴스가 이 회사 주가에 좋은 소식인지 나쁜 소식인지, 그 이유

【2】 📊 재무 수치 쉬운 설명
아래 수치들이 리포트에 있다면 각각 설명해주세요 (없으면 "데이터 없음" 표시):

  ▸ PER (주가수익비율)
    - 이게 뭔지: 예) "현재 주가가 1년 순이익의 몇 배냐를 나타냄"
    - 현재 수치가 높은지 낮은지, 왜 그게 좋은지/나쁜지
    - 이 종목 PER은 어떤 수준인지

  ▸ PBR (주가순자산비율)
    - 이게 뭔지: 예) "회사 장부상 가치 대비 주가가 몇 배냐"
    - 현재 수치의 의미

  ▸ ROE (자기자본이익률)
    - 이게 뭔지: 예) "회사가 주주 돈으로 얼마나 이익을 냈는지"
    - 현재 수치가 괜찮은 수준인지

  ▸ 매출 성장률
    - 이게 뭔지
    - 현재 방향이 좋은지 나쁜지

  ▸ 영업이익률
    - 이게 뭔지: 예) "물건을 팔아서 실제로 얼마나 남기는지"
    - 이 회사가 돈을 잘 버는 편인지

  ▸ 부채비율
    - 이게 뭔지
    - 지금 수치가 위험한 수준인지, 업종 특성도 감안해서

【3】 💡 초보자를 위한 최종 의견
  - 지금 이 종목, 어떻게 판단하나요? (매수 유망 / 관망 / 위험 중 하나 + 이유)
  - 이 회사에서 가장 기대되는 점 딱 1가지
  - 꼭 조심해야 할 점 딱 1가지
  - 한 줄 요약 (20자 이내)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[📋 리포트 원문]
{report_text}
"""


@app.get("/api/stocks/{ticker}/clipboard", response_model=ClipboardText)
def stock_clipboard(ticker: str, refresh: bool = False):
    """Claude 웹에 그대로 붙여넣을 텍스트. 백그라운드 캐시 무조건 반환 (TTL 무시).
    refresh=True 인 경우만 즉석 재생성. + stampede 락."""
    entry = find_watch_entry(ticker)
    if not entry:
        raise HTTPException(404, f"unknown ticker: {ticker}")

    if not refresh:
        # yf_ticker 키 우선, 없으면 code 키로 폴백 - TTL 없음
        cached = cache_get(f"clipboard:{entry['ticker']}")
        if not cached or not cached.get("text"):
            cached = cache_get(f"clipboard:{entry['code']}")
        if cached and cached.get("text"):
            return ClipboardText(text=cached["text"])
    cache_key = f"clipboard:{entry['ticker']}"

    # ★ Cache stampede 방지 (캐시 미스 첫 1회 또는 refresh)
    with keyed_lock.get(cache_key):
        if not refresh:
            cached = cache_get(cache_key)
            if cached and cached.get("text"):
                log.info(f"stock_clipboard: stampede 방지 - {cache_key}")
                return ClipboardText(text=cached["text"])

        try:
            bundle = collector.collect_all(
                ticker=entry["ticker"],
                news_query=entry["name"],
                stock_code=entry["code"],
            )
            report_text = report_builder.build(
                ticker=entry["ticker"],
                bundle=bundle,
                company_name=entry["name"],
                top_k_news=3,
                max_related=8,
            )
            text = _build_beginner_prompt(report_text, entry["name"], entry["ticker"])
            cache_set(cache_key, {"text": text})
            return ClipboardText(text=text)
        except Exception as e:
            log.exception(f"clipboard failed for {ticker}")
            raise HTTPException(500, f"clipboard build failed: {e}")


# ─────────────────────────────────────────────
# 안전장치 진단 엔드포인트
# ─────────────────────────────────────────────
@app.get("/api/safety/stats")
def safety_stats():
    """rate limiter / DB cleaner / keyed lock 현재 상태."""
    return {
        "gemini_limiter": gemini_limiter.stats(),
        "db_cleaner": db_cleaner.stats(),
        "keyed_lock": keyed_lock.stats(),
        "cache_settings": {
            "hot_stocks_ttl_sec": HOT_STOCKS_CACHE_TTL_SEC,
            "report_ttl_sec": REPORT_CACHE_TTL_SEC,
        },
    }


@app.post("/api/safety/cleanup-cache")
def safety_cleanup_cache():
    """report_cache 만료 행 즉시 청소 (관리자용)."""
    deleted = db_cleaner.force_clean(
        DB_PATH,
        max(REPORT_CACHE_TTL_SEC, HOT_STOCKS_CACHE_TTL_SEC) * 6,
    )
    return {"deleted": deleted, "ok": True}


# ─────────────────────────────────────────────
# 자동매매 엔드포인트
# ─────────────────────────────────────────────
try:
    import auto_trader
    _AUTO_TRADER_AVAILABLE = True
except ImportError:
    _AUTO_TRADER_AVAILABLE = False
    log.warning("auto_trader 모듈 없음 — 자동매매 비활성화")


class AutoTradeStartRequest(BaseModel):
    conditions: dict
    trade_mode: str = "paper"  # "real" | "paper"  기본값=모의투자


@app.post("/api/auto-trade/analyze-image")
async def auto_trade_analyze_image(file: UploadFile = File(...)):
    """
    이미지 업로드 → Gemini Vision으로 매수/매도 조건 추출.
    반환: { summary, buy_conditions, sell_conditions, check_interval_minutes }
    """
    if not _AUTO_TRADER_AVAILABLE:
        raise HTTPException(503, "auto_trader 모듈 없음")
    try:
        image_bytes = await file.read()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        mime = file.content_type or "image/jpeg"
        conditions = auto_trader.analyze_image_conditions(image_b64, mime)
        auto_trader.set_conditions_image_text(conditions.get("summary", ""))
        # 이중 검증 (실패해도 분석 결과는 반환)
        verification = {}
        try:
            verification = auto_trader.verify_conditions_image(image_b64, mime, conditions)
        except Exception as ve:
            log.warning(f"검증 단계 실패 (무시): {ve}")
        return {"ok": True, "conditions": conditions, "verification": verification}
    except Exception as e:
        log.exception("이미지 분석 실패")
        raise HTTPException(500, f"이미지 분석 실패: {e}")


@app.post("/api/auto-trade/analyze-text")
async def auto_trade_analyze_text(req: dict):
    """
    텍스트 복붙 → Gemini/Groq로 매수/매도 조건 추출.
    Body: { "text": "전략 텍스트 내용" }
    반환: { ok, conditions }
    """
    if not _AUTO_TRADER_AVAILABLE:
        raise HTTPException(503, "auto_trader 모듈 없음")
    text = (req.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "text 필드가 비어있습니다.")
    try:
        conditions = auto_trader.analyze_text_conditions(text)
        auto_trader.set_conditions_image_text(conditions.get("summary", ""))
        verification = {}
        try:
            verification = auto_trader.verify_conditions_text(text, conditions)
        except Exception as ve:
            log.warning(f"검증 단계 실패 (무시): {ve}")
        return {"ok": True, "conditions": conditions, "verification": verification}
    except Exception as e:
        log.exception("텍스트 분석 실패")
        raise HTTPException(500, f"텍스트 분석 실패: {e}")


@app.post("/api/auto-trade/start")
def auto_trade_start(req: AutoTradeStartRequest):
    """조건을 받아 자동매매 루프 시작."""
    if not _AUTO_TRADER_AVAILABLE:
        raise HTTPException(503, "auto_trader 모듈 없음")
    started = auto_trader.start_trading(req.conditions, trade_mode=req.trade_mode)
    if not started:
        raise HTTPException(409, "이미 실행 중입니다")
    mode_label = "모의투자" if req.trade_mode == "paper" else "실전투자"
    return {"ok": True, "message": f"자동매매 시작 ({mode_label})"}


@app.post("/api/auto-trade/stop")
def auto_trade_stop():
    """자동매매 루프 정지."""
    if not _AUTO_TRADER_AVAILABLE:
        raise HTTPException(503, "auto_trader 모듈 없음")
    stopped = auto_trader.stop_trading()
    return {"ok": True, "stopped": stopped}


@app.get("/api/auto-trade/status")
def auto_trade_status():
    if not _AUTO_TRADER_AVAILABLE:
        return {"available": False}
    return {"available": True, **auto_trader.get_status()}


class PhaseStartRequest(BaseModel):
    strategy: dict
    trade_mode: str = "paper"
    resume: bool = False

@app.post("/api/auto-trade/start-phase")
def auto_trade_start_phase(req: PhaseStartRequest):
    if not _AUTO_TRADER_AVAILABLE:
        raise HTTPException(503, "auto_trader 모듈 없음")
    started = auto_trader.start_trading_phase(req.strategy, trade_mode=req.trade_mode, resume=req.resume)
    return {"ok": True, "started": started}


# ── 템플릿 API ────────────────────────────────
try:
    import strategy_templates as _templates
    _TEMPLATES_AVAILABLE = True
except ImportError:
    _TEMPLATES_AVAILABLE = False

@app.get("/api/templates")
def list_templates():
    if not _TEMPLATES_AVAILABLE:
        raise HTTPException(503, "strategy_templates 모듈 없음")
    names = _templates.list_templates()
    result = []
    for name in names:
        try:
            t = _templates.get_template(name)
            result.append({
                "name": name,
                "description": t.get("description", ""),
                "ticker": t.get("ticker", ""),
                "builtin": _templates.is_builtin(name),
                "phases": t.get("phases", {}),
            })
        except Exception:
            pass
    return result

@app.get("/api/templates/{name}")
def get_template(name: str):
    if not _TEMPLATES_AVAILABLE:
        raise HTTPException(503, "strategy_templates 모듈 없음")
    try:
        return _templates.get_template(name)
    except KeyError:
        raise HTTPException(404, f"템플릿 없음: {name}")

class SaveTemplateRequest(BaseModel):
    name: str
    strategy: dict

@app.post("/api/templates")
def save_template(req: SaveTemplateRequest):
    if not _TEMPLATES_AVAILABLE:
        raise HTTPException(503, "strategy_templates 모듈 없음")
    try:
        _templates.save_template(req.name, req.strategy)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/templates/{name}")
def delete_template(name: str):
    if not _TEMPLATES_AVAILABLE:
        raise HTTPException(503, "strategy_templates 모듈 없음")
    try:
        _templates.delete_template(name)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, f"템플릿 없음: {name}")


# ── 조건 초기화 / 수정 API ──────────────────────
@app.post("/api/auto-trade/reset-conditions")
def auto_trade_reset_conditions():
    if not _AUTO_TRADER_AVAILABLE:
        raise HTTPException(503, "auto_trader 모듈 없음")
    auto_trader.reset_conditions()
    return {"ok": True}


class FixConditionsRequest(BaseModel):
    existing: dict
    fix_text: str

@app.post("/api/auto-trade/fix-conditions")
async def auto_trade_fix_conditions(req: FixConditionsRequest):
    try:
        import groq as _groq
        import json as _json
        import re as _re
        client = _groq.Groq()
        prompt = f"""아래는 현재 자동매매 앱에 설정된 조건 JSON입니다.
사용자의 수정 요청에 따라 조건을 수정하고, 수정된 전체 JSON을 반환하세요.
반드시 동일한 JSON 구조를 유지하세요.

현재 조건:
{_json.dumps(req.existing, ensure_ascii=False, indent=2)}

수정 요청:
{req.fix_text}

수정된 JSON만 반환하세요 (마크다운 없이):"""
        resp = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=4096,
        )
        raw = resp.choices[0].message.content.strip()
        m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if m:
            raw = m.group(0)
        fixed = _json.loads(raw)
        return fixed
    except Exception as e:
        raise HTTPException(500, f"수정 실패: {e}")


