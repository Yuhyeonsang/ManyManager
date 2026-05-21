"""
네이버 금융 API 직통 — 한국 종목 가격/등락 폴백.

Yahoo Finance 가 rate limit / 응답 없음일 때 사용.
한국 종목 (.KS/.KQ) 전용 — KOSPI/KOSDAQ 데이터가 더 정확하고 빠름.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, Optional

import requests
import pandas as pd

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finance.naver.com/",
}


def _strip_kr_suffix(code: str) -> str:
    """005930.KS → 005930. KOSDAQ 의 .KQ 도 제거."""
    return str(code).replace(".KS", "").replace(".KQ", "").strip()


def _parse_num(s) -> Optional[float]:
    """문자열 가격 → float. '1,940,000' → 1940000.0, None/실패 → None"""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def get_realtime(code: str) -> Optional[Dict]:
    """
    실시간 현재가/등락/거래량.

    Parameters
    ----------
    code : '005930' / '005930.KS' / '403870.KQ' 등

    Returns
    -------
    dict with current_price, change, change_pct, open, high, low, volume, ...
    실패 시 None.
    """
    c = _strip_kr_suffix(code)
    try:
        r = requests.get(
            f"https://polling.finance.naver.com/api/realtime/domestic/stock/{c}",
            headers=_HEADERS, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.debug("Naver 폴링 실패 (%s): %s", code, e)
        return None

    datas = data.get("datas") or []
    if not datas:
        return None
    d = datas[0]

    # 등락 부호: code 2=상승, 5=하락, 3=보합
    cmp_code = (d.get("compareToPreviousPrice") or {}).get("code")
    sign = 1 if cmp_code == "2" else (-1 if cmp_code == "5" else 0)

    change_abs = _parse_num(d.get("compareToPreviousClosePrice"))
    if change_abs is not None and sign:
        change_abs = abs(change_abs) * sign

    fluctuations = _parse_num(d.get("fluctuationsRatio"))
    if fluctuations is not None and sign and fluctuations < 0 == False:
        # naver 가 부호 없이 절대값 줄 때 대응
        if sign == -1 and fluctuations > 0:
            fluctuations = -fluctuations

    return {
        "ticker": code,
        "code": d.get("itemCode"),
        "name": d.get("stockName"),
        "current_price": _parse_num(d.get("closePrice")),
        "open": _parse_num(d.get("openPrice")),
        "high": _parse_num(d.get("highPrice")),
        "low": _parse_num(d.get("lowPrice")),
        "change": change_abs,
        "change_pct": fluctuations,
        "volume": _parse_num(d.get("accumulatedTradingVolume")),
        "trading_value": _parse_num(d.get("accumulatedTradingValue")),
        "market_status": d.get("marketStatus"),
        "as_of": d.get("localTradedAt"),
    }


def get_history(code: str, period_days: int = 180) -> pd.DataFrame:
    """
    일봉 OHLCV — yfinance.Ticker.history(period) 의 KR 폴백.

    columns: Open, High, Low, Close, Volume
    index:   DatetimeIndex
    실패 시 빈 DataFrame.
    """
    c = _strip_kr_suffix(code)
    end = datetime.now()
    start = end - timedelta(days=period_days)
    url = "https://api.finance.naver.com/siseJson.naver"
    params = {
        "symbol": c,
        "requestType": "1",  # 1=일봉
        "startTime": start.strftime("%Y%m%d"),
        "endTime": end.strftime("%Y%m%d"),
        "timeframe": "day",
    }
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        text = r.text.strip()
    except Exception as e:
        logger.debug("Naver siseJson 실패 (%s): %s", code, e)
        return pd.DataFrame()

    # 네이버 siseJson 응답은 Python 리스트 리터럴 형식. ast.literal_eval 사용.
    try:
        import ast
        rows = ast.literal_eval(text)
    except Exception as e:
        logger.debug("Naver siseJson 파싱 실패 (%s): %s", code, e)
        return pd.DataFrame()

    if not rows or len(rows) < 2:
        return pd.DataFrame()

    # 첫 행은 헤더, 나머지는 데이터
    header = rows[0]
    data = rows[1:]
    df = pd.DataFrame(data, columns=header)

    # 한글 컬럼 → 영문 매핑
    col_map = {
        "날짜": "Date", "시가": "Open", "고가": "High",
        "저가": "Low", "종가": "Close", "거래량": "Volume",
    }
    df.rename(columns=col_map, inplace=True)

    if "Date" not in df.columns:
        return pd.DataFrame()

    df["Date"] = pd.to_datetime(df["Date"].astype(str), format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["Date"]).set_index("Date")

    # 필요한 컬럼만, 숫자형으로
    out_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[out_cols].apply(pd.to_numeric, errors="coerce").dropna(how="all")
    return df


if __name__ == "__main__":
    import sys, json
    code = sys.argv[1] if len(sys.argv) > 1 else "000660"
    print(f"== realtime: {code} ==")
    rt = get_realtime(code)
    print(json.dumps(rt, ensure_ascii=False, indent=2))
    print(f"\n== history (30d): {code} ==")
    h = get_history(code, period_days=30)
    print(h.tail(10) if not h.empty else "EMPTY")
