import os
import logging
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# yfinance 폴백 — Pi 3B+ 에서 curl_cffi 부재로 yfinance 가
# Yahoo crumb 인증을 못 받는 경우, 직접 Chart API 호출.
# ─────────────────────────────────────────────────────────────
try:
    import yahoo_direct as _yd  # type: ignore
    _YD_AVAILABLE = True
except ImportError:
    _YD_AVAILABLE = False
    log.warning("yahoo_direct 미설치 — yfinance 폴백 비활성")

try:
    import naver_finance as _naver  # type: ignore
    _NAVER_AVAILABLE = True
except ImportError:
    _NAVER_AVAILABLE = False
    log.warning("naver_finance 미설치 — KR 폴백 비활성")


def _is_kr_ticker(ticker: str) -> bool:
    t = (ticker or "").upper()
    return t.endswith(".KS") or t.endswith(".KQ")


def _yf_history_safe(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """가격 일봉 데이터.
    KR 종목(.KS/.KQ): Naver 직행 → 실패 시 yfinance/yahoo_direct 폴백.
    US 종목: yfinance → yahoo_direct 순.
    ─ Yahoo 429 rate-limit 차단 대응 (2024~): KR 호출량을 0으로 줄여서
      US 종목 야후 호출이 차단 임계치를 안 넘게 함.
    """
    is_kr = _is_kr_ticker(ticker)

    # KR 종목: Naver 우선
    if is_kr and _NAVER_AVAILABLE:
        try:
            period_days = _period_to_days(period)
            df = _naver.get_history(ticker, period_days=period_days)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            log.debug(f"naver_finance history 실패 ({ticker}): {e}")

    # US 종목, 또는 KR 인데 Naver 가 실패한 경우
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period=period, auto_adjust=True)
        if hist is not None and not hist.empty:
            return hist
    except Exception as e:
        log.debug(f"yfinance history 실패 ({ticker}): {e}")
    if _YD_AVAILABLE:
        try:
            df = _yd.download(ticker, period=period)
            if df is not None and not df.empty:
                # ⚠️ Yahoo Chart API 의 "close" 는 이미 분할 조정됨 (auto_adjust=True 와 유사).
                # 반면 "adjclose" 는 배당까지 누적 적용되어 한국 고배당주는 3~5배 부풀려짐.
                # 따라서 그대로 두고 raw close 를 사용한다.
                return df
        except Exception as e:
            log.debug(f"yahoo_direct fallback 실패 ({ticker}): {e}")
    return pd.DataFrame()


def _period_to_days(period: str) -> int:
    """yfinance period 문자열 → 일수. '6mo' → 180, '1y' → 365"""
    p = (period or "").lower().strip()
    if p.endswith("d"):
        try: return int(p[:-1])
        except: return 30
    if p.endswith("mo"):
        try: return int(p[:-2]) * 30
        except: return 180
    if p.endswith("y"):
        try: return int(p[:-1]) * 365
        except: return 365
    return 180


def _yf_info_safe(ticker: str) -> Dict:
    """yfinance.Ticker.info 우선, 실패 시 yahoo_direct.get_quote 폴백.
    KR 종목은 야후 info 안 씀 — pykrx/KRX 직통이 더 정확하고 차단 위험 0.
    (collect_all 의 KRX/pykrx 폴백이 PER/PBR/ROE/시총 채워줌)
    """
    if _is_kr_ticker(ticker):
        return {}
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        if info and (info.get("trailingPE") is not None or info.get("currentPrice") is not None):
            return info
    except Exception as e:
        log.debug(f"yfinance info 실패 ({ticker}): {e}")
    if _YD_AVAILABLE:
        try:
            q = _yd.get_quote(ticker) or {}
            return q
        except Exception as e:
            log.debug(f"yahoo_direct quote 실패 ({ticker}): {e}")
    return {}

# ─────────────────────────────────────────────────────────────
# pykrx 벌크 fundamental 캐시 (KRX 전체 종목 PER/PBR/EPS/BPS)
#   - KRX 직통 API 가 응답 안 줄 때의 폴백
#   - 프로세스 시작 후 첫 호출 시 1~2초 (KRX 전체 ~2600개 한 번에 받음)
#   - 그 후엔 메모리에서 즉시 조회 (microsecond)
#   - 날짜 바뀌면 자동 재로드
# ─────────────────────────────────────────────────────────────
try:
    from pykrx import stock as pykrx_stock  # type: ignore
    _PYKRX_AVAILABLE = True
except ImportError:
    _PYKRX_AVAILABLE = False
    log.warning("pykrx 미설치 — 코스닥 소형주 PER/PBR 폴백 비활성")


_pykrx_fund_cache: Optional[pd.DataFrame] = None
_pykrx_fund_cache_date: Optional[str] = None


