"""
yfinance 우회 모듈 — Yahoo Finance Chart API 직통 호출

Pi 3B+ 환경에서 yfinance 0.2.50/0.2.54가 curl_cffi 부재로
크럼(crumb) 인증에 실패하는 문제 해결용.

curl로는 HTTP 200 잘 받아지므로, 동일한 헤더로 requests 호출.
"""
import logging
from typing import Dict, Optional, List

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# 실제 브라우저처럼 보이는 헤더 — Yahoo 봇 검출 우회
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Safari/605.1.15"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
}

# 세션 + 크럼 캐시 (프로세스 동안 재사용)
_session: Optional[requests.Session] = None
_crumb: Optional[str] = None


def _get_session() -> requests.Session:
    global _session, _crumb
    if _session is not None:
        return _session
    s = requests.Session()
    s.headers.update(_HEADERS)
    # 1) 야후 쿠키 받기
    try:
        s.get("https://fc.yahoo.com/", timeout=10)
    except Exception as e:
        logger.warning("fc.yahoo.com warm-up 실패: %s", e)
    # 2) 크럼 받기
    try:
        r = s.get(
            "https://query1.finance.yahoo.com/v1/test/getcrumb",
            timeout=10,
        )
        if r.status_code == 200 and r.text:
            _crumb = r.text.strip()
    except Exception as e:
        logger.warning("crumb 획득 실패: %s", e)
    _session = s
    return s


def download(
    ticker: str,
    period: str = "1mo",
    interval: str = "1d",
) -> pd.DataFrame:
    """
    yfinance.download() 대체.
    Yahoo Chart API (crumb 불필요) 직통.

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
        logger.warning("%s chart 요청 실패: %s", ticker, e)
        return pd.DataFrame()

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
        # NaN 행 제거
        df = df.dropna(how="all")
        return df
    except (KeyError, IndexError, TypeError) as e:
        logger.warning("%s chart 파싱 실패: %s", ticker, e)
        return pd.DataFrame()


def get_quote(ticker: str) -> Dict:
    """
    현재가/시총/PER/PBR 등 기본 시세 정보 (crumb 필요).
    실패 시 빈 dict.
    """
    s = _get_session()
    if not _crumb:
        # crumb 없으면 quoteSummary는 못 씀
        return {}
    modules = ",".join([
        "price", "summaryDetail",
        "defaultKeyStatistics", "financialData",
    ])
    url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
    params = {"modules": modules, "crumb": _crumb}
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
    global _session, _crumb
    _session = None
    _crumb = None


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
