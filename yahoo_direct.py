"""
yfinance 우회 모듈 — Yahoo Finance Chart API 직통 호출

Pi 3B+ 환경에서 yfinance 0.2.50/0.2.54가 curl_cffi 부재로
크럼(crumb) 인증에 실패하는 문제 해결용.

curl로는 HTTP 200 잘 받아지므로, 동일한 헤더로 requests 호출.
"""
import logging
import os
from typing import Dict, Optional, List

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# Tiingo — Yahoo 차단 시 최우선 폴백
# ─────────────────────────────────────────────────────────────
_TIINGO_TOKEN: Optional[str] = os.environ.get("TIINGO_API_KEY")


def download_tiingo(ticker: str, period: str = "1mo") -> pd.DataFrame:
    """
    Tiingo Daily API로 가격 히스토리 조회.
    인증 필요하지만 Pi에서 정상 작동, 무료 1000회/일.
    """
    token = _TIINGO_TOKEN or os.environ.get("TIINGO_API_KEY")
    if not token:
        logger.warning("TIINGO_API_KEY 미설정 — Tiingo 폴백 비활성")
        return pd.DataFrame()

    from datetime import datetime, timedelta
    end = datetime.now()
    p = (period or "1mo").lower().strip()
    if p.endswith("d"):
        delta = timedelta(days=int(p[:-1]))
    elif p.endswith("mo"):
        delta = timedelta(days=int(p[:-2]) * 30)
    elif p.endswith("y"):
        delta = timedelta(days=int(p[:-1]) * 365)
    else:
        delta = timedelta(days=30)
    start = end - delta

    # Tiingo는 ETF/레버리지 포함 미국 주식 전부 지원
    # 한국 종목(.KS/.KQ)은 미지원 → 빈 DataFrame 반환
    t = ticker.upper().replace(".KS", "").replace(".KQ", "")
    if ticker.upper().endswith((".KS", ".KQ")):
        return pd.DataFrame()

    url = f"https://api.tiingo.com/tiingo/daily/{t}/prices"
    params = {
        "startDate": start.strftime("%Y-%m-%d"),
        "endDate": end.strftime("%Y-%m-%d"),
        "token": token,
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            logger.warning("Tiingo %s HTTP %s", ticker, r.status_code)
            return pd.DataFrame()
        data = r.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df.set_index("date").sort_index()
        df = df.rename(columns={
            "adjClose": "Close",
            "adjOpen": "Open",
            "adjHigh": "High",
            "adjLow": "Low",
            "adjVolume": "Volume",
        })
        cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
        return df[cols].dropna(how="all")
    except Exception as e:
        logger.warning("Tiingo %s 실패: %s", ticker, e)
        return pd.DataFrame()

# 헤더 — curl 로 HTTP 200 받았던 조합. Origin/Referer 빼야 CORS 경로 안 탐.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

# 세션 + 크럼 캐시 (프로세스 동안 재사용)
_session: Optional[requests.Session] = None
_crumb: Optional[str] = None
_crumb_tried: bool = False


def _get_session() -> requests.Session:
    """가벼운 세션. fc.yahoo.com warm-up 안 함 (rate limit 절약)."""
    global _session
    if _session is not None:
        return _session
    s = requests.Session()
    s.headers.update(_HEADERS)
    _session = s
    return s


def _ensure_crumb(s: requests.Session) -> Optional[str]:
    """quoteSummary 처음 호출할 때만 크럼 시도. 실패해도 chart 는 가능."""
    global _crumb, _crumb_tried
    if _crumb or _crumb_tried:
        return _crumb
    _crumb_tried = True
    try:
        s.get("https://fc.yahoo.com/", timeout=10)
        r = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10)
        if r.status_code == 200 and r.text and "Too Many" not in r.text:
            _crumb = r.text.strip()
    except Exception as e:
        logger.debug("crumb 획득 실패: %s", e)
    return _crumb


def _period_to_stooq_dates(period: str):
    """period 문자열 → (start_str, end_str) YYYYMMDD 형식."""
    from datetime import datetime, timedelta
    end = datetime.now()
    p = (period or "1mo").lower().strip()
    if p.endswith("d"):
        delta = timedelta(days=int(p[:-1]))
    elif p.endswith("mo"):
        delta = timedelta(days=int(p[:-2]) * 30)
    elif p.endswith("y"):
        delta = timedelta(days=int(p[:-1]) * 365)
    else:
        delta = timedelta(days=30)
    start = end - delta
    return start.strftime("%Y%m%d"), end.strftime("%Y%m%d")


def download_stooq(ticker: str, period: str = "1mo") -> pd.DataFrame:
    """
    Stooq.com CSV 직통 — Yahoo 차단 시 폴백.
    인증 불필요, Pi에서도 정상 작동.
    """
    import io
    # Stooq 티커 변환: AAPL → AAPL.US, 005930.KS → 005930.KS (그대로)
    t = ticker.upper()
    if not ("." in t):
        t = f"{t}.US"
    start, end = _period_to_stooq_dates(period)
    url = f"https://stooq.com/q/d/l/?s={t}&d1={start}&d2={end}&i=d"
    try:
        s = _get_session()
        r = s.get(url, timeout=15)
        if r.status_code != 200 or len(r.content) < 50:
            logger.warning("stooq %s HTTP %s / 빈 응답", ticker, r.status_code)
            return pd.DataFrame()
        df = pd.read_csv(io.StringIO(r.text))
        if df.empty or "Close" not in df.columns:
            return pd.DataFrame()
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
        # 컬럼명 통일
        df = df.rename(columns={"Adj Close": "Adj Close"})
        return df
    except Exception as e:
        logger.warning("stooq %s 실패: %s", ticker, e)
        return pd.DataFrame()