def get_pykrx_fundamentals() -> Optional[pd.DataFrame]:
    """KRX 전체 종목의 PER/PBR/EPS/BPS/DIV/DPS DataFrame.
    인덱스는 종목코드(문자열 6자리). 하루 1회 자동 새로 로드.
    휴장일(주말·공휴일)에는 직전 거래일로 자동 폴백.
    실패 시 None."""
    global _pykrx_fund_cache, _pykrx_fund_cache_date
    if not _PYKRX_AVAILABLE:
        return None
    today = datetime.now().strftime("%Y%m%d")
    if _pykrx_fund_cache is not None and _pykrx_fund_cache_date == today:
        return _pykrx_fund_cache
    # 휴장일/주말 대비 — 최근 10일 거슬러 가며 시도
    for delta in range(0, 10):
        trd = (datetime.now() - timedelta(days=delta)).strftime("%Y%m%d")
        try:
            df = pykrx_stock.get_market_fundamental(trd, market="ALL")
            if df is None or len(df) == 0:
                continue
            # ★ 휴장일 검증 — pykrx 는 휴장일에도 모든 종목 0으로 채운 df 를 반환함.
            #    PER>0 인 종목이 전체의 10% 이상 있어야 정상 거래일로 본다.
            try:
                non_zero = int((df["PER"] > 0).sum())
            except Exception:
                non_zero = 0
            if non_zero < len(df) * 0.1:
                log.info(
                    f"pykrx {trd}: 휴장일 추정 (PER>0: {non_zero}/{len(df)}) — 직전 거래일 시도"
                )
                continue
            _pykrx_fund_cache = df
            _pykrx_fund_cache_date = today
            log.info(f"pykrx fundamental 로드: {len(df)}종목 (기준일 {trd})")
            return df
        except Exception as e:
            log.warning(f"pykrx 호출 실패 ({trd}): {e}")
    return None


