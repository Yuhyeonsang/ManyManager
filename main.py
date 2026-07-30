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
    KR_ETF_UNIVERSE,
    US_STOCK_UNIVERSE,
    search_stocks,
    get_hot_stocks_kr,
    get_hot_stocks_us,
    get_hot_stocks_mixed,
    to_yf_ticker,
    get_kr_etf_info_list,
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
        build_news_inferred as _build_news_inferred,
        US_MARKET_QUERIES as _US_QUERIES,
        get_setting as _wl_get,
        set_setting as _wl_set,
        DEFAULT_CATEGORIES as _DEFAULT_CATS,
    )
    _WATCHLIST_BUILDER_AVAILABLE = True
except ImportError:
    _WATCHLIST_BUILDER_AVAILABLE = False


def build_watchlist() -> List[Dict]:
    """뉴스 추론 1순위 → 부족분은 거래대금 상위 → 대형주 순으로 채워
    KR HOT_KR_LIMIT + US HOT_US_LIMIT 개를 항상 채운다."""
    def _norm(s):
        code = s.get("code") or s.get("ticker", "")
        ticker = s.get("ticker") or to_yf_ticker(code)
        region = s.get("region") or ("KR" if str(code).isdigit() and len(str(code)) == 6 else "US")
        return {
            "code": code,
            "name": s.get("name") or code,
            "ticker": ticker,
            "region": region,
            "market": s.get("market"),
            "news_inference": s.get("news_inference"),
        }

    kr: List[Dict] = []
    us: List[Dict] = []
    seen = set()

    def _add(s, source):
        e = _norm(s)
        if not e["code"] or e["code"] in seen:
            return
        e["source"] = source
        seen.add(e["code"])
        (kr if e["region"] == "KR" else us).append(e)

    # 1순위 — 뉴스 기반 추론 (KR/US 섞여 나옴)
    if _WATCHLIST_BUILDER_AVAILABLE:
        try:
            news_items = _build_wl(
                db_path=DB_PATH,
                kr_limit=HOT_KR_LIMIT * 2,
                us_limit=HOT_US_LIMIT * 2,
                include_us=INCLUDE_US,
            ) or []
            for s in news_items:
                _add(s, "news")
        except Exception as e:
            log.warning(f"watchlist_builder failed: {e}")

    # 1순위(미국) — 해외 뉴스 추론 (US 키워드 → Gemini가 티커 출력 → 유니버스 매칭)
    if _WATCHLIST_BUILDER_AVAILABLE and INCLUDE_US and len(us) < HOT_US_LIMIT:
        try:
            us_news = _build_news_inferred(
                _US_QUERIES,
                limit=HOT_US_LIMIT * 2,
                db_path=DB_PATH,
                market_hint="미국 NASDAQ/NYSE",
            ) or []
            for s in us_news:
                _add(s, "news")
        except Exception as e:
            log.warning(f"US news inference failed: {e}")

    # 2순위 — KR 거래대금 상위
    if len(kr) < HOT_KR_LIMIT:
        try:
            for s in get_hot_stocks_kr(limit=HOT_KR_LIMIT * 3):
                _add(s, "volume")
                if len(kr) >= HOT_KR_LIMIT:
                    break
        except Exception as e:
            log.warning(f"get_hot_stocks_kr failed: {e}")
    # 3순위 — KR 대형주 마스터 (항상 가능)
    if len(kr) < HOT_KR_LIMIT:
        for w in KR_STOCK_UNIVERSE:
            _add({**w, "region": "KR"}, "major")
            if len(kr) >= HOT_KR_LIMIT:
                break

    if INCLUDE_US:
        # 2순위 — US 거래대금 상위
        if len(us) < HOT_US_LIMIT:
            try:
                for s in get_hot_stocks_us(limit=HOT_US_LIMIT * 3):
                    _add(s, "volume")
                    if len(us) >= HOT_US_LIMIT:
                        break
            except Exception as e:
                log.warning(f"get_hot_stocks_us failed: {e}")
        # 3순위 — US 대형주 마스터 (항상 가능)
        if len(us) < HOT_US_LIMIT:
            for w in US_STOCK_UNIVERSE:
                _add({**w, "region": "US"}, "major")
                if len(us) >= HOT_US_LIMIT:
                    break

    return kr[:HOT_KR_LIMIT] + us[:HOT_US_LIMIT]

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

CACHE_WARM_INTERVAL_SEC = int(os.getenv("CACHE_WARM_INTERVAL_SEC", "3600"))  # 1시간
CACHE_WARM_ENABLED = os.getenv("CACHE_WARM_ENABLED", "1") in ("1", "true", "True")
CACHE_WARM_PARALLEL = int(os.getenv("CACHE_WARM_PARALLEL", "3"))             # 워머는 3개만 (서버 부담 ↓)