def download(
    ticker: str,
    period: str = "1mo",
    interval: str = "1d",
) -> pd.DataFrame:
    """
    yfinance.download() 대체.
    Yahoo Chart API → 실패 시 Stooq 폴백.

    Returns
    -------
    pd.DataFrame  — 컬럼: Open, High, Low, Close, Adj Close, Volume
                    인덱스: DatetimeIndex
                    실패 시 빈 DataFrame
    """
    s = _get_session()
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"interval": interval, "range": period}
    try:
        r = s.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning("%s Yahoo chart 실패, Tiingo 폴백: %s", ticker, e)
        return download_tiingo(ticker, period)

    try:
        result = data["chart"]["result"][0]
        ts = result["timestamp"]
        quote = result["indicators"]["quote"][0]
        adjclose_list = result["indicators"].get("adjclose", [{}])
        adjclose = (
            adjclose_list[0].get("adjclose", [None] * len(ts))
            if adjclose_list else [None] * len(ts)
        )
        df = pd.DataFrame(
            {
                "Open": quote.get("open", []),
                "High": quote.get("high", []),
                "Low": quote.get("low", []),
                "Close": quote.get("close", []),
                "Adj Close": adjclose,
                "Volume": quote.get("volume", []),
            },
            index=pd.to_datetime(ts, unit="s"),
        )
        df.index.name = "Date"
        df = df.dropna(how="all")
        if df.empty:
            raise ValueError("Yahoo 응답은 왔으나 데이터 없음 (차단)")
        return df
    except (KeyError, IndexError, TypeError, ValueError) as e:
        logger.warning("%s Yahoo 파싱 실패, Tiingo 폴백: %s", ticker, e)
        return download_tiingo(ticker, period)


def get_quote(ticker: str) -> Dict:
    """
    현재가/시총/PER/PBR 등 기본 시세 정보 (crumb 필요).
    실패 시 빈 dict.
    """
    s = _get_session()
    crumb = _ensure_crumb(s)
    if not crumb:
        # crumb 없으면 quoteSummary는 못 씀
        return {}
    modules = ",".join([
        "price", "summaryDetail",
        "defaultKeyStatistics", "financialData",
    ])
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    params = {"modules": modules, "crumb": crumb}
    try:
        r = s.get(url, params=params, timeout=15)
        if r.status_code == 401:
            # 크럼 만료 — 다시 받기
            _reset_session()
            return {}
        r.raise_for_status()
        data = r.json()
        result = data["quoteSummary"]["result"][0]
    except Exception as e:
        logger.warning("%s quoteSummary 실패: %s", ticker, e)
        return {}

    def _raw(d: Dict, k: str):
        v = d.get(k)
        if isinstance(v, dict):
            return v.get("raw")
        return v

    pr = result.get("price", {}) or {}
    sd = result.get("summaryDetail", {}) or {}
    ks = result.get("defaultKeyStatistics", {}) or {}
    fd = result.get("financialData", {}) or {}

    info = {
        "shortName": _raw(pr, "shortName"),
        "longName": _raw(pr, "longName"),
        "currentPrice": _raw(pr, "regularMarketPrice") or _raw(fd, "currentPrice"),
        "previousClose": _raw(pr, "regularMarketPreviousClose") or _raw(sd, "previousClose"),
        "marketCap": _raw(pr, "marketCap"),
        "trailingPE": _raw(sd, "trailingPE"),
        "forwardPE": _raw(sd, "forwardPE"),
        "priceToBook": _raw(ks, "priceToBook"),
        "returnOnEquity": _raw(fd, "returnOnEquity"),
        "debtToEquity": _raw(fd, "debtToEquity"),
        "revenueGrowth": _raw(fd, "revenueGrowth"),
        "operatingMargins": _raw(fd, "operatingMargins"),
        "trailingEps": _raw(ks, "trailingEps"),
    }
    return {k: v for k, v in info.items() if v is not None}


def _reset_session():
    global _session, _crumb, _crumb_tried
    _session = None
    _crumb = None
    _crumb_tried = False


# yfinance.download 시그니처 호환 wrapper
def yf_download_compat(
    tickers,
    period: str = "1mo",
    interval: str = "1d",
    progress: bool = False,
    **kwargs,
) -> pd.DataFrame:
    """yfinance.download() 와 같은 시그니처로 호출 가능."""
    if isinstance(tickers, (list, tuple)):
        # 멀티 ticker — 첫번째만 처리 (현재 코드가 단일 ticker 위주)
        if not tickers:
            return pd.DataFrame()
        ticker = tickers[0]
    else:
        ticker = str(tickers)
    return download(ticker, period=period, interval=interval)


if __name__ == "__main__":
    # 빠른 동작 확인
    import sys
    t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    print(f"== chart: {t} ==")
    df = download(t, period="5d")
    print(df)
    print(f"\n== quote: {t} ==")
    q = get_quote(t)
    for k, v in q.items():
        print(f"  {k}: {v}")
