"""
네이버 금융 API 직통.
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


def _strip_kr_suffix(code):
    return str(code).replace(".KS", "").replace(".KQ", "").strip()


def _parse_num(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    try:
        return float(str(s).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def get_realtime(code):
    c = _strip_kr_suffix(code)
    try:
        r = requests.get(
            f"https://polling.finance.naver.com/api/realtime/domestic/stock/{c}",
            headers=_HEADERS, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.debug("Naver realtime fail (%s): %s", code, e)
        return None
    datas = data.get("datas") or []
    if not datas:
        return None
    d = datas[0]
    cmp_code = (d.get("compareToPreviousPrice") or {}).get("code")
    sign = 1 if cmp_code == "2" else (-1 if cmp_code == "5" else 0)
    change_abs = _parse_num(d.get("compareToPreviousClosePrice"))
    if change_abs is not None and sign:
        change_abs = abs(change_abs) * sign
    fluctuations = _parse_num(d.get("fluctuationsRatio"))
    if fluctuations is not None and sign:
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


def get_history(code, period_days=180):
    c = _strip_kr_suffix(code)
    end = datetime.now()
    start = end - timedelta(days=period_days)
    url = "https://api.finance.naver.com/siseJson.naver"
    params = {
        "symbol": c, "requestType": "1",
        "startTime": start.strftime("%Y%m%d"),
        "endTime": end.strftime("%Y%m%d"),
        "timeframe": "day",
    }
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        r.raise_for_status()
        text = r.text.strip()
    except Exception as e:
        logger.debug("Naver siseJson fail (%s): %s", code, e)
        return pd.DataFrame()
    try:
        import ast
        rows = ast.literal_eval(text)
    except Exception:
        return pd.DataFrame()
    if not rows or len(rows) < 2:
        return pd.DataFrame()
    header = rows[0]
    data = rows[1:]
    df = pd.DataFrame(data, columns=header)
    col_map = {"날짜": "Date", "시가": "Open", "고가": "High",
               "저가": "Low", "종가": "Close", "거래량": "Volume"}
    df.rename(columns=col_map, inplace=True)
    if "Date" not in df.columns:
        return pd.DataFrame()
    df["Date"] = pd.to_datetime(df["Date"].astype(str), format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["Date"]).set_index("Date")
    out_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    df = df[out_cols].apply(pd.to_numeric, errors="coerce")
    if "Close" in df.columns:
        df = df.dropna(subset=["Close"])
    df = df.sort_index()
    return df


def get_summary(code):
    """네이버 종목 페이지 스크래핑 - pykrx/KRX/DART 폴백 실패시 최후 수단."""
    c = _strip_kr_suffix(code)
    if not c.isdigit() or len(c) != 6:
        return None
    try:
        r = requests.get(
            f"https://finance.naver.com/item/main.naver?code={c}",
            headers=_HEADERS, timeout=10,
        )
        r.raise_for_status()
        if r.encoding and r.encoding.lower() in ("iso-8859-1", "ascii"):
            r.encoding = r.apparent_encoding or "euc-kr"
        html = r.text
    except Exception as e:
        logger.debug("Naver summary fail (%s): %s", code, e)
        return None

    def _p(s):
        if not s:
            return None
        s = str(s).replace(",", "").replace("%", "").replace("배", "").strip()
        if s in ("", "-", "N/A"):
            return None
        try:
            v = float(s)
            return v if v != 0 else None
        except ValueError:
            return None

    def _by_id(idname):
        m = re.search(r'id="' + re.escape(idname) + r'"[^>]*>([^<]+)<', html)
        return _p(m.group(1)) if m else None

    out = {
        "per": _by_id("_per"),
        "pbr": _by_id("_pbr"),
        "eps": _by_id("_eps"),
        "bps": _by_id("_bps"),
        "dividend_yield_pct": _by_id("_dvd_yld_for_yr"),
    }

    # ★ 재무 분석 탭 API에서 영업이익률·ROE·매출성장률 추가 시도
    try:
        fin_r = requests.get(
            f"https://finance.naver.com/item/coinfo.naver?code={c}&target=finsum_more",
            headers=_HEADERS, timeout=10,
        )
        if fin_r.status_code == 200:
            if fin_r.encoding and fin_r.encoding.lower() in ("iso-8859-1", "ascii"):
                fin_r.encoding = fin_r.apparent_encoding or "euc-kr"
            fin_html = fin_r.text

            # 영업이익률: 테이블에서 "영업이익률" 행의 최신 값
            m_op = re.search(
                r'영업이익률[^<]*</th>.*?<td[^>]*>\s*([0-9,.\-]+)\s*</td>',
                fin_html, re.S,
            )
            if m_op:
                out["operating_margin_pct"] = _p(m_op.group(1))

            # ROE: "ROE" 행
            m_roe = re.search(
                r'>ROE[^<]*</th>.*?<td[^>]*>\s*([0-9,.\-]+)\s*</td>',
                fin_html, re.S,
            )
            if m_roe:
                out["roe_pct"] = _p(m_roe.group(1))

            # 매출액 증가율
            m_rev = re.search(
                r'매출액\s*증가율[^<]*</th>.*?<td[^>]*>\s*([0-9,.\-]+)\s*</td>',
                fin_html, re.S,
            )
            if m_rev:
                out["revenue_growth_pct"] = _p(m_rev.group(1))
    except Exception as e:
        logger.debug("Naver finsum fail (%s): %s", code, e)

    if any(v is not None for v in out.values()):
        logger.info("Naver summary OK %s: %s", code, out)
        return out
    return None


if __name__ == "__main__":
    import sys, json
    code = sys.argv[1] if len(sys.argv) > 1 else "000660"
    print(json.dumps(get_realtime(code), ensure_ascii=False, indent=2))
    print(json.dumps(get_summary(code), ensure_ascii=False, indent=2))