class StockDataCollector:
    def __init__(
        self,
        naver_client_id: Optional[str] = None,
        naver_client_secret: Optional[str] = None,
        dart_api_key: Optional[str] = None,
    ):
        self.naver_client_id = naver_client_id or os.getenv("NAVER_CLIENT_ID")
        self.naver_client_secret = naver_client_secret or os.getenv("NAVER_CLIENT_SECRET")
        self.dart_api_key = dart_api_key or os.getenv("DART_API_KEY")
        self._corp_code_cache: Dict[str, str] = {}

    def get_kr_fundamentals_pykrx(self, stock_code: str) -> Optional[Dict]:
        """pykrx 로 단일 종목 PER/PBR/EPS/BPS 받음 (벌크 캐시에서 조회).
        코스피/코스닥 가리지 않고 KRX 상장 전 종목 커버."""
        if not stock_code or not stock_code.isdigit() or len(stock_code) != 6:
            return None
        df = get_pykrx_fundamentals()
        if df is None or stock_code not in df.index:
            return None
        try:
            row = df.loc[stock_code]
            def _safe(v):
                try:
                    if v is None or pd.isna(v):
                        return None
                    f = float(v)
                    return f if f != 0 else None
                except Exception:
                    return None
            return {
                "stock_code": stock_code,
                "per": _safe(row.get("PER")),
                "pbr": _safe(row.get("PBR")),
                "eps": _safe(row.get("EPS")),
                "bps": _safe(row.get("BPS")),
                "div_yield_pct": _safe(row.get("DIV")),
                "dps": _safe(row.get("DPS")),
            }
        except Exception as e:
            log.warning(f"pykrx 조회 실패 ({stock_code}): {e}")
            return None

    def get_price_data(
        self,
        ticker: str,
        period: str = "6mo",
        ma_windows: List[int] = [5, 20, 60, 120],
    ) -> Dict:
        try:
            hist = _yf_history_safe(ticker, period=period)
            if hist.empty:
                return {"ticker": ticker, "error": "no price data"}

            # NaN 행 제거 — 일부 소스(Naver siseJson)는 trailing 빈 row 가 있음
            close = hist["Close"].dropna()
            if len(close) == 0:
                return {"ticker": ticker, "error": "no valid close price"}
            current_price = float(close.iloc[-1])
            prev_close = float(close.iloc[-2]) if len(close) > 1 else current_price
            change = current_price - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0

            ma = {}
            for w in ma_windows:
                if len(close) >= w:
                    ma[f"MA{w}"] = round(float(close.rolling(w).mean().iloc[-1]), 2)
                else:
                    ma[f"MA{w}"] = None

            high_52w = float(close.tail(252).max()) if len(close) > 0 else None
            low_52w = float(close.tail(252).min()) if len(close) > 0 else None
            avg_volume = int(hist["Volume"].tail(20).mean()) if "Volume" in hist else None

            recent = [
                {
                    "date": idx.strftime("%Y-%m-%d"),
                    "close": round(float(row["Close"]), 2),
                    "volume": int(row["Volume"]) if pd.notna(row["Volume"]) else 0,
                }
                for idx, row in hist.tail(10).iterrows()
            ]

            return {
                "ticker": ticker,
                "as_of": hist.index[-1].strftime("%Y-%m-%d"),
                "current_price": round(current_price, 2),
                "prev_close": round(prev_close, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "moving_averages": ma,
                "high_52w": round(high_52w, 2) if high_52w else None,
                "low_52w": round(low_52w, 2) if low_52w else None,
                "avg_volume_20d": avg_volume,
                "recent_10d": recent,
            }
        except Exception as e:
            return {"ticker": ticker, "error": str(e)}

    def get_news_data(
        self,
        query: str,
        display: int = 10,
        sort: str = "date",
    ) -> Dict:
        if not (self.naver_client_id and self.naver_client_secret):
            return {"query": query, "error": "missing naver api credentials"}
        try:
            url = "https://openapi.naver.com/v1/search/news.json"
            headers = {
                "X-Naver-Client-Id": self.naver_client_id,
                "X-Naver-Client-Secret": self.naver_client_secret,
            }
            params = {"query": query, "display": display, "sort": sort}
            r = requests.get(url, headers=headers, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()

            def clean(s: str) -> str:
                return (
                    s.replace("<b>", "")
                    .replace("</b>", "")
                    .replace("&quot;", '"')
                    .replace("&amp;", "&")
                    .replace("&lt;", "<")
                    .replace("&gt;", ">")
                    .strip()
                )

            items = [
                {
                    "title": clean(it.get("title", "")),
                    "description": clean(it.get("description", "")),
                    "link": it.get("originallink") or it.get("link", ""),
                    "pub_date": it.get("pubDate", ""),
                }
                for it in data.get("items", [])
            ]

            return {
                "query": query,
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "total": data.get("total", len(items)),
                "count": len(items),
                "items": items,
            }
        except Exception as e:
            return {"query": query, "error": str(e)}

    def _load_corp_code(self, stock_code: str) -> Optional[str]:
        if stock_code in self._corp_code_cache:
            return self._corp_code_cache[stock_code]
        try:
            import zipfile, io, xml.etree.ElementTree as ET
            url = "https://opendart.fss.or.kr/api/corpCode.xml"
            r = requests.get(url, params={"crtfc_key": self.dart_api_key}, timeout=20)
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                with z.open(z.namelist()[0]) as f:
                    tree = ET.parse(f)
            for node in tree.iter("list"):
                sc = (node.findtext("stock_code") or "").strip()
                if sc:
                    self._corp_code_cache[sc] = node.findtext("corp_code").strip()
            return self._corp_code_cache.get(stock_code)
        except Exception:
            return None

    def get_financial_statements(
        self,
        stock_code: str,
        year: Optional[int] = None,
        report_code: str = "11011",
    ) -> Dict:
        if not self.dart_api_key:
            return {"stock_code": stock_code, "error": "missing dart api key"}
        try:
            corp_code = self._load_corp_code(stock_code)
            if not corp_code:
                return {"stock_code": stock_code, "error": "corp_code not found"}

            year = year or (datetime.now().year - 1)
            url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
            params = {
                "crtfc_key": self.dart_api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": report_code,
                "fs_div": "CFS",
            }
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()

            if data.get("status") != "000":
                params["fs_div"] = "OFS"
                r = requests.get(url, params=params, timeout=15)
                data = r.json()
                if data.get("status") != "000":
                    return {
                        "stock_code": stock_code,
                        "year": year,
                        "error": f"dart {data.get('status')}: {data.get('message')}",
                    }

            targets = {
                "매출액": "revenue",
                "영업이익": "operating_income",
                "당기순이익": "net_income",
                "자산총계": "total_assets",
                "부채총계": "total_liabilities",
                "자본총계": "total_equity",
            }

            def to_num(s: str) -> Optional[int]:
                if not s:
                    return None
                try:
                    return int(s.replace(",", ""))
                except Exception:
                    return None

            result: Dict[str, Dict] = {}
            for item in data.get("list", []):
                name = item.get("account_nm", "").strip()
                if name in targets:
                    key = targets[name]
                    if key in result:
                        continue
                    result[key] = {
                        "current": to_num(item.get("thstrm_amount")),
                        "previous": to_num(item.get("frmtrm_amount")),
                        "before_previous": to_num(item.get("bfefrmtrm_amount")),
                        "fs_name": item.get("sj_nm"),
                    }

            rev = result.get("revenue", {}).get("current")
            op = result.get("operating_income", {}).get("current")
            ni = result.get("net_income", {}).get("current")
            eq = result.get("total_equity", {}).get("current")
            li = result.get("total_liabilities", {}).get("current")
            assets = result.get("total_assets", {}).get("current")

            ratios = {
                "operating_margin_pct": round(op / rev * 100, 2) if rev and op else None,
                "net_margin_pct": round(ni / rev * 100, 2) if rev and ni else None,
                "roe_pct": round(ni / eq * 100, 2) if ni and eq else None,
                "roa_pct": round(ni / assets * 100, 2) if ni and assets else None,
                "debt_to_equity_pct": round(li / eq * 100, 2) if li and eq else None,
            }

            ni_prev = result.get("net_income", {}).get("previous")
            rev_prev = result.get("revenue", {}).get("previous")
            growth = {
                "revenue_yoy_pct": round((rev - rev_prev) / rev_prev * 100, 2) if rev and rev_prev else None,
                "net_income_yoy_pct": round((ni - ni_prev) / ni_prev * 100, 2) if ni and ni_prev else None,
            }

            return {
                "stock_code": stock_code,
                "corp_code": corp_code,
                "year": year,
                "report_code": report_code,
                "currency": "KRW",
                "indicators": result,
                "ratios": ratios,
                "growth": growth,
            }
        except Exception as e:
            return {"stock_code": stock_code, "error": str(e)}

    def get_kr_market_cap(self, stock_code: str) -> Optional[Dict]:
        """KRX 직통 API 에서 KR 종목의 시총·발행주식수·BPS·PER·PBR 가져옴.
        yfinance 가 못 채워주는 코스닥 소형주 폴백."""
        if not stock_code or not stock_code.isdigit() or len(stock_code) != 6:
            return None
        try:
            url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
            today = datetime.now().strftime("%Y%m%d")
            for delta in range(0, 7):
                trd = (datetime.now() - timedelta(days=delta)).strftime("%Y%m%d")
                params = {
                    "bld": "dbms/MDC/STAT/standard/MDCSTAT03501",
                    "tboxisuCd_finder_stkisu0_0": f"{stock_code}/",
                    "isuCd": f"KR7{stock_code}000",
                    "isuCd2": f"KR7{stock_code}000",
                    "codeNmisuCd_finder_stkisu0_0": "",
                    "param1isuCd_finder_stkisu0_0": "ALL",
                    "strtDd": trd,
                    "endDd": trd,
                    "share": "1",
                    "money": "1",
                    "csvxls_isNo": "false",
                }
                r = requests.post(
                    url,
                    data=params,
                    headers={
                        "Referer": "http://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd",
                        "User-Agent": "Mozilla/5.0",
                    },
                    timeout=10,
                )
                rows = r.json().get("output", []) if r.ok else []
                if rows:
                    row = rows[0]
                    def _i(k):
                        try:
                            return int(str(row.get(k, "")).replace(",", ""))
                        except Exception:
                            return None
                    def _f(k):
                        try:
                            v = str(row.get(k, "")).replace(",", "")
                            return float(v) if v not in ("", "-") else None
                        except Exception:
                            return None
                    return {
                        "stock_code": stock_code,
                        "trade_date": trd,
                        "close": _i("TDD_CLSPRC"),
                        "market_cap": _i("MKTCAP"),
                        "shares_outstanding": _i("LIST_SHRS"),
                        "per": _f("PER"),
                        "pbr": _f("PBR"),
                        "eps": _i("EPS"),
                        "bps": _i("BPS"),
                    }
            return None
        except Exception:
            return None

    def get_market_metrics(self, ticker: str) -> Dict:
        """yfinance.info 에서 PER / PBR / ROE / 시가총액 등 시장 지표를 가져온다.
        DART 만으로는 안 잡히는 PER·PBR 을 채우기 위한 보조 소스.
        """
        try:
            info = _yf_info_safe(ticker)

            def _round(x, n=2):
                try:
                    if x is None:
                        return None
                    return round(float(x), n)
                except Exception:
                    return None

            per = info.get("trailingPE") or info.get("forwardPE")
            pbr = info.get("priceToBook")
            roe = info.get("returnOnEquity")
            roe_pct = _round(roe * 100, 2) if isinstance(roe, (int, float)) else None
            div_yield = info.get("dividendYield")
            div_yield_pct = (
                _round(div_yield * 100, 2)
                if isinstance(div_yield, (int, float)) and div_yield < 1
                else _round(div_yield, 2)
            )

            return {
                "ticker": ticker,
                "per": _round(per, 2),
                "pbr": _round(pbr, 2),
                "roe_pct": roe_pct,
                "market_cap": info.get("marketCap"),
                "dividend_yield_pct": div_yield_pct,
                "beta": _round(info.get("beta"), 2),
                "currency": info.get("currency"),
                "exchange": info.get("exchange"),
                "short_name": info.get("shortName") or info.get("longName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
            }
        except Exception as e:
            return {"ticker": ticker, "error": str(e)}

    def collect_all(
        self,
        ticker: str,
        news_query: Optional[str] = None,
        stock_code: Optional[str] = None,
        year: Optional[int] = None,
    ) -> Dict:
        price = self.get_price_data(ticker)
        news = self.get_news_data(news_query) if news_query else None
        fin = self.get_financial_statements(stock_code, year) if stock_code else None
        mm = self.get_market_metrics(ticker)

        # KR 종목이고 yfinance 가 PER/PBR/시총을 못 채웠으면 KRX 직통 폴백
        if stock_code and isinstance(mm, dict):
            need_krx = (
                mm.get("market_cap") is None
                or mm.get("per") is None
                or mm.get("pbr") is None
            )
            if need_krx:
                krx = self.get_kr_market_cap(stock_code)
                if krx:
                    if mm.get("market_cap") is None and krx.get("market_cap"):
                        mm["market_cap"] = krx["market_cap"]
                        mm["market_cap_source"] = "krx"
                    if mm.get("per") is None and krx.get("per") is not None:
                        mm["per"] = krx["per"]
                        mm["per_source"] = "krx"
                    if mm.get("pbr") is None and krx.get("pbr") is not None:
                        mm["pbr"] = krx["pbr"]
                        mm["pbr_source"] = "krx"
                    mm["eps"] = krx.get("eps")
                    mm["bps"] = krx.get("bps")
                    mm["shares_outstanding"] = krx.get("shares_outstanding")

        # KRX 직통도 못 채웠으면 pykrx 폴백 (코스닥 소형주 강함)
        if stock_code and isinstance(mm, dict):
            still_need = mm.get("per") is None or mm.get("pbr") is None
            if still_need:
                pk = self.get_kr_fundamentals_pykrx(stock_code)
                if pk:
                    if mm.get("per") is None and pk.get("per") is not None:
                        mm["per"] = pk["per"]
                        mm["per_source"] = "pykrx"
                    if mm.get("pbr") is None and pk.get("pbr") is not None:
                        mm["pbr"] = pk["pbr"]
                        mm["pbr_source"] = "pykrx"
                    if mm.get("eps") is None and pk.get("eps") is not None:
                        mm["eps"] = pk["eps"]
                    if mm.get("bps") is None and pk.get("bps") is not None:
                        mm["bps"] = pk["bps"]
                    if mm.get("dividend_yield_pct") is None and pk.get("div_yield_pct") is not None:
                        mm["dividend_yield_pct"] = pk["div_yield_pct"]
                        mm["dividend_yield_source"] = "pykrx"

        if fin and "ratios" in fin and isinstance(mm, dict):
            r = fin["ratios"]
            if mm.get("roe_pct") is None and r.get("roe_pct") is not None:
                mm["roe_pct"] = r["roe_pct"]
                mm["roe_source"] = "dart_calc"
            mc = mm.get("market_cap")
            ni = fin["indicators"].get("net_income", {}).get("current")
            eq = fin["indicators"].get("total_equity", {}).get("current")
            if mm.get("per") is None and mc and ni and ni > 0:
                mm["per"] = round(mc / ni, 2)
                mm["per_source"] = "dart_calc"
            if mm.get("pbr") is None and mc and eq and eq > 0:
                mm["pbr"] = round(mc / eq, 2)
                mm["pbr_source"] = "dart_calc"

        return {
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "price": price,
            "news": news,
            "financials": fin,
            "market_metrics": mm,
        }


# ─────────────────────────────────────────────
# 국장 / 미국장 종목 마스터 + 동적 핫 종목
# ─────────────────────────────────────────────

KR_STOCK_UNIVERSE: List[Dict] = [
    # 코스피 시총 상위
    {"code": "005930", "name": "삼성전자", "market": "KOSPI"},
    {"code": "000660", "name": "SK하이닉스", "market": "KOSPI"},
    {"code": "373220", "name": "LG에너지솔루션", "market": "KOSPI"},
    {"code": "207940", "name": "삼성바이오로직스", "market": "KOSPI"},
    {"code": "005380", "name": "현대차", "market": "KOSPI"},
    {"code": "005490", "name": "POSCO홀딩스", "market": "KOSPI"},
    {"code": "000270", "name": "기아", "market": "KOSPI"},
    {"code": "035420", "name": "NAVER", "market": "KOSPI"},
    {"code": "035720", "name": "카카오", "market": "KOSPI"},
    {"code": "068270", "name": "셀트리온", "market": "KOSPI"},
    {"code": "051910", "name": "LG화학", "market": "KOSPI"},
    {"code": "006400", "name": "삼성SDI", "market": "KOSPI"},
    {"code": "012330", "name": "현대모비스", "market": "KOSPI"},
    {"code": "028260", "name": "삼성물산", "market": "KOSPI"},
    {"code": "066570", "name": "LG전자", "market": "KOSPI"},
    {"code": "003670", "name": "포스코퓨처엠", "market": "KOSPI"},
    {"code": "017670", "name": "SK텔레콤", "market": "KOSPI"},
    {"code": "030200", "name": "KT", "market": "KOSPI"},
    {"code": "015760", "name": "한국전력", "market": "KOSPI"},
    {"code": "034730", "name": "SK", "market": "KOSPI"},
    {"code": "009150", "name": "삼성전기", "market": "KOSPI"},
    {"code": "010130", "name": "고려아연", "market": "KOSPI"},
    {"code": "086790", "name": "하나금융지주", "market": "KOSPI"},
    {"code": "055550", "name": "신한지주", "market": "KOSPI"},
    {"code": "105560", "name": "KB금융", "market": "KOSPI"},
    {"code": "316140", "name": "우리금융지주", "market": "KOSPI"},
    {"code": "024110", "name": "기업은행", "market": "KOSPI"},
    {"code": "032830", "name": "삼성생명", "market": "KOSPI"},
    {"code": "323410", "name": "카카오뱅크", "market": "KOSPI"},
    {"code": "377300", "name": "카카오페이", "market": "KOSPI"},
    {"code": "036570", "name": "엔씨소프트", "market": "KOSPI"},
    {"code": "251270", "name": "넷마블", "market": "KOSPI"},
    {"code": "352820", "name": "하이브", "market": "KOSPI"},
    {"code": "041510", "name": "에스엠", "market": "KOSDAQ"},
    {"code": "035900", "name": "JYP Ent.", "market": "KOSDAQ"},
    {"code": "122870", "name": "와이지엔터테인먼트", "market": "KOSDAQ"},
    # 코스닥 시총·테마 상위
    {"code": "247540", "name": "에코프로비엠", "market": "KOSDAQ"},
    {"code": "086520", "name": "에코프로", "market": "KOSDAQ"},
    {"code": "091990", "name": "셀트리온헬스케어", "market": "KOSDAQ"},
    {"code": "196170", "name": "알테오젠", "market": "KOSDAQ"},
    {"code": "263750", "name": "펄어비스", "market": "KOSDAQ"},
    {"code": "293490", "name": "카카오게임즈", "market": "KOSDAQ"},
    {"code": "112040", "name": "위메이드", "market": "KOSDAQ"},
    {"code": "058470", "name": "리노공업", "market": "KOSDAQ"},
    {"code": "240810", "name": "원익IPS", "market": "KOSDAQ"},
    {"code": "095340", "name": "ISC", "market": "KOSDAQ"},
    {"code": "066970", "name": "엘앤에프", "market": "KOSDAQ"},
    {"code": "278280", "name": "천보", "market": "KOSDAQ"},
    {"code": "121600", "name": "나노신소재", "market": "KOSDAQ"},
    {"code": "214450", "name": "파마리서치", "market": "KOSDAQ"},
    {"code": "328130", "name": "루닛", "market": "KOSDAQ"},
    {"code": "078340", "name": "컴투스", "market": "KOSDAQ"},
    {"code": "067310", "name": "하나마이크론", "market": "KOSDAQ"},
    {"code": "403870", "name": "HPSP", "market": "KOSDAQ"},
    {"code": "418550", "name": "제이앤티씨", "market": "KOSDAQ"},
    {"code": "357780", "name": "솔브레인", "market": "KOSDAQ"},
    # 중형주
    {"code": "267260", "name": "HD현대일렉트릭", "market": "KOSPI"},
    {"code": "010620", "name": "현대미포조선", "market": "KOSPI"},
    {"code": "042660", "name": "한화오션", "market": "KOSPI"},
    {"code": "329180", "name": "HD현대중공업", "market": "KOSPI"},
    {"code": "047810", "name": "한국항공우주", "market": "KOSPI"},
    {"code": "012450", "name": "한화에어로스페이스", "market": "KOSPI"},
    {"code": "272210", "name": "한화시스템", "market": "KOSPI"},
    {"code": "010140", "name": "삼성중공업", "market": "KOSPI"},
    {"code": "180640", "name": "한진칼", "market": "KOSPI"},
    {"code": "003490", "name": "대한항공", "market": "KOSPI"},
    {"code": "028050", "name": "삼성E&A", "market": "KOSPI"},
    {"code": "010950", "name": "S-Oil", "market": "KOSPI"},
    {"code": "096770", "name": "SK이노베이션", "market": "KOSPI"},
    {"code": "267250", "name": "HD현대", "market": "KOSPI"},
    {"code": "402340", "name": "SK스퀘어", "market": "KOSPI"},
    {"code": "259960", "name": "크래프톤", "market": "KOSPI"},
    {"code": "138040", "name": "메리츠금융지주", "market": "KOSPI"},
    {"code": "001040", "name": "CJ", "market": "KOSPI"},
    {"code": "097950", "name": "CJ제일제당", "market": "KOSPI"},
    {"code": "033780", "name": "KT&G", "market": "KOSPI"},
    {"code": "271560", "name": "오리온", "market": "KOSPI"},
    {"code": "139480", "name": "이마트", "market": "KOSPI"},
    {"code": "023530", "name": "롯데쇼핑", "market": "KOSPI"},
    {"code": "004020", "name": "현대제철", "market": "KOSPI"},
    {"code": "010120", "name": "LS ELECTRIC", "market": "KOSPI"},
    {"code": "006260", "name": "LS", "market": "KOSPI"},
]

US_STOCK_UNIVERSE: List[Dict] = [
    {"code": "AAPL", "name": "Apple", "market": "NASDAQ"},
    {"code": "MSFT", "name": "Microsoft", "market": "NASDAQ"},
    {"code": "GOOGL", "name": "Alphabet (Class A)", "market": "NASDAQ"},
    {"code": "GOOG", "name": "Alphabet (Class C)", "market": "NASDAQ"},
    {"code": "AMZN", "name": "Amazon", "market": "NASDAQ"},
    {"code": "NVDA", "name": "NVIDIA", "market": "NASDAQ"},
    {"code": "META", "name": "Meta Platforms", "market": "NASDAQ"},
    {"code": "TSLA", "name": "Tesla", "market": "NASDAQ"},
    {"code": "AVGO", "name": "Broadcom", "market": "NASDAQ"},
    {"code": "AMD", "name": "AMD", "market": "NASDAQ"},
    {"code": "INTC", "name": "Intel", "market": "NASDAQ"},
    {"code": "TSM", "name": "TSMC ADR", "market": "NYSE"},
    {"code": "MU", "name": "Micron", "market": "NASDAQ"},
    {"code": "ASML", "name": "ASML", "market": "NASDAQ"},
    {"code": "ORCL", "name": "Oracle", "market": "NYSE"},
    {"code": "CRM", "name": "Salesforce", "market": "NYSE"},
    {"code": "ADBE", "name": "Adobe", "market": "NASDAQ"},
    {"code": "NFLX", "name": "Netflix", "market": "NASDAQ"},
    {"code": "DIS", "name": "Disney", "market": "NYSE"},
    {"code": "UBER", "name": "Uber", "market": "NYSE"},
    {"code": "ABNB", "name": "Airbnb", "market": "NASDAQ"},
    {"code": "PYPL", "name": "PayPal", "market": "NASDAQ"},
    {"code": "SHOP", "name": "Shopify", "market": "NYSE"},
    {"code": "PLTR", "name": "Palantir", "market": "NASDAQ"},
    {"code": "SNOW", "name": "Snowflake", "market": "NYSE"},
    {"code": "COIN", "name": "Coinbase", "market": "NASDAQ"},
    {"code": "MARA", "name": "Marathon Digital", "market": "NASDAQ"},
    {"code": "RIOT", "name": "Riot Platforms", "market": "NASDAQ"},
    {"code": "SOFI", "name": "SoFi", "market": "NASDAQ"},
    {"code": "RIVN", "name": "Rivian", "market": "NASDAQ"},
    {"code": "LCID", "name": "Lucid", "market": "NASDAQ"},
    {"code": "F", "name": "Ford", "market": "NYSE"},
    {"code": "GM", "name": "General Motors", "market": "NYSE"},
    {"code": "BA", "name": "Boeing", "market": "NYSE"},
    {"code": "LMT", "name": "Lockheed Martin", "market": "NYSE"},
    {"code": "RTX", "name": "RTX (Raytheon)", "market": "NYSE"},
    {"code": "JPM", "name": "JPMorgan Chase", "market": "NYSE"},
    {"code": "BAC", "name": "Bank of America", "market": "NYSE"},
    {"code": "GS", "name": "Goldman Sachs", "market": "NYSE"},
    {"code": "WMT", "name": "Walmart", "market": "NYSE"},
    {"code": "COST", "name": "Costco", "market": "NASDAQ"},
    {"code": "TGT", "name": "Target", "market": "NYSE"},
    {"code": "MCD", "name": "McDonald's", "market": "NYSE"},
    {"code": "KO", "name": "Coca-Cola", "market": "NYSE"},
    {"code": "PEP", "name": "PepsiCo", "market": "NASDAQ"},
    {"code": "PG", "name": "Procter & Gamble", "market": "NYSE"},
    {"code": "JNJ", "name": "Johnson & Johnson", "market": "NYSE"},
    {"code": "PFE", "name": "Pfizer", "market": "NYSE"},
    {"code": "LLY", "name": "Eli Lilly", "market": "NYSE"},
    {"code": "MRNA", "name": "Moderna", "market": "NASDAQ"},
    {"code": "XOM", "name": "ExxonMobil", "market": "NYSE"},
    {"code": "CVX", "name": "Chevron", "market": "NYSE"},
    {"code": "SPY", "name": "S&P 500 ETF", "market": "NYSEARCA"},
    {"code": "QQQ", "name": "Nasdaq 100 ETF", "market": "NASDAQ"},
    {"code": "IWM", "name": "Russell 2000 ETF", "market": "NYSEARCA"},
    {"code": "DIA", "name": "Dow ETF", "market": "NYSEARCA"},
]


def to_yf_ticker(code: str, market: str = "KR") -> str:
    """6자리 한국 코드면 .KS / 미국 코드면 그대로."""
    code = (code or "").strip().upper()
    if code.isdigit() and len(code) == 6:
        return f"{code}.KS"
    return code


def search_stocks(query: str, limit: int = 20) -> List[Dict]:
    """종목 코드/이름 부분 일치 검색. 국장 + 미국 통합."""
    q = (query or "").strip()
    if not q:
        return []
    qu = q.upper()
    out: List[Dict] = []
    for s in KR_STOCK_UNIVERSE + US_STOCK_UNIVERSE:
        name = s.get("name", "")
        code = s.get("code", "")
        if qu in code.upper() or q in name or qu in name.upper():
            out.append({
                "code": code,
                "name": name,
                "market": s.get("market"),
                "ticker": to_yf_ticker(code),
                "region": "KR" if code.isdigit() and len(code) == 6 else "US",
            })
            if len(out) >= limit:
                break
    return out


def get_hot_stocks_kr(limit: int = 8) -> List[Dict]:
    """국장(코스피+코스닥) 거래대금 상위. 1차 KRX → 실패 시 universe 샘플링."""
    import random
    try:
        url = "http://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
        out: List[Dict] = []
        for mkt_id in ["STK", "KSQ"]:
            params = {
                "bld": "dbms/MDC/STAT/standard/MDCSTAT01501",
                "locale": "ko_KR",
                "mktId": mkt_id,
                "trdDd": datetime.now().strftime("%Y%m%d"),
                "share": "1",
                "money": "1",
                "csvxls_isNo": "false",
            }
            r = requests.post(url, data=params, timeout=10, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "http://data.krx.co.kr/",
            })
            if r.status_code != 200:
                continue
            data = r.json()
            rows = data.get("OutBlock_1", []) or []
            rows.sort(
                key=lambda x: int((x.get("ACC_TRDVAL") or "0").replace(",", "") or 0),
                reverse=True,
            )
            for row in rows[: limit // 2 + 4]:
                code = (row.get("ISU_SRT_CD") or "").strip()
                name = (row.get("ISU_ABBRV") or "").strip()
                if not code or not name:
                    continue
                out.append({
                    "code": code,
                    "name": name,
                    "market": "KOSPI" if mkt_id == "STK" else "KOSDAQ",
                    "ticker": to_yf_ticker(code),
                    "region": "KR",
                    "trade_value": int((row.get("ACC_TRDVAL") or "0").replace(",", "") or 0),
                    "change_pct_krx": float((row.get("FLUC_RT") or "0").replace(",", "") or 0),
                })
        if out:
            kospi = [s for s in out if s["market"] == "KOSPI"]
            kosdaq = [s for s in out if s["market"] == "KOSDAQ"]
            kospi.sort(key=lambda x: x["trade_value"], reverse=True)
            kosdaq.sort(key=lambda x: x["trade_value"], reverse=True)
            mixed: List[Dict] = []
            i = 0
            while len(mixed) < limit and (i < len(kospi) or i < len(kosdaq)):
                if i < len(kospi):
                    mixed.append(kospi[i])
                if len(mixed) < limit and i < len(kosdaq):
                    mixed.append(kosdaq[i])
                i += 1
            return mixed[:limit]
    except Exception:
        pass

    # Fallback - 대형주 편중 회피용 랜덤 샘플
    pool = KR_STOCK_UNIVERSE.copy()
    random.shuffle(pool)
    half = max(1, limit // 2)
    kospi = [s for s in pool if s.get("market") == "KOSPI"][:half + 1]
    kosdaq = [s for s in pool if s.get("market") == "KOSDAQ"][:half + 1]
    picked: List[Dict] = []
    i = 0
    while len(picked) < limit and (i < len(kospi) or i < len(kosdaq)):
        if i < len(kospi):
            picked.append(kospi[i])
        if len(picked) < limit and i < len(kosdaq):
            picked.append(kosdaq[i])
        i += 1
    return [{
        "code": s["code"],
        "name": s["name"],
        "market": s["market"],
        "ticker": to_yf_ticker(s["code"]),
        "region": "KR",
    } for s in picked[:limit]]


def get_hot_stocks_us(limit: int = 5) -> List[Dict]:
    """미국 핫 종목 - yfinance + yahoo_direct 폴백으로 등락률·거래량 기준."""
    import random
    try:
        candidates = [s["code"] for s in US_STOCK_UNIVERSE[:30]]
        scored: List[Tuple[str, float, float]] = []
        for code in candidates:
            try:
                hist = _yf_history_safe(code, period="5d")
                if hist.empty or len(hist) < 2:
                    continue
                last = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                vol = float(hist["Volume"].iloc[-1])
                chg = (last - prev) / prev * 100
                scored.append((code, abs(chg), vol))
            except Exception:
                continue
        scored.sort(key=lambda x: (x[1], x[2]), reverse=True)
        picked = [code for code, _, _ in scored[:limit]]
        if picked:
            by_code = {s["code"]: s for s in US_STOCK_UNIVERSE}
            return [{
                "code": code,
                "name": by_code.get(code, {}).get("name", code),
                "market": by_code.get(code, {}).get("market", "US"),
                "ticker": code,
                "region": "US",
            } for code in picked]
    except Exception:
        pass

    pool = US_STOCK_UNIVERSE.copy()
    random.shuffle(pool)
    return [{
        "code": s["code"],
        "name": s["name"],
        "market": s["market"],
        "ticker": s["code"],
        "region": "US",
    } for s in pool[:limit]]


def get_hot_stocks_mixed(kr_limit: int = 6, us_limit: int = 4) -> List[Dict]:
    """국장 + 미국장 합쳐서 다양화."""
    return get_hot_stocks_kr(kr_limit) + get_hot_stocks_us(us_limit)


# ─────────────────────────────────────────────
# monitor_loop.py 가 호출하는 모듈 레벨 헬퍼
# ─────────────────────────────────────────────
DEFAULT_WATCH_QUERIES = [
    "삼성전자", "SK하이닉스", "NAVER", "카카오", "현대차",
    "삼성바이오로직스", "셀트리온", "LG에너지솔루션",
]


def collect_news(queries=None, per_query: int = 5):
    """모니터링 루프용 - 워치리스트 종목 뉴스를 평탄화해 반환."""
    queries = queries or DEFAULT_WATCH_QUERIES
    collector = StockDataCollector()
    all_items = []
    for q in queries:
        bundle = collector.get_news_data(q, display=per_query)
        if bundle.get('error'):
            continue
        for it in bundle.get('items', []):
            all_items.append({
                'title': it.get('title', ''),
                'url': it.get('link', ''),
                'description': it.get('description', ''),
                'pub_date': it.get('pub_date', ''),
                '_source': q,
            })
    return all_items