def _warm_hot_stocks_cache():
    """전체 watchlist + 관심종목 분석 → hot_stocks 캐시 + 개별 report 캐시 동시 갱신.
    ★ 관심종목은 report 캐시 사전 워밍 전용 — hot_stocks 목록에는 포함하지 않음."""
    try:
        from concurrent.futures import ThreadPoolExecutor as _TPE, as_completed as _ac
        hot_list = build_watchlist()              # 대시보드에 실제로 표시할 종목
        hot_tickers = {w["ticker"] for w in hot_list}

        # 관심종목: report 캐시 사전 워밍용 (대시보드 리스트 제외)
        all_list = list(hot_list)
        for fav in _get_user_favorites_as_watchlist():
            if fav["ticker"] not in hot_tickers:
                all_list.append(fav)

        results: Dict[str, Dict] = {}

        def _job(w):
            try:
                r = analyze_one(w["ticker"], w["code"], w["name"])
                # 개별 report 캐시 미리 저장
                try:
                    report = _build_report_from_analysis(w, r)
                    cache_set(f"report:{r['ticker']}", report.model_dump())
                except Exception as re:
                    log.warning(f"[warmer] report cache 저장 실패 {w['ticker']}: {re}")
                return {
                    "ticker": r["ticker"], "name": r["name"], "price": r["price"],
                    "change_pct": r["change_pct"], "grade": r["grade"],
                    "score": r["score"], "summary": r["summary"],
                    "source": w.get("source"),
                }
            except Exception as e:
                log.error(f"warmer failed for {w['ticker']}: {e}")
                return None

        # 워머는 더 적은 워커로 (서버 부담 최소화)
        with _TPE(max_workers=CACHE_WARM_PARALLEL) as ex:
            futs = {ex.submit(_job, w): w for w in all_list}   # 관심종목도 분석 (캐시용)
            for f in _ac(futs):
                d = f.result()
                if d:
                    results[d["ticker"]] = d

        # hot_stocks 캐시: 원래 watchlist 종목만 저장 (관심종목 제외)
        out = []
        for w in hot_list:
            tk = to_yf_ticker(w["code"]) if w.get("code", "").isdigit() and len(w["code"]) == 6 else w["code"]
            d = results.get(tk) or results.get(w["code"])
            if d:
                out.append(d)
        if out:
            cache_set(HOT_STOCKS_CACHE_KEY, out)
            log.info(f"[warmer] 갱신 완료 — hot_stocks {len(out)}건 + 관심종목 report 캐시 {len(all_list) - len(hot_list)}건")
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
            CREATE TABLE IF NOT EXISTS user_favorites (
                ticker TEXT PRIMARY KEY,
                name   TEXT,
                code   TEXT,
                synced_at TEXT NOT NULL
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
    source: Optional[str] = None   # news | volume | major


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
    return_6m: Optional[float] = None          # 6개월 수익률 %
    return_ytd: Optional[float] = None         # YTD 수익률 %
    return_1y: Optional[float] = None          # 1년 수익률 %
    return_3y_ann: Optional[float] = None      # 3년 연평균 % (US)
    return_5y_ann: Optional[float] = None      # 5년 연평균 % (US)
    beta: Optional[float] = None               # 베타 (US)
    benchmark_index: Optional[str] = None     # 기초지수 (KR)
    # 물타기 / 불타기 점수 (ETF 전용)
    water_score: Optional[int] = None          # 0-100 (높을수록 저점 매수 적기)
    fire_score: Optional[int] = None           # 0-100 (높을수록 모멘텀 추가 적기)
    water_reasons: Optional[List[str]] = None  # 물타기 근거
    fire_reasons: Optional[List[str]] = None   # 불타기 근거
    # 시세 추가 정보
    price_52w_high: Optional[float] = None
    price_52w_low: Optional[float] = None
    avg_volume_20d: Optional[float] = None
    daily_volume: Optional[int] = None       # 당일 거래량 (KR, 네이버)
    # 기술적 지표 (물타기/불타기 계산 근거)
    ma20: Optional[float] = None             # 20일 이동평균
    ma60: Optional[float] = None             # 60일 이동평균
    position_52w_pct: Optional[float] = None # 52주 위치 % (0=저점, 100=고점)
    momentum_10d_pct: Optional[float] = None # 10일 모멘텀 %
    # 시세 상세 (네이버 sise 페이지)
    change_pct: Optional[float] = None       # 당일 등락률 %
    day_open: Optional[int] = None           # 시가
    day_high: Optional[int] = None           # 당일 고가
    day_low: Optional[int] = None            # 당일 저가
    trading_value_billion: Optional[float] = None  # 거래대금 (억원)
    market_cap_billion: Optional[float] = None     # 시가총액 (억원)
    shares_outstanding: Optional[int] = None       # 상장주식수
    foreign_holding: Optional[int] = None          # 외국인보유 (천주)
    # 구성종목 (naver coinfo / wisereport / pykrx 파싱 결과)
    constituents: Optional[List[str]] = None


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
    # 데이터 출처
    per_source: Optional[str] = None
    pbr_source: Optional[str] = None
    roe_source: Optional[str] = None
    revenue_growth_source: Optional[str] = None
    operating_margin_source: Optional[str] = None
    debt_ratio_source: Optional[str] = None


class NewsItem(BaseModel):
    title: str
    link: Optional[str] = None
    pub_date: Optional[str] = None      # 원본 형식 (예: "Sat, 24 May 2026 11:30:00 +0900")
    impact: Optional[str] = None        # 긍정/부정/중립


class PriceInfo(BaseModel):
    """일반 주식(비-ETF)용 시세·이동평균 스냅샷. analyzer.SemanticLayer.analyze_price()가
    파이썬으로 계산한 값을 그대로 노출 — AI 호출 없이 즉시 계산되므로 앱/클립보드에서 빠르게 씀."""
    current_price: Optional[float] = None
    change_pct: Optional[float] = None
    ma5: Optional[float] = None
    ma20: Optional[float] = None
    ma60: Optional[float] = None
    ma120: Optional[float] = None
    price_52w_high: Optional[float] = None
    price_52w_low: Optional[float] = None
    position_52w_pct: Optional[float] = None   # 0=52주 최저, 100=52주 최고
    momentum_10d_pct: Optional[float] = None
    signals: Optional[List[str]] = None        # "단기 정배열(MA5>MA20)" 등 사람이 읽을 수 있는 시그널


class StockReport(BaseModel):
    ticker: str
    name: str
    grade: str
    score: int
    news_summary: str                   # legacy 호환용 — 줄바꿈 텍스트
    news_items: Optional[List[NewsItem]] = None          # ETF 자체 뉴스 / 일반 종목 뉴스
    etf_constituent_news_items: Optional[List[NewsItem]] = None  # ETF 구성종목 뉴스 (ETF만)
    financials: Financials
    etf_info: Optional[EtfInfo] = None  # ETF면 채워짐, 일반 주식이면 None
    price_info: Optional[PriceInfo] = None  # 비-ETF 종목의 시세/이동평균 (ETF는 etf_info 쪽에 이미 있음)
    updated_at: str


class ClipboardText(BaseModel):
    text: str


# ─────────────────────────────────────────────
# ETF 물타기/불타기 점수 계산
# ─────────────────────────────────────────────
def _calc_etf_trade_scores(price_an: Dict, price: Dict, etf_raw: Dict) -> Dict:
    """
    물타기 점수(water): 저점 매수 적기도 (0=비추, 100=최적)
    불타기 점수(fire):  모멘텀 추가 매수 적기도 (0=비추, 100=최적)
    yfinance 실패 시 Naver 데이터(52주 고저, 수익률)로 대체 계산.
    """
    pos = price_an.get("position_52w_pct")   # 52주 위치 %
    mom = price_an.get("momentum_10d_pct")   # 10일 모멘텀 %
    ma = price_an.get("ma_summary") or {}
    ma20 = ma.get("MA20")
    ma60 = ma.get("MA60")
    cp = price_an.get("current_price")
    r1m = etf_raw.get("return_1m")
    r3m = etf_raw.get("return_3m")

    # ── Naver 데이터로 pos/cp 보완 (yfinance 실패 시) ──────────────
    hi52 = etf_raw.get("price_52w_high")
    lo52 = etf_raw.get("price_52w_low")
    nav = etf_raw.get("nav")
    # cp가 없으면 NAV로 대체
    if cp is None and nav:
        cp = nav
    # pos가 없으면 52주 고저+현재가로 계산
    if pos is None and hi52 and lo52 and cp:
        rng = hi52 - lo52
        if rng > 0:
            pos = round((cp - lo52) / rng * 100, 1)
    # mom이 없으면 1개월 수익률을 방향 proxy로 사용 (4주≈10거래일×2)
    if mom is None and r1m is not None:
        mom = r1m / 2  # 1개월 절반치를 10일 모멘텀 근사값으로

    # ── 물타기 점수 ──────────────────────────────
    water = 0
    water_r: List[str] = []

    if pos is not None:
        if pos < 15:
            water += 40; water_r.append(f"52주 저점 근처 ({pos:.0f}%)")
        elif pos < 30:
            water += 25; water_r.append(f"52주 하단권 ({pos:.0f}%)")
        elif pos < 50:
            water += 10; water_r.append(f"52주 중하단 ({pos:.0f}%)")

    if ma20 and cp and cp < ma20:
        water += 15; water_r.append(f"MA20 하향 이탈")
    if ma60 and cp and cp < ma60:
        water += 20; water_r.append(f"MA60 하향 (중기 약세)")
    if mom is not None and mom < -5:
        water += 10; water_r.append(f"10일 하락 ({mom:+.1f}%)")
    if r1m is not None and r1m < -5:
        water += 10; water_r.append(f"1개월 수익률 {r1m:+.1f}%")
    elif r3m is not None and r3m < -10:
        water += 5; water_r.append(f"3개월 수익률 {r3m:+.1f}%")

    water = min(water, 100)

    # ── 불타기 점수 ──────────────────────────────
    fire = 0
    fire_r: List[str] = []

    if pos is not None:
        if pos > 85:
            fire += 40; fire_r.append(f"52주 신고점 근처 ({pos:.0f}%)")
        elif pos > 70:
            fire += 25; fire_r.append(f"52주 상단권 ({pos:.0f}%)")
        elif pos > 50:
            fire += 10; fire_r.append(f"52주 중상단 ({pos:.0f}%)")

    if ma20 and cp and cp > ma20:
        fire += 15; fire_r.append(f"MA20 상향 돌파")
    if ma60 and cp and cp > ma60:
        fire += 20; fire_r.append(f"MA60 상향 (중기 강세)")
    if mom is not None and mom > 5:
        fire += 10; fire_r.append(f"10일 상승 ({mom:+.1f}%)")
    if r1m is not None and r1m > 5:
        fire += 10; fire_r.append(f"1개월 수익률 {r1m:+.1f}%")
    elif r3m is not None and r3m > 10:
        fire += 5; fire_r.append(f"3개월 수익률 {r3m:+.1f}%")

    fire = min(fire, 100)

    return {
        "water_score": water,
        "fire_score": fire,
        "water_reasons": water_r if water_r else ["뚜렷한 저점 신호 없음"],
        "fire_reasons": fire_r if fire_r else ["뚜렷한 상승 신호 없음"],
        "price_52w_high": price.get("high_52w") or hi52,
        "price_52w_low": price.get("low_52w") or lo52,
        "avg_volume_20d": price.get("avg_volume_20d"),
        # 계산 근거 지표 (앱/클립보드에 표시)
        "ma20": ma.get("MA20") if ma else None,
        "ma60": ma.get("MA60") if ma else None,
        "position_52w_pct": round(pos, 1) if pos is not None else None,
        "momentum_10d_pct": round(mom, 1) if mom is not None else None,
    }


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

    # ★ ETF: yfinance 실패 시 네이버 일별시세 데이터로 price_an 대체
    etf_raw_pre = bundle.get("etf_info")
    if etf_raw_pre and price_an.get("error") and etf_raw_pre.get("naver_price"):
        naver_price = etf_raw_pre["naver_price"]
        price_an_naver = sem.analyze_price(naver_price)
        if not price_an_naver.get("error"):
            price_an = price_an_naver
            price = naver_price  # _calc_etf_trade_scores용 price도 교체
            log.info(f"ETF {ticker}: 네이버 일별시세 기반 price_an 사용")
    etf_raw = bundle.get("etf_info")  # ETF면 Dict, 아니면 None
    # ★ fund_name 보정: naver/pykrx 이름보다 검색 name이 더 정확 (잘못된 코드 매핑 대응)
    if etf_raw and name:
        etf_raw = dict(etf_raw)
        etf_raw["fund_name"] = name

    # ★ ETF 물타기/불타기 점수 계산 (price_an 에러여도 Naver 데이터로 시도)
    if etf_raw:
        trade_scores = _calc_etf_trade_scores(price_an, price, etf_raw)
        etf_raw.update(trade_scores)
        bundle["etf_info"] = etf_raw  # ★ 복사본을 bundle에 반영 (ma20/water_score 등 report 엔드포인트에 전달)

    # Gemini 호출은 실패해도 계속 진행
    try:
        picks = gemini_filter.filter_news(items, top_k=3, company_name=name, ticker=code) if items else []
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

    # ★ ETF 구성종목 뉴스 별도 처리 (3개)
    const_picks: List[Dict] = []
    if etf_raw:
        const_items = (bundle.get("etf_constituent_news") or {}).get("items", [])
        if const_items:
            try:
                const_picks = gemini_filter.filter_news(const_items, top_k=3)
                if not const_picks or const_picks[0].get("error"):
                    raise ValueError("gemini fallback")
            except Exception:
                const_picks = [
                    {
                        "title": it.get("title", ""),
                        "link": it.get("link"),
                        "pub_date": it.get("pub_date"),
                        "impact": "중립",
                        "reason": "raw_fallback",
                    }
                    for it in const_items[:3]
                    if it.get("title")
                ]

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
            "const_picks": const_picks,
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
    # 2) KR ETF 마스터에서 (이름 포함 — 관심종목에서 이름이 표시되도록)
    for s in KR_ETF_UNIVERSE:
        if t == s["code"] or t == s["code"] + ".KS":
            return {"ticker": f"{s['code']}.KS", "code": s["code"], "name": s["name"], "region": "KR"}
    # 2.5) KR ETF 동적 목록 (pykrx — KR_ETF_UNIVERSE에 없는 신규 ETF 커버)
    for s in get_kr_etf_info_list():
        code = s.get("code", "")
        if t == code or t == code + ".KS":
            return {"ticker": f"{code}.KS", "code": code, "name": s.get("name", code), "region": "KR"}
    # 3) 미국 마스터에서
    for s in US_STOCK_UNIVERSE:
        if t == s["code"]:
            return {"ticker": s["code"], "code": s["code"], "name": s["name"], "region": "US"}
    # 3.5) 동적 유니버스(전체 상장 코스피/코스닥/나스닥/NYSE)에서 코드·심볼 조회 → 이름 확보
    try:
        import universe as _uni
        u = _uni.lookup_by_code(t)
        if u:
            if u.get("region") == "KR":
                return {"ticker": to_yf_ticker(u["code"]), "code": u["code"], "name": u["name"], "region": "KR"}
            return {"ticker": u["code"], "code": u["code"], "name": u["name"], "region": "US"}
    except Exception:
        pass
    # 4) 모르는 6자리 → 국장으로 추정 (name은 빈값 → pykrx/naver에서 채워짐)
    if t.isdigit() and len(t) == 6:
        return {"ticker": f"{t}.KS", "code": t, "name": t, "region": "KR"}
    # 5) 알파벳만 있으면 미국 티커로 추정
    if t.isalpha() and 1 <= len(t) <= 5:
        return {"ticker": t, "code": t, "name": t, "region": "US"}
    # 6) 점이 들어있으면 그대로 yfinance 티커로
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


def cache_del(key: str) -> None:
    try:
        with db_conn() as conn:
            conn.execute("DELETE FROM report_cache WHERE ticker = ?", (key,))
            conn.commit()
    except Exception as e:
        log.warning(f"cache_del({key}) failed: {e}")


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
                    "source": w.get("source"),
                }
            except Exception as e:
                log.error(f"hot-stocks failed for {w['ticker']}: {e}")
                return {
                    "ticker": w["code"], "name": w["name"], "price": 0.0,
                    "change_pct": 0.0, "grade": "WATCH", "score": 50,
                    "summary": f"분석 실패: {str(e)[:60]}",
                    "source": w.get("source"),
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


# ─────────────────────────────────────────────
# 관심종목 서버 동기화 (워머 사전 캐싱용)
# ─────────────────────────────────────────────
class FavoriteItem(BaseModel):
    ticker: str
    name: Optional[str] = None
    code: Optional[str] = None


@app.post("/api/user/favorites")
def sync_favorites(items: List[FavoriteItem]):
    """앱 관심종목 목록을 서버에 저장. 워머가 이 목록도 사전 분석함."""
    now = datetime.now().isoformat()
    with db_conn() as conn:
        conn.execute("DELETE FROM user_favorites")
        for item in items:
            conn.execute(
                "INSERT OR REPLACE INTO user_favorites (ticker, name, code, synced_at) VALUES (?, ?, ?, ?)",
                (item.ticker, item.name, item.code, now),
            )
        conn.commit()
    log.info(f"[favorites] 동기화 완료 — {len(items)}건")
    return {"ok": True, "count": len(items)}


@app.get("/api/user/favorites")
def get_favorites():
    """서버에 저장된 관심종목 목록 조회."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT ticker, name, code FROM user_favorites ORDER BY synced_at DESC"
        ).fetchall()
    return [{"ticker": r[0], "name": r[1], "code": r[2]} for r in rows]


def _get_user_favorites_as_watchlist() -> List[Dict]:
    """user_favorites 테이블 → 워머용 watchlist 포맷."""
    try:
        with db_conn() as conn:
            rows = conn.execute(
                "SELECT ticker, name, code FROM user_favorites"
            ).fetchall()
        out = []
        for ticker, name, code in rows:
            c = code or (ticker.replace(".KS", "").replace(".KQ", "") if ticker else "")
            out.append({
                "ticker": ticker,
                "name": name or ticker,
                "code": c,
                "region": "KR" if c.isdigit() and len(c) == 6 else "US",
            })
        return out
    except Exception as e:
        log.warning(f"[favorites] 조회 실패: {e}")
        return []


def _build_report_from_analysis(entry: dict, r: dict) -> "StockReport":
    """analyze_one() 결과 → StockReport 변환 (워머/엔드포인트 공용)."""
    intr = r["_internals"]
    fin_an = intr["fin_an"]
    picks = intr["picks"]
    const_picks = intr.get("const_picks", [])
    bundle = intr["bundle"]
    market_metrics = (bundle or {}).get("market_metrics") or {}

    news_items_out: List[NewsItem] = []
    if picks and not picks[0].get("error"):
        seen_titles = set()
        for p in picks:
            title = (p.get("title") or "").strip()
            if not title:
                continue
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
        news_lines = [f"• [{ni.impact or '-'}] {ni.title}" for ni in news_items_out]
        news_summary = "\n".join(news_lines) if news_lines else "최신 뉴스 부족"
    else:
        news_summary = "최신 뉴스 부족 또는 AI 키 미설정"

    margins = (fin_an.get("margins") or {}) if not fin_an.get("error") else {}
    growth = (fin_an.get("yoy_growth_pct") or {}) if not fin_an.get("error") else {}
    mm_safe = market_metrics if not market_metrics.get("error") else {}

    per = mm_safe.get("per")
    pbr = mm_safe.get("pbr")
    roe = mm_safe.get("roe_pct")

    revenue_growth = mm_safe.get("revenue_growth_pct")
    if revenue_growth is None:
        revenue_growth = growth.get("revenue")

    operating_margin = mm_safe.get("operating_margin_pct")
    if operating_margin is None:
        operating_margin = margins.get("operating_margin_pct")

    debt_ratio = mm_safe.get("debt_to_equity_pct")
    if debt_ratio is None:
        debt_ratio = fin_an.get("debt_to_equity_pct") if not fin_an.get("error") else None

    def _fmt_source(s: Optional[str]) -> Optional[str]:
        if not s:
            return None
        if s == "naver_scrape":
            return "네이버"
        if s in ("krx", "pykrx"):
            return "KRX"
        if s.startswith("dart"):
            return "DART"
        if s == "yf_annual":
            return "Yahoo"
        return s

    def _na_reason(val, field: str) -> Optional[str]:
        if val is not None:
            return None
        has_fin_error = bool(fin_an.get("error"))
        has_mm_error = bool(market_metrics.get("error"))
        is_loss = (
            (roe is not None and roe < 0)
            or (operating_margin is not None and operating_margin < 0)
        )
        is_impaired = is_loss and pbr is None and not has_mm_error
        if field == "per":
            if is_loss: return "적자"
            if has_mm_error: return "오류"
            return "미제공"
        if field == "pbr":
            if is_impaired: return "자본잠식 의심"
            if has_mm_error: return "오류"
            return "미제공"
        if field == "roe":
            if is_loss: return "적자"
            if has_fin_error: return "오류"
            return "미제공"
        if field == "revenue_growth":
            if has_fin_error: return "오류"
            return "전기 비교불가"
        if field == "operating_margin":
            if is_loss: return "적자"
            if has_fin_error: return "오류"
            return "미제공"
        if field == "debt_ratio":
            if has_fin_error: return "오류"
            return "미제공"
        return "미제공"

    financials = Financials(
        per=per, pbr=pbr, roe=roe,
        revenue_growth=revenue_growth,
        operating_margin=operating_margin,
        debt_ratio=debt_ratio,
        per_basis=mm_safe.get("per_basis") or "연간",
        pbr_basis=mm_safe.get("pbr_basis") or "분기말",
        roe_basis=mm_safe.get("roe_basis") or "연간",
        revenue_growth_basis=mm_safe.get("revenue_growth_basis") or "YoY",
        operating_margin_basis=mm_safe.get("operating_margin_basis") or "연간",
        debt_ratio_basis="분기말",
        per_na_reason=_na_reason(per, "per"),
        pbr_na_reason=_na_reason(pbr, "pbr"),
        roe_na_reason=_na_reason(roe, "roe"),
        revenue_growth_na_reason=_na_reason(revenue_growth, "revenue_growth"),
        operating_margin_na_reason=_na_reason(operating_margin, "operating_margin"),
        debt_ratio_na_reason=_na_reason(debt_ratio, "debt_ratio"),
        per_source=_fmt_source(mm_safe.get("per_source")),
        pbr_source=_fmt_source(mm_safe.get("pbr_source")),
        roe_source=_fmt_source(mm_safe.get("roe_source")),
        revenue_growth_source=_fmt_source(mm_safe.get("revenue_growth_source")),
        operating_margin_source=_fmt_source(mm_safe.get("operating_margin_source")),
        debt_ratio_source=_fmt_source(mm_safe.get("debt_to_equity_source")),
    )

    etf_raw = r.get("_internals", {}).get("bundle", {}).get("etf_info")
    etf_info_model = EtfInfo(**etf_raw) if etf_raw else None

    # ★ 비-ETF 종목용 시세/이동평균 (이미 analyze_one()이 계산해둔 price_an 재사용 — AI 호출 없이 즉시)
    price_info_model = None
    if not etf_raw:
        price_an_pi = intr.get("price_an") or {}
        if not price_an_pi.get("error"):
            ma_pi = price_an_pi.get("ma_summary") or {}
            bundle_price_pi = (bundle or {}).get("price") or {}
            price_info_model = PriceInfo(
                current_price=price_an_pi.get("current_price"),
                change_pct=price_an_pi.get("change_pct"),
                ma5=ma_pi.get("MA5"),
                ma20=ma_pi.get("MA20"),
                ma60=ma_pi.get("MA60"),
                ma120=ma_pi.get("MA120"),
                price_52w_high=bundle_price_pi.get("high_52w"),
                price_52w_low=bundle_price_pi.get("low_52w"),
                position_52w_pct=price_an_pi.get("position_52w_pct"),
                momentum_10d_pct=price_an_pi.get("momentum_10d_pct"),
                signals=price_an_pi.get("signals") or None,
            )

    # ★ ETF 구성종목 뉴스 아이템 변환
    const_news_items_out: List[NewsItem] = []
    if const_picks:
        seen_c = set()
        for p in const_picks:
            if p.get("error"):
                continue
            title = (p.get("title") or "").strip()
            if not title:
                continue
            norm = "".join(ch for ch in title if ch.isalnum())[:60].lower()
            if norm in seen_c:
                continue
            seen_c.add(norm)
            const_news_items_out.append(NewsItem(
                title=title,
                link=p.get("link"),
                pub_date=p.get("pub_date"),
                impact=p.get("impact") or "중립",
            ))

    return StockReport(
        ticker=r["ticker"],
        name=r["name"],
        grade=r["grade"],
        score=r["score"],
        news_summary=news_summary,
        news_items=news_items_out or None,
        etf_constituent_news_items=const_news_items_out or None,
        financials=financials,
        etf_info=etf_info_model,
        price_info=price_info_model,
        updated_at=datetime.now().isoformat(),
    )


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

        report = _build_report_from_analysis(entry, r)
        cache_set(cache_key, report.model_dump())
        if refresh:
            # 새로고침 시 텍스트(클립보드) 캐시도 비워 다음 복사 때 최신으로 재생성
            cache_del(f"clipboard:{entry['ticker']}")
            cache_del(f"clipboard:{entry['code']}")
        return report


def _build_etf_beginner_prompt(report_text: str, name: str, ticker: str) -> str:
    """ETF 전용 초보자용 Claude 질문 프롬프트."""
    return f"""당신은 ETF 투자 전문가입니다.
아래 ETF 리포트(기술적 지표 + 뉴스 + 펀더멘털)를 종합해서 초보 투자자가 바로 실행할 수 있는 구체적 투자 전략을 제시해주세요.
어려운 금융 용어는 쉬운 말로 풀고, 수치마다 "좋다/나쁘다/보통" 판단을 명확히 해주세요.

⚠️ 절대 규칙:
- 리포트에 없는 구성종목·비중·종목명은 절대 추측하지 마세요.
- 기초지수 이름에서 유추한 추측도 금지입니다.
- 데이터가 없으면 "데이터 없음"으로만 표시하세요.
- 투자 판단은 리포트 수치에 근거해서 구체적으로 제시하세요. "상황에 따라 다름" 같은 애매한 답변 금지.

[분석 ETF] {name} ({ticker})

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

다음 6가지를 순서대로 작성해주세요:

【1】 📰 뉴스가 이 ETF에 미치는 영향
뉴스가 없으면 이 섹션 전체 생략. 뉴스가 있으면 각각:
  ① 한 줄 핵심 요약
  ② 이 ETF 구성종목에 직접 미치는 영향 (리포트에 구성종목 있을 때만 구체적으로)
  ③ ETF 가격에 단기(1~2주) / 중기(1~3개월) 영향
  ④ 판정: 🟢 호재 / 🔴 악재 / 🟡 중립 + 이유 한 줄
- 뉴스 전체를 종합해서 마지막에 한 줄: "뉴스 종합 → ETF에 [긍정적/부정적/혼재]"

【2】 📦 ETF 구조 핵심 파악
  ▸ 기초지수: 어떤 테마·자산을 추종하는지 쉽게 설명
  ▸ 구성종목: 리포트에 있으면 각 회사 한 줄 설명 + 쏠림 위험. 없으면 "구성종목 데이터 없음"만 쓰세요.
  ▸ 운용보수: 저렴/보통/비쌈 판단 (0.3% 이하=매우 저렴, 0.5~1%=보통, 1% 초과=비쌈)
  ▸ AUM 규모: 크면 유동성 좋음, 작으면 주의

【3】 📊 기술적 분석 & 타이밍 판단
리포트의 MA20/MA60/52주 위치/모멘텀/수익률 수치를 모두 활용해서:
  ▸ 현재 가격 위치: 52주 위치 %로 "현재 고점 근처인지 저점 근처인지" 판단
  ▸ 이동평균선 분석: 현재가 vs MA20 vs MA60 관계 → 상승추세/하락추세/횡보 판단
  ▸ 모멘텀: 최근 10일 방향성이 오르는 중인지 꺾이는 중인지
  ▸ 수익률 맥락: 1개월/3개월/1년 수익률 흐름이 가속/둔화/반전 중인지
  ▸ 💧 물타기 점수 해석: 점수가 있으면 → 수치 기반으로 "지금 저점 매수 타이밍이 맞는지" 명확 판단. 없으면 생략.
  ▸ 🔥 불타기 점수 해석: 점수가 있으면 → "지금 모멘텀 추가 매수가 적절한지" 명확 판단. 없으면 생략.

【4】 🔭 모멘텀 전망 (단기·중기)
  ▸ 단기 모멘텀 (1~4주): 뉴스 + 차트 위치 + 이동평균 종합 → 상승/하락/횡보 중 어느 방향이 강한지
  ▸ 중기 모멘텀 (1~3개월): 이 ETF 테마의 업황 사이클 + 글로벌 트렌드 연결해서 방향성 예측
  ▸ 모멘텀 꺾일 수 있는 리스크 신호 2가지
  ▸ 모멘텀 강화될 수 있는 트리거 이벤트 2가지

【5】 💰 구체적 매수 전략
리포트의 모든 수치를 종합해서 아래를 구체적으로 제시하세요:

  ▸ 전략 선택: 아래 중 하나를 선택하고 이유를 설명하세요
    - 💧 물타기 전략: 현재가 근처 또는 하락 시 분할 매수 (저점 매집 목적)
    - 🔥 불타기 전략: 상승 모멘텀 확인 후 추가 매수 (추세 추종 목적)
    - ⏸️ 관망: 지금은 매수 타이밍이 아닌 이유

  ▸ 분할 매수 플랜 (물타기 또는 불타기 선택 시):
    - 1차 매수: 언제, 얼마나 (예: "지금 바로, 예산의 30%")
    - 2차 매수: 어떤 조건일 때 (예: "MA60 지지 확인 시 추가 30%")
    - 3차 매수: 추가 조건 (예: "추가 -5% 하락 시 나머지 40%")

  ▸ 손절 기준: 어느 가격/조건이면 손절할지 (예: "MA60 이탈 + 모멘텀 음전환 시")
  ▸ 목표 수익률: 단기/중기 목표 (수익률 데이터 기반으로 현실적으로)

【6】 ⚡ 최종 한 줄 요약
"지금 [ETF명]은 [물타기/불타기/관망] — [핵심 이유 한 줄]"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[📋 리포트 원문]
{report_text}
"""


def _build_beginner_prompt(report_text: str, name: str, ticker: str) -> str:
    """리포트 원문을 초보 투자자용 Claude/Gemini 질문 프롬프트로 감쌈 (상세 버전)."""
    return f"""당신은 20년 경력의 친절한 주식 투자 선생님입니다.
주식을 처음 접하는 초보 투자자도 완전히 이해할 수 있도록, 아래 종목 리포트(시세·이동평균·재무·뉴스·등급)를 근거로 깊이 있고 구체적으로 분석해주세요.

[필수 규칙]
- 어려운 금융 용어는 반드시 쉬운 말 + 일상 비유로 풀어서 설명할 것.
- 모든 숫자에 "좋다 / 보통 / 나쁘다" 판단과 그 이유를 붙일 것.
- 리포트에 있는 수치만 사용하고, 없는 정보는 "데이터 없음"으로 표시(추측 금지).
- 매수가·목표가·손절가는 반드시 구체적인 숫자로 제시할 것. "상황에 따라 다름" 같은 답변 금지.

[분석 종목] {name} ({ticker})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

아래 5개 항목을 순서대로 작성해주세요.

【1】 📰 뉴스 분석
뉴스가 있으면 각각:
  ① 무슨 일인지 2~3줄로 쉽게 요약
  ② 이 회사에 호재🟢 / 악재🔴 / 중립🟡 인지 + 그 이유
  ③ 단기(1~2주)와 중기(1~3개월) 주가에 미칠 영향
끝에 한 줄: "뉴스 종합 → 이 종목에 [긍정 / 부정 / 혼재]"
(뉴스가 없으면 이 항목은 생략)

【2】 📈 기술적 분석 (차트·추세)
  ▸ 현재가가 이동평균선(5·20·60·120일) 대비 어디 있는지 → 정배열/역배열, 상승추세인지 하락추세인지 쉬운 말로
  ▸ 52주 최고·최저 대비 현재 위치 → 지금이 비싼 구간인지 싼 구간인지
  ▸ 최근 모멘텀(오르는 힘/내리는 힘) 평가
  → 한 줄 결론: "차트상 지금은 [매수하기 좋은 / 관망할 / 위험한] 자리"

【3】 📊 재무제표 쉬운 해설
아래 지표를 각각 "① 이게 뭔지(비유) → ② 이 종목 수치 → ③ 좋은지 나쁜지 + 이유" 3단계로 설명:
  ▸ PER (주가수익비율) — 이익 대비 주가가 비싼가 싼가
  ▸ PBR (주가순자산비율) — 회사 자산 대비 주가 수준
  ▸ ROE (자기자본이익률) — 주주 돈으로 얼마나 효율적으로 버나
  ▸ 영업이익률 — 팔아서 실제로 남기는 비율 (돈 잘 버는 사업인지)
  ▸ 매출 성장률 — 회사가 커지고 있는지
  ▸ 부채비율 — 빚이 위험한 수준인지 (업종 특성도 감안)
  → 한 줄 결론: "재무 종합 → 이 회사는 [튼튼한 / 보통의 / 취약한] 회사"

【4】 💰 매매 전략 (구체적 가격 제시)
현재가를 기준으로 구체적인 숫자를 제시해주세요:
  ▸ 지금 사도 되는가? → (지금 매수 / 조금 기다렸다 매수 / 매수 보류) 중 택1 + 이유
  ▸ 적정 매수가: 얼마쯤(또는 어떤 가격 구간)에서 사는 게 좋은지
  ▸ 목표 매도가(익절): 어느 정도 오르면 파는 게 좋은지
  ▸ 손절가: 얼마 아래로 떨어지면 손절해야 하는지
  ▸ 분할매수 제안 (예: 지금 절반 사고, 더 떨어지면 추가 매수)
  ▸ 이미 보유 중이라면: 계속 들고 갈지 / 더 살지 / 줄일지도 함께 언급

【5】 🎯 초보자용 최종 결론
  - 종합 판단: 적극매수 / 매수 / 관망 / 매도 중 하나 + 핵심 이유 2~3줄
  - 이 종목의 가장 큰 매력 1가지, 가장 큰 위험 1가지
  - 한 줄 요약 (25자 이내)

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
            # ★ ETF: 물타기/불타기 점수 계산 + naver_price로 price 대체
            _etf_raw = bundle.get("etf_info")
            if _etf_raw:
                _price = bundle.get("price") or {}
                _price_an = sem.analyze_price(_price)
                if _price_an.get("error") and _etf_raw.get("naver_price"):
                    _np = _etf_raw["naver_price"]
                    _price_an_n = sem.analyze_price(_np)
                    if not _price_an_n.get("error"):
                        _price_an = _price_an_n
                        _price = _np
                        bundle["price"] = _np  # _build_etf도 이 price 사용
                _ts = _calc_etf_trade_scores(_price_an, _price, _etf_raw)
                _etf_raw.update(_ts)

            report_text = report_builder.build(
                ticker=entry["ticker"],
                bundle=bundle,
                company_name=entry["name"],
                top_k_news=3,
                max_related=8,
            )
            # ETF 여부 감지: bundle에 etf_info 있거나 KR_ETF_UNIVERSE 코드이면 ETF
            _is_etf = bool(bundle.get("etf_info")) or any(
                e.get("code") == entry.get("code") for e in KR_ETF_UNIVERSE
            )
            if _is_etf:
                text = _build_etf_beginner_prompt(report_text, entry["name"], entry["ticker"])
            else:
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

# ─────────────────────────────────────────────────────────────
# ETF 네이버 코드 관리 (사용자가 직접 등록)
# wisereport 구성종목 동적 조회에 사용
# ─────────────────────────────────────────────────────────────
ETF_NAVER_CODES_PATH = os.path.join(os.path.dirname(__file__), "etf_naver_codes.json")


def _load_etf_naver_codes() -> Dict[str, str]:
    try:
        with open(ETF_NAVER_CODES_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_etf_naver_codes(codes: Dict[str, str]) -> None:
    with open(ETF_NAVER_CODES_PATH, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False, indent=2)


class EtfNaverCodeBody(BaseModel):
    naver_code: str


@app.get("/api/etf/naver-codes")
def get_etf_naver_codes():
    """등록된 ETF KRX→네이버 코드 매핑 전체 반환"""
    return _load_etf_naver_codes()


@app.put("/api/etf/naver-code/{krx_code}")
def put_etf_naver_code(krx_code: str, body: EtfNaverCodeBody):
    """ETF 네이버 코드 등록/수정. wisereport 구성종목 조회에 즉시 반영."""
    naver_code = body.naver_code.strip()
    if not naver_code:
        raise HTTPException(400, "naver_code가 비어있습니다")
    codes = _load_etf_naver_codes()
    codes[krx_code] = naver_code
    _save_etf_naver_codes(codes)
    return {"ok": True, "krx_code": krx_code, "naver_code": naver_code}


@app.delete("/api/etf/naver-code/{krx_code}")
def delete_etf_naver_code(krx_code: str):
    """ETF 네이버 코드 삭제"""
    codes = _load_etf_naver_codes()
    removed = codes.pop(krx_code, None)
    if removed is not None:
        _save_etf_naver_codes(codes)
    return {"ok": True, "removed": removed is not None}


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000)
