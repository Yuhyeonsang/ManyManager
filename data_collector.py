import os
import logging
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

try:
    from bs4 import BeautifulSoup
    _BS4_AVAILABLE = True
except ImportError:
    _BS4_AVAILABLE = False
    logging.getLogger(__name__).warning("bs4(BeautifulSoup) 미설치 — 네이버 ETF 파싱 비활성")

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


def _kr_realtime_price(ticker: str) -> Optional[Dict]:
    """일별시세 실패 시 네이버 실시간 시세(polling 엔드포인트)로 현재가만이라도 채움. KR 전용."""
    if not (_is_kr_ticker(ticker) and _NAVER_AVAILABLE):
        return None
    try:
        rt = _naver.get_realtime(ticker)
    except Exception:
        return None
    if not rt:
        return None
    cp = rt.get("current_price")
    if not cp:
        return None
    return {
        "ticker": ticker,
        "as_of": rt.get("as_of"),
        "current_price": round(float(cp), 2),
        "prev_close": None,
        "change": rt.get("change"),
        "change_pct": rt.get("change_pct"),
        "moving_averages": {},
        "high_52w": None,
        "low_52w": None,
        "avg_volume_20d": rt.get("volume"),
        "recent_10d": [],
        "source": "naver_realtime",
    }


def _yf_history_safe(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """가격 일봉 데이터.
    KR 종목(.KS/.KQ): Naver 직행 → 실패 시 yfinance/yahoo_direct 폴백.
    US 종목: yfinance → yahoo_direct 순.
    ─ Yahoo 429 rate-limit 차단 대응 (2024~): KR 호출량을 0으로 줄여서
      US 종목 야후 호출이 차단 임계치를 안 넘게 함.
    """
    is_kr = _is_kr_ticker(ticker)

    # KR 종목: Naver 우선 (동시요청 throttle 대비 재시도)
    if is_kr and _NAVER_AVAILABLE:
        period_days = _period_to_days(period)
        import time as _t
        for _attempt in range(3):
            try:
                df = _naver.get_history(ticker, period_days=period_days)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                log.debug(f"naver_finance history 실패 ({ticker}, 시도 {_attempt+1}): {e}")
            _t.sleep(0.6)

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



# ─────────────────────────────────────────────────────────────
# KR ETF 목록 캐시 — ETF 자동 감지용 (pykrx 기반)
# ─────────────────────────────────────────────────────────────
_kr_etf_set_cache: Optional[set] = None
_kr_etf_set_date: Optional[str] = None


def get_kr_etf_set() -> set:
    """KRX 상장 ETF 종목코드 집합 (일 1회 갱신). 실패 시 빈 set."""
    global _kr_etf_set_cache, _kr_etf_set_date
    if not _PYKRX_AVAILABLE:
        return _kr_etf_set_cache or set()
    today = datetime.now().strftime("%Y%m%d")
    if _kr_etf_set_cache is not None and _kr_etf_set_date == today:
        return _kr_etf_set_cache
    try:
        tickers = pykrx_stock.get_etf_ticker_list()
        _kr_etf_set_cache = set(tickers)
        _kr_etf_set_date = today
        log.info(f"KR ETF 목록 로드: {len(_kr_etf_set_cache)}개")
        return _kr_etf_set_cache
    except Exception as e:
        log.warning(f"KR ETF 목록 로드 실패: {e}")
        return _kr_etf_set_cache or set()


def is_kr_etf(stock_code: str) -> bool:
    """KR ETF 여부 판별.
    1순위: KR_ETF_UNIVERSE 하드코드 (pykrx 없어도 항상 작동)
    2순위: pykrx 동적 목록 (전체 KRX ETF 커버)"""
    if not stock_code:
        return False
    # 하드코드 우선 확인 (Python 함수는 호출 시 평가되므로 KR_ETF_UNIVERSE 참조 가능)
    try:
        if any(s["code"] == stock_code for s in KR_ETF_UNIVERSE):
            return True
    except NameError:
        pass
    return stock_code in get_kr_etf_set()


# ─── KR ETF 이름 캐시 (KRX API) ───────────────────────────────
_kr_etf_info_cache: Optional[List[Dict]] = None
_kr_etf_info_date: Optional[str] = None


def get_kr_etf_info_list() -> List[Dict]:
    """pykrx로 전체 ETF 코드+이름 목록. 일 1회 캐시.
    pykrx의 get_market_ticker_name이 내부적으로 KRX 이름 테이블을 일괄 캐시하므로
    첫 호출 이후 빠름."""
    global _kr_etf_info_cache, _kr_etf_info_date
    today = datetime.now().strftime("%Y%m%d")
    if _kr_etf_info_cache is not None and _kr_etf_info_date == today:
        return _kr_etf_info_cache
    if not _PYKRX_AVAILABLE:
        return _kr_etf_info_cache or []
    try:
        codes = list(get_kr_etf_set())
        if not codes:
            return _kr_etf_info_cache or []
        result = []
        for code in codes:
            try:
                name = pykrx_stock.get_market_ticker_name(code)
                if name:
                    result.append({"code": code, "name": name})
            except Exception:
                pass
        if result:
            _kr_etf_info_cache = result
            _kr_etf_info_date = today
            log.info(f"KR ETF 이름 목록: {len(result)}개")
        return result or _kr_etf_info_cache or []
    except Exception as e:
        log.warning(f"KR ETF 이름 목록 실패: {e}")
        return _kr_etf_info_cache or []

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
        period: str = "1y",  # ★ 6mo(~126거래일)였던 걸 1y(~252거래일)로 수정.
        # 52주 고저(high_52w/low_52w)가 close.tail(252)로 계산되는데 6mo로는
        # 절반(126일)치밖에 안 가져와서 "52주"가 실제로는 "6개월" 범위였음.
        # (2026-07 검증: Micron 52주위치 46.8%(앱, 6mo 기준) vs 55.2%(실제 52주
        # 103.38~1255 기준) — 8%p 오차. MA120도 6mo로는 버퍼 없이 딱 걸치는 수준.
        ma_windows: List[int] = [5, 20, 60, 120],
    ) -> Dict:
        try:
            hist = _yf_history_safe(ticker, period=period)
            if hist.empty:
                _fb = _kr_realtime_price(ticker)
                return _fb if _fb else {"ticker": ticker, "error": "no price data"}

            # NaN 행 제거 — 일부 소스(Naver siseJson)는 trailing 빈 row 가 있음
            close = hist["Close"].dropna()
            if len(close) == 0:
                _fb = _kr_realtime_price(ticker)
                return _fb if _fb else {"ticker": ticker, "error": "no valid close price"}
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

    def get_us_news(self, ticker: str, display: int = 25) -> Dict:
        """US 종목 영어 뉴스 (yfinance Ticker.news). 신/구 포맷 모두 처리."""
        try:
            raw = yf.Ticker(ticker).news or []
        except Exception as e:
            return {"query": ticker, "error": f"yf news: {e}", "items": []}
        items = []
        for it in raw:
            c = it.get("content") if isinstance(it.get("content"), dict) else it
            title = (c.get("title") or it.get("title") or "").strip()
            if not title:
                continue
            summary = (c.get("summary") or c.get("description") or "").strip()
            link = ""
            for cand in (c.get("canonicalUrl"), c.get("clickThroughUrl")):
                if isinstance(cand, dict) and cand.get("url"):
                    link = cand["url"]
                    break
            if not link:
                link = it.get("link", "")
            pub = c.get("pubDate") or it.get("providerPublishTime") or ""
            items.append({"title": title, "description": summary,
                          "link": link, "pub_date": str(pub)})
            if len(items) >= display:
                break
        return {"query": ticker, "count": len(items), "items": items}

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

            # DART account_nm 은 회사마다 표현이 다름 (손실 포함, 수익 표현 등)
            # 우선순위: 앞쪽 키가 먼저 매칭되면 뒤쪽 변형은 무시됨 (result에 이미 있으면 skip)
            targets = {
                # 매출
                "매출액": "revenue",
                "수익(매출액)": "revenue",
                "매출": "revenue",
                "영업수익": "revenue",
                "수익": "revenue",
                # 영업이익
                "영업이익": "operating_income",
                "영업이익(손실)": "operating_income",
                "영업손익": "operating_income",
                # 당기순이익
                "당기순이익": "net_income",
                "당기순이익(손실)": "net_income",
                "당기순손익": "net_income",
                "분기순이익": "net_income",
                "반기순이익": "net_income",
                "분기순이익(손실)": "net_income",
                "반기순이익(손실)": "net_income",
                # 자산/부채/자본
                "자산총계": "total_assets",
                "자산 총계": "total_assets",
                "부채총계": "total_liabilities",
                "부채 총계": "total_liabilities",
                "자본총계": "total_equity",
                "자본 총계": "total_equity",
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
            eq_prev = result.get("total_equity", {}).get("previous")
            li = result.get("total_liabilities", {}).get("current")
            assets = result.get("total_assets", {}).get("current")

            # ROE: 평균자기자본 사용 (당기말+전기말)/2 — 업계 표준
            avg_eq = (eq + eq_prev) / 2 if eq and eq_prev else eq

            ratios = {
                "operating_margin_pct": round(op / rev * 100, 2) if rev and op else None,
                "net_margin_pct": round(ni / rev * 100, 2) if rev and ni else None,
                "roe_pct": round(ni / avg_eq * 100, 2) if ni and avg_eq and avg_eq > 0 else None,
                "roa_pct": round(ni / assets * 100, 2) if ni and assets else None,
                "debt_to_equity_pct": round(li / eq * 100, 2) if li and eq and eq != 0 else None,
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

            # 미국 종목 보강 — DART 없으니 yfinance.info 에서 직접
            # yfinance 는 ratio(0~1) 로 줘서 *100 해야 함
            rev_growth = info.get("revenueGrowth")
            rev_growth_pct = (
                _round(rev_growth * 100, 2) if isinstance(rev_growth, (int, float)) else None
            )
            op_margin = info.get("operatingMargins")
            op_margin_pct = (
                _round(op_margin * 100, 2) if isinstance(op_margin, (int, float)) else None
            )
            # debtToEquity 는 yfinance 가 이미 0~수백 의 % 형태로 줌 (예: 28.71)
            debt_to_equity = info.get("debtToEquity")
            debt_to_equity_pct = _round(debt_to_equity, 2) if isinstance(debt_to_equity, (int, float)) else None

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
                # 보강 필드 (특히 US — DART 못 쓰는 경우용)
                "revenue_growth_pct": rev_growth_pct,
                "operating_margin_pct": op_margin_pct,
                "debt_to_equity_pct": debt_to_equity_pct,
            }
        except Exception as e:
            return {"ticker": ticker, "error": str(e)}

    def _get_ttm_income_statement(self, stock_code: str, annual_fin: Dict) -> Optional[Dict]:
        """TTM 손익 = (연간) - (Q1 전년) + (Q1 당해).
        반환: {ttm_net_income, ttm_revenue, ttm_operating_income, equity_curr, equity_prev}
        Q1 당해 보고서(11013)가 없으면 None 반환."""
        try:
            ind = annual_fin.get("indicators", {})
            annual_ni  = ind.get("net_income", {}).get("current")
            annual_rev = ind.get("revenue", {}).get("current")
            annual_op  = ind.get("operating_income", {}).get("current")
            eq_curr    = ind.get("total_equity", {}).get("current")
            eq_prev    = ind.get("total_equity", {}).get("previous")
            if annual_ni is None:
                return None
            base_year = annual_fin.get("year", datetime.now().year - 1)
            curr_year = datetime.now().year

            # Q1 당해
            q1_curr = self.get_financial_statements(stock_code, year=curr_year, report_code="11013")
            if q1_curr.get("error"):
                return None
            ci = q1_curr.get("indicators", {})
            q1c_ni     = ci.get("net_income", {}).get("current")
            q1c_rev    = ci.get("revenue", {}).get("current")
            q1c_op     = ci.get("operating_income", {}).get("current")
            q1c_equity      = ci.get("total_equity", {}).get("current")       # ★ 최신 분기 자본총계
            q1c_liabilities = ci.get("total_liabilities", {}).get("current")  # ★ 최신 분기 부채총계
            if q1c_ni is None:
                return None

            # Q1 전년
            q1_prev = self.get_financial_statements(stock_code, year=base_year, report_code="11013")
            if q1_prev.get("error"):
                return None
            pi = q1_prev.get("indicators", {})
            q1p_ni  = pi.get("net_income", {}).get("current")
            q1p_rev = pi.get("revenue", {}).get("current")
            q1p_op  = pi.get("operating_income", {}).get("current")
            if q1p_ni is None:
                return None

            def _ttm(annual, prev, curr):
                if annual is None or prev is None or curr is None:
                    return None
                return annual - prev + curr

            ttm_ni  = _ttm(annual_ni,  q1p_ni,  q1c_ni)
            ttm_rev = _ttm(annual_rev, q1p_rev, q1c_rev)
            ttm_op  = _ttm(annual_op,  q1p_op,  q1c_op)

            if ttm_ni is None:
                return None

            return {
                "ttm_net_income":       ttm_ni,
                "ttm_revenue":          ttm_rev,
                "ttm_operating_income": ttm_op,
                "equity_curr":          eq_curr,
                "equity_prev":          eq_prev,
                "latest_equity":        q1c_equity,      # ★ 최신 분기 자본 (PBR 계산용)
                "latest_liabilities":   q1c_liabilities, # ★ 최신 분기 부채 (부채비율 계산용)
            }
        except Exception as e:
            log.debug(f"TTM income statement 계산 실패 ({stock_code}): {e}")
            return None

    def _get_ttm_net_income(self, stock_code: str, annual_fin: Dict) -> Optional[float]:
        """하위 호환용 래퍼 — _get_ttm_income_statement 위임."""
        r = self._get_ttm_income_statement(stock_code, annual_fin)
        return r["ttm_net_income"] if r else None


    # ─────────────────────────────────────────────
    # ETF 전용 지표 수집
    # ─────────────────────────────────────────────

    def get_kr_etf_metrics(self, stock_code: str) -> Dict:
        """KR ETF 전용: NAV·괴리율·순자산총액·수익률.
        우선순위: wisereport(naver_code 등록 시) → pykrx → yfinance"""
        result: Dict = {"is_etf": True, "market": "KR"}

        # ★ 1순위: 네이버 금융 ETF 메인 페이지 파싱 (naver_code 등록된 경우)
        naver_codes = self._load_etf_naver_codes()
        naver_code = naver_codes.get(stock_code)
        if naver_code:
            naver_data = self._get_kr_etf_naver_main(naver_code)
            if naver_data:
                result.update(naver_data)
            # 네이버에서 못 가져온 항목은 wisereport 메타로 보완 (기초지수·유형 등)
            if not result.get("benchmark_index") or not result.get("expense_ratio_pct"):
                try:
                    wr_meta = self._get_kr_etf_meta_wisereport(naver_code)
                    for k, v in wr_meta.items():
                        if k not in result:
                            result[k] = v
                except Exception:
                    pass
            # ETF 이름 보완
            try:
                if _NAVER_AVAILABLE:
                    rt = _naver.get_realtime(f"{stock_code}.KS")
                    if rt and rt.get("name"):
                        result["fund_name"] = rt["name"]
            except Exception:
                pass
            # ★ 일별시세 파싱 → MA20/MA60/모멘텀/평균거래량 계산
            naver_price = self._get_kr_etf_price_history_naver(naver_code)
            if naver_price:
                result["naver_price"] = naver_price
                # 52주 고저: 일별시세에서 계산된 값이 더 정확하면 덮어씀
                if naver_price.get("high_52w") and not result.get("price_52w_high"):
                    result["price_52w_high"] = naver_price["high_52w"]
                if naver_price.get("low_52w") and not result.get("price_52w_low"):
                    result["price_52w_low"] = naver_price["low_52w"]
            else:
                log.warning(f"ETF {stock_code}: sise_day 파싱 실패 (naver_code={naver_code}) → MA20/MA60 없음")

            # 핵심 데이터(NAV 또는 기초지수)가 있으면 pykrx 생략
            if result.get("nav") or result.get("benchmark_index"):
                log.info(f"ETF {stock_code} 네이버 파싱 데이터 사용 완료")
                return result

        if not _PYKRX_AVAILABLE:
            return result

        # NAV + 괴리율 + 순자산총액
        try:
            end = datetime.now()
            start = end - timedelta(days=7)
            df = pykrx_stock.get_etf_price_and_nav(
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
                stock_code,
            )
            if df is not None and not df.empty:
                last = df.iloc[-1]
                nav = float(last.get("NAV") or last.get("기준가") or 0)
                price_close = float(last.get("종가") or 0)
                total_assets = float(last.get("순자산총액") or 0)
                if nav > 0:
                    result["nav"] = nav
                    if price_close > 0:
                        result["nav_diff_pct"] = round((price_close - nav) / nav * 100, 2)
                if total_assets > 0:
                    result["total_assets_billion"] = round(total_assets / 1e8, 1)
        except Exception as e:
            log.debug(f"KR ETF NAV 실패 ({stock_code}): {e}")

        # 수익률 (1개월/3개월/1년)
        try:
            end = datetime.now()
            start_1y = end - timedelta(days=380)
            df_ohlcv = pykrx_stock.get_etf_ohlcv_by_date(
                start_1y.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
                stock_code,
            )
            if df_ohlcv is not None and not df_ohlcv.empty and "종가" in df_ohlcv.columns:
                closes = df_ohlcv["종가"].dropna().astype(float)
                if len(closes) >= 1:
                    last_p = closes.iloc[-1]
                    def _ret(n):
                        if len(closes) < n + 1:
                            return None
                        old = closes.iloc[-(n + 1)]
                        return round((last_p - old) / old * 100, 2) if old else None
                    result["return_1m"] = _ret(20)
                    result["return_3m"] = _ret(60)
                    result["return_1y"] = _ret(240)
        except Exception as e:
            log.debug(f"KR ETF 수익률 실패 ({stock_code}): {e}")

        # pykrx 수익률 없으면 yfinance fallback
        if result.get("return_1m") is None and result.get("return_3m") is None:
            try:
                import yfinance as yf
                tk = yf.Ticker(f"{stock_code}.KS")
                hist = tk.history(period="1y")
                if hist is not None and not hist.empty and "Close" in hist.columns:
                    closes = hist["Close"].dropna()
                    if len(closes) >= 1:
                        last_p = float(closes.iloc[-1])
                        def _ret_yf(n):
                            if len(closes) < n + 1:
                                return None
                            old = float(closes.iloc[-(n + 1)])
                            return round((last_p - old) / old * 100, 2) if old else None
                        result["return_1m"] = _ret_yf(20)
                        result["return_3m"] = _ret_yf(60)
                        result["return_1y"] = _ret_yf(240)
                        # NAV/AUM도 없으면 yfinance info로 보완
                        if result.get("total_assets_billion") is None:
                            try:
                                info = tk.info or {}
                                ta = info.get("totalAssets")
                                if ta and ta > 0:
                                    result["total_assets_billion"] = round(ta / 1e8, 1)
                            except Exception:
                                pass
            except Exception as e:
                log.debug(f"KR ETF yfinance 수익률 실패 ({stock_code}): {e}")

        # ETF 이름 (Naver 실시간)
        try:
            if _NAVER_AVAILABLE:
                rt = _naver.get_realtime(f"{stock_code}.KS")
                if rt and rt.get("name"):
                    result["fund_name"] = rt["name"]
        except Exception as e:
            log.debug(f"KR ETF 이름 실패 ({stock_code}): {e}")

        return result

    def _load_etf_naver_codes(self) -> Dict[str, str]:
        """etf_naver_codes.json 에서 KRX→네이버 코드 매핑 로드"""
        try:
            path = os.path.join(os.path.dirname(__file__), "etf_naver_codes.json")
            with open(path, "r", encoding="utf-8") as f:
                import json as _json
                return _json.load(f)
        except Exception:
            return {}

    def _wisereport_decode(self, content_bytes: bytes) -> str:
        """wisereport 응답 bytes → str. UTF-8 우선, 실패 시 EUC-KR."""
        try:
            return content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return content_bytes.decode("euc-kr", errors="replace")

    def _is_valid_kr_name(self, name: str) -> bool:
        """회사명 유효성 검사. 한글/영문/숫자/기호만 허용, 깨진 문자 제외."""
        if not name or len(name) > 30:
            return False
        # 대체문자·다이아몬드·물음표 등 인코딩 오류 표시 제외
        bad = ("◆", "◇", "▲", "▽", "□", "■", "�", "?")
        if any(b in name for b in bad):
            return False
        # 최소 1자 이상 한글 또는 영문 포함
        import re
        return bool(re.search(r"[가-힣a-zA-Z]", name))

    def _get_wisereport_html(self, naver_code: str) -> str:
        """wisereport ETF 메인 페이지 HTML 가져오기 (공용)."""
        url = f"https://navercomp.wisereport.co.kr/v2/ETF/index.aspx?cmp_cd={naver_code}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.naver.com/",
        }
        r = requests.get(url, headers=headers, timeout=15)
        return self._wisereport_decode(r.content)

    def _get_kr_etf_constituents_naver_coinfo(self, naver_code: str, top_n: int = 5) -> List[str]:
        """네이버 coinfo 페이지에서 ETF 구성종목 직접 파싱 (비중 상위 top_n).
        URL: https://finance.naver.com/item/coinfo.naver?code={naver_code}
        """
        try:
            from bs4 import BeautifulSoup
            url = f"https://finance.naver.com/item/coinfo.naver?code={naver_code}"
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            r = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(r.content, "html.parser", from_encoding="euc-kr")
            # 구성종목 테이블: class="tbl_type3" 또는 구성종목명 헤더 포함 테이블
            pairs = []
            for table in soup.find_all("table"):
                headers_row = table.find("tr")
                if not headers_row:
                    continue
                ths = [th.get_text(strip=True) for th in headers_row.find_all(["th", "td"])]
                if "구성종목명" not in str(ths):
                    continue
                # 컬럼 인덱스 찾기
                try:
                    name_idx = next(i for i, h in enumerate(ths) if "구성종목명" in h)
                    weight_idx = next((i for i, h in enumerate(ths) if "구성비중" in h), None)
                except StopIteration:
                    continue
                for row in table.find_all("tr")[1:]:
                    cells = row.find_all("td")
                    if len(cells) <= name_idx:
                        continue
                    name = cells[name_idx].get_text(strip=True)
                    if not name or not self._is_valid_kr_name(name):
                        continue
                    weight = 0.0
                    if weight_idx is not None and len(cells) > weight_idx:
                        try:
                            weight = float(cells[weight_idx].get_text(strip=True).replace(",", "").replace("%", ""))
                        except ValueError:
                            pass
                    pairs.append((name, weight))
                if pairs:
                    break
            if not pairs:
                log.debug(f"naver coinfo 구성종목 없음 ({naver_code})")
                return []
            pairs.sort(key=lambda x: -x[1])
            result = [f"{n}({w:.2f}%)" if w > 0 else n for n, w in pairs[:top_n]]
            log.info(f"naver coinfo ETF 구성종목 ({naver_code}): {result}")
            return result
        except Exception as e:
            log.debug(f"naver coinfo 구성종목 파싱 실패 ({naver_code}): {e}")
            return []

    def _get_kr_etf_constituents_wisereport(self, naver_code: str, top_n: int = 5) -> List[str]:
        """wisereport HTML 파싱으로 ETF 구성종목 동적 조회 (비중 상위 top_n).
        반환: ['SK하이닉스(24.97%)', '삼성전기(24.10%)', ...] 형태 (비중 포함)
        """
        import re
        try:
            content = self._get_wisereport_html(naver_code)
            names_raw = re.findall(r'"STK_NM_KOR"\s*:\s*"([^"]+)"', content)
            weights_raw = re.findall(r'"ETF_WEIGHT"\s*:\s*([\d.]+)', content)
            names = [n for n in names_raw if self._is_valid_kr_name(n)]
            if not names:
                log.debug(f"wisereport 구성종목 없음 ({naver_code}) — HTML 길이: {len(content)}")
                return []
            if weights_raw and len(weights_raw) == len(names_raw):
                pairs = [(n, float(w)) for n, w in zip(names_raw, weights_raw) if self._is_valid_kr_name(n)]
                pairs.sort(key=lambda x: -x[1])
                # 비중 포함 형태: "SK하이닉스(24.97%)"
                result = [f"{n}({w:.2f}%)" for n, w in pairs[:top_n]]
            else:
                result = names[:top_n]
            log.info(f"wisereport ETF 구성종목 ({naver_code}): {result}")
            return result
        except Exception as e:
            log.debug(f"wisereport 구성종목 파싱 실패 ({naver_code}): {e}")
            return []

    def _get_kr_etf_returns_wisereport(self, naver_code: str) -> Dict:
        """wisereport GetNAVData.aspx → NAV·수익률(1/3/12개월) 반환."""
        import re, json as _json
        result: Dict = {}
        try:
            end = datetime.now()
            start = end - timedelta(days=400)
            url = (
                f"https://navercomp.wisereport.co.kr/v2/ETF/GetNAVData.aspx"
                f"?startDT={start.strftime('%Y%m%d')}&endDT={end.strftime('%Y%m%d')}"
                f"&dataType=D&cmp_cd={naver_code}&cmp_typ=5"
            )
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": f"https://navercomp.wisereport.co.kr/v2/ETF/index.aspx?cmp_cd={naver_code}",
            }
            r = requests.get(url, headers=headers, timeout=10)
            content = self._wisereport_decode(r.content)
            data = _json.loads(content)
            # 응답은 리스트이거나 {"result": [...]} 형태
            records = data if isinstance(data, list) else data.get("result", data.get("data", []))
            if not records:
                return result
            navs: List[float] = []
            for rec in records:
                v = rec.get("NAV") or rec.get("nav") or rec.get("BASE_NAV")
                if v is not None:
                    try:
                        navs.append(float(str(v).replace(",", "")))
                    except ValueError:
                        pass
            if navs:
                last = navs[-1]
                result["nav"] = last
                def _ret(n: int):
                    if len(navs) < n + 1:
                        return None
                    old = navs[-(n + 1)]
                    return round((last - old) / old * 100, 2) if old else None
                result["return_1m"] = _ret(20)
                result["return_3m"] = _ret(60)
                result["return_1y"] = _ret(240)
            log.info(f"wisereport NAV 데이터 ({naver_code}): nav={result.get('nav')}, 1m={result.get('return_1m')}")
        except Exception as e:
            log.debug(f"wisereport GetNAVData 실패 ({naver_code}): {e}")
        return result

    def _get_kr_etf_meta_wisereport(self, naver_code: str) -> Dict:
        """wisereport HTML에서 ETF 메타정보(AUM·보수·기초지수·유형) 파싱."""
        import re
        result: Dict = {}
        try:
            content = self._get_wisereport_html(naver_code)
            if len(content) < 1000:
                return result
            # 순자산(AUM) — 여러 키 시도
            for key in ("TOTAL_NAV", "BASE_AMT", "NETASSET", "NET_ASSET"):
                m = re.search(rf'"{key}"\s*:\s*"?([\d.]+)"?', content)
                if m:
                    result["total_assets_billion"] = round(float(m.group(1)) / 1e8, 1)
                    break
            # 펀드보수(TER)
            for key in ("EXPN_RT", "FUND_EXPNS_RATIO", "TER", "MGMT_FEE"):
                m = re.search(rf'"{key}"\s*:\s*"?([\d.]+)"?', content)
                if m:
                    result["expense_ratio_pct"] = float(m.group(1))
                    break
            # 기초지수
            for key in ("BNC_IDX_NM", "BASE_IDX_NM", "BENCHMARK_NM"):
                m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', content)
                if m and m.group(1).strip():
                    result["benchmark_index"] = m.group(1).strip()
                    break
            # ETF 유형
            for key in ("ETF_TP_NM", "FUND_TP_NM", "TYPE_NM"):
                m = re.search(rf'"{key}"\s*:\s*"([^"]+)"', content)
                if m and m.group(1).strip():
                    result["category"] = m.group(1).strip()
                    break
            if result:
                log.info(f"wisereport ETF 메타 ({naver_code}): {result}")
        except Exception as e:
            log.debug(f"wisereport ETF 메타 파싱 실패 ({naver_code}): {e}")
        return result

    def _get_kr_etf_naver_main(self, naver_code: str) -> Dict:
        """네이버 금융 ETF 메인 페이지 파싱.
        https://finance.naver.com/item/main.naver?code={naver_code}
        EUC-KR 인코딩. NAV·수익률·52주고저·AUM·기초지수·펀드보수·자산운용사 반환.
        """
        import re
        result: Dict = {}
        if not _BS4_AVAILABLE:
            log.debug("bs4 미설치 — 네이버 ETF 파싱 스킵")
            return result
        try:
            url = f"https://finance.naver.com/item/main.naver?code={naver_code}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept-Language": "ko-KR,ko;q=0.9",
                "Referer": "https://finance.naver.com/",
            }
            r = requests.get(url, headers=headers, timeout=12)
            if r.status_code != 200:
                log.debug(f"네이버 ETF 페이지 {r.status_code} ({naver_code})")
                return result

            soup = BeautifulSoup(r.content, "html.parser", from_encoding="euc-kr")

            def _clean(s: str) -> str:
                return re.sub(r"\s+", " ", s).strip()

            def _parse_pct(s: str):
                """'+27.75%' 또는 '-3.2%' → float, N/A·빈값 → None"""
                s = s.replace("%", "").replace("+", "").replace(",", "").strip()
                if s in ("N/A", "-", "", "N"):
                    return None
                try:
                    return float(s)
                except ValueError:
                    return None

            def _parse_num(s: str):
                nums = re.findall(r"[\d,]+", s)
                if not nums:
                    return None
                try:
                    return float(nums[0].replace(",", ""))
                except ValueError:
                    return None

            # ── 테이블 행 순회 ────────────────────────────────────
            def _proc_kv(key: str, val: str):
                """key-value 쌍에서 ETF 지표 추출 (result에 직접 저장)."""
                # 기초지수
                if "기초지수" in key and len(val) > 2 and "benchmark_index" not in result:
                    result["benchmark_index"] = val
                # 수익률
                elif "1개월" in key and "수익률" in key:
                    v = _parse_pct(val)
                    if v is not None: result["return_1m"] = v
                elif "3개월" in key and "수익률" in key:
                    v = _parse_pct(val)
                    if v is not None: result["return_3m"] = v
                elif "6개월" in key and "수익률" in key:
                    v = _parse_pct(val)
                    if v is not None: result["return_6m"] = v
                elif "1년" in key and "수익률" in key:
                    v = _parse_pct(val)
                    if v is not None: result["return_1y"] = v
                # NAV
                elif "NAV" in key and "nav" not in result:
                    v = _parse_num(val)
                    if v and v > 0: result["nav"] = v
                # 52주 최고/최저
                elif "52주" in key and ("고" in key or "저" in key or "최" in key):
                    nums = re.findall(r"[\d,]+", val)
                    if len(nums) >= 2:
                        try:
                            result["price_52w_high"] = float(nums[0].replace(",", ""))
                            result["price_52w_low"] = float(nums[1].replace(",", ""))
                        except ValueError:
                            pass
                # 자산운용사
                elif "자산운용사" in key and "fund_family" not in result:
                    result["fund_family"] = val
                # 시가총액 (AUM)
                elif "시가총액" in key and "total_assets_billion" not in result:
                    jo_m = re.search(r"([\d,]+)조", val)
                    eok_m = re.search(r"([\d,]+)억", val)
                    try:
                        jo = float(jo_m.group(1).replace(",", "")) if jo_m else 0.0
                        eok = float(eok_m.group(1).replace(",", "")) if eok_m else 0.0
                        total_eok = jo * 10000 + eok
                        if total_eok > 0: result["total_assets_billion"] = round(total_eok, 0)
                    except (ValueError, AttributeError):
                        pass
                # 펀드보수 / 총보수
                elif ("펀드보수" in key or "총보수" in key) and "expense_ratio_pct" not in result:
                    m = re.search(r"([\d.]+)\s*%", val)
                    if m:
                        try: result["expense_ratio_pct"] = float(m.group(1))
                        except ValueError: pass
                # 거래량 (일 거래량)
                elif "거래량" in key and "거래대금" not in key and "daily_volume" not in result:
                    nums = re.findall(r"[\d,]+", val)
                    if nums:
                        try: result["daily_volume"] = int(nums[0].replace(",", ""))
                        except ValueError: pass
                # 전일가 / 현재가 (괴리율 계산용)
                elif ("전일" in key or "현재가" in key) and "prev_close" not in result:
                    nums = re.findall(r"[\d,]+", key + val)
                    if nums:
                        try:
                            v = float(nums[0].replace(",", ""))
                            if v > 1000: result["prev_close"] = v
                        except ValueError: pass
                # 등락률 (당일 변동률 %)
                elif "등락률" in key and "change_pct" not in result:
                    result["change_pct"] = _parse_pct(val)
                # 시가 (오늘 시가, 시가총액과 구별 위해 key가 정확히 "시가"여야 함)
                elif key.strip() == "시가" and "day_open" not in result:
                    v = _parse_num(val)
                    if v and v > 0: result["day_open"] = int(v)
                # 고가
                elif key.strip() == "고가" and "day_high" not in result:
                    v = _parse_num(val)
                    if v and v > 0: result["day_high"] = int(v)
                # 저가
                elif key.strip() == "저가" and "day_low" not in result:
                    v = _parse_num(val)
                    if v and v > 0: result["day_low"] = int(v)
                # 거래대금 (백만원 단위)
                elif "거래대금" in key and "trading_value_billion" not in result:
                    v = _parse_num(val)
                    if v and v > 0:
                        result["trading_value_billion"] = round(v / 10, 1)  # 백만→억원
                # 상장주식수
                elif "상장주식수" in key and "shares_outstanding" not in result:
                    v = _parse_num(val)
                    if v and v > 0: result["shares_outstanding"] = int(v)
                # 외국인현재 (천주)
                elif "외국인" in key and "foreign_holding" not in result:
                    nums = re.findall(r"[\d,]+", val)
                    if nums:
                        try: result["foreign_holding"] = int(nums[0].replace(",", ""))
                        except ValueError: pass

            for tr in soup.find_all("tr"):
                tds = tr.find_all(["td", "th"])
                if len(tds) < 2:
                    continue
                # 2열(key-val) 기본 처리
                key0 = _clean(tds[0].get_text())
                val1 = _clean(tds[1].get_text())
                _proc_kv(key0, val1)
                # 3열 이상 행: 추가 key-val 쌍 탐색 (예: 전일|고가|거래량 행)
                for i in range(2, len(tds) - 1, 2):
                    _proc_kv(_clean(tds[i].get_text()), _clean(tds[i+1].get_text()))
                # 단일 셀에 '거래량'이 포함된 경우 (예: '거래량35,282,843')
                for td in tds:
                    raw = _clean(td.get_text())
                    if "거래량" in raw and "daily_volume" not in result:
                        nums = re.findall(r"[\d,]{4,}", raw)
                        if nums:
                            try: result["daily_volume"] = int(max(nums, key=len).replace(",", ""))
                            except ValueError: pass

            # 괴리율 계산 (NAV vs 전일가)
            if result.get("nav") and result.get("prev_close"):
                nav = result["nav"]
                price = result["prev_close"]
                result["nav_diff_pct"] = round((price - nav) / nav * 100, 2)

            # ── sise.naver 추가 파싱 (시세 탭: 등락률·시가·고가·저가·시가총액 등) ──
            try:
                sise_url = f"https://finance.naver.com/item/sise.naver?code={naver_code}"
                rs = requests.get(sise_url, headers=headers, timeout=10)
                if rs.status_code == 200:
                    ss = BeautifulSoup(rs.content, "html.parser", from_encoding="euc-kr")
                    for tr in ss.find_all("tr"):
                        tds = tr.find_all(["td", "th"])
                        if len(tds) < 2:
                            continue
                        key0 = _clean(tds[0].get_text())
                        val1 = _clean(tds[1].get_text())
                        _proc_kv(key0, val1)
                        for i in range(2, len(tds) - 1, 2):
                            _proc_kv(_clean(tds[i].get_text()), _clean(tds[i+1].get_text()))
            except Exception as e2:
                log.debug(f"네이버 sise 파싱 실패 ({naver_code}): {e2}")

            if result:
                log.info(f"네이버 ETF 파싱 성공 ({naver_code}): {list(result.keys())}")
            else:
                log.debug(f"네이버 ETF 파싱 결과 없음 ({naver_code})")
        except Exception as e:
            log.debug(f"네이버 ETF 파싱 실패 ({naver_code}): {e}")
        return result

    def _get_kr_etf_price_history_naver(self, naver_code: str, pages: int = 7) -> Dict:
        """네이버 일별시세 페이지 파싱 → MA5/20/60/120·모멘텀·평균거래량 계산.
        https://finance.naver.com/item/sise_day.naver?code={naver_code}&page={n}
        EUC-KR 인코딩. 최대 pages*10 ≈ 140 거래일 수집.
        반환: price 딕셔너리 (analyze_price 호환)
        """
        import re
        if not _BS4_AVAILABLE:
            return {}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "ko-KR,ko;q=0.9",
            "Referer": f"https://finance.naver.com/item/main.naver?code={naver_code}",
        }
        rows = []  # [{"date": "2026.06.15", "close": 24800, "volume": 27440398}, ...]

        def _num(s):
            s = re.sub(r"[^0-9]", "", s)
            return int(s) if s else None

        def _fetch_page(page: int):
            """단일 페이지 파싱 → row 리스트 반환"""
            try:
                url = f"https://finance.naver.com/item/sise_day.naver?code={naver_code}&page={page}"
                r = requests.get(url, headers=headers, timeout=8)
                if r.status_code != 200:
                    return []
                soup = BeautifulSoup(r.content, "html.parser", from_encoding="euc-kr")
                table = soup.find("table", class_="type2")
                if not table:
                    return []
                page_rows = []
                for tr in table.find_all("tr"):
                    tds = tr.find_all("td")
                    if len(tds) < 7:
                        continue
                    texts = [td.get_text(strip=True) for td in tds]
                    date_str = texts[0]
                    if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_str):
                        continue
                    close = _num(texts[1])
                    volume = _num(texts[6])
                    if close:
                        page_rows.append({"date": date_str, "close": close, "volume": volume or 0})
                return page_rows
            except Exception:
                return []

        try:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=min(pages, 4)) as ex:
                futures = {ex.submit(_fetch_page, p): p for p in range(1, pages + 1)}
                for fut in as_completed(futures):
                    rows.extend(fut.result())
        except Exception as e:
            log.warning(f"네이버 일별시세 파싱 실패 ({naver_code}): {e}")

        if not rows:
            return {}

        # 오래된 순으로 정렬 (최신이 먼저 들어오므로 reverse)
        rows.sort(key=lambda x: x["date"])

        closes = [r["close"] for r in rows]
        volumes = [r["volume"] for r in rows]
        n = len(closes)

        def _ma(lst, w):
            if len(lst) >= w:
                return round(sum(lst[-w:]) / w)
            return None

        ma5 = _ma(closes, 5)
        ma20 = _ma(closes, 20)
        ma60 = _ma(closes, 60)
        ma120 = _ma(closes, 120)
        avg_vol_20 = int(sum(volumes[-20:]) / min(20, len(volumes))) if volumes else None

        # 10일 모멘텀
        recent_10 = [{"close": c} for c in closes[-10:]]

        # 52주 고저: 최근 250 거래일 (보유 데이터 내에서)
        hi52 = max(closes[-250:]) if closes else None
        lo52 = min(closes[-250:]) if closes else None

        current_price = closes[-1] if closes else None

        result = {
            "current_price": current_price,
            "high_52w": hi52,
            "low_52w": lo52,
            "moving_averages": {"MA5": ma5, "MA20": ma20, "MA60": ma60, "MA120": ma120},
            "recent_10d": recent_10,
            "avg_volume_20d": avg_vol_20,
            "_source": "naver_sise_day",
            "_days": n,
        }
        log.info(f"네이버 일별시세 파싱 성공 ({naver_code}): {n}일치, MA20={ma20}, MA60={ma60}")
        return result

    def get_kr_etf_constituents(self, stock_code: str, top_n: int = 5) -> List[str]:
        """KR ETF 상위 구성종목 이름 목록 (비중순).
        우선순위: naver coinfo(naver_code 등록 시) → wisereport → pykrx → top_holdings 하드코드"""
        naver_codes = self._load_etf_naver_codes()
        naver_code = naver_codes.get(stock_code)
        if naver_code:
            # ★ 1순위: wisereport (naver coinfo는 JS렌더링 필요해서 생략)
            result = self._get_kr_etf_constituents_wisereport(naver_code, top_n=top_n)
            if result:
                return result
            log.warning(f"ETF {stock_code}: 구성종목 wisereport 실패 (naver_code={naver_code})")

        if not _PYKRX_AVAILABLE:
            return []
        for delta in range(7):
            trd = (datetime.now() - timedelta(days=delta)).strftime("%Y%m%d")
            try:
                df = pykrx_stock.get_etf_portfolio_deposit_file(trd, stock_code)
                if df is None or df.empty:
                    continue
                # 컬럼명 동적 탐지 (pykrx 버전마다 다를 수 있음)
                name_col = next(
                    (c for c in df.columns if "종목명" in str(c) or c.lower() == "name"),
                    None,
                )
                weight_col = next(
                    (c for c in df.columns if "비중" in str(c) or "weight" in str(c).lower()),
                    None,
                )
                if name_col is None:
                    break
                if weight_col:
                    df = df.sort_values(weight_col, ascending=False)
                names = [
                    str(r[name_col]).strip()
                    for _, r in df.head(top_n).iterrows()
                    if str(r[name_col]).strip() not in ("", "nan", "None")
                    # 인코딩 깨진 이름(◆ 등 대체문자 포함) 제외
                    and "�" not in str(r[name_col])
                    and "◆" not in str(r[name_col])
                    and "?" not in str(r[name_col])
                ]
                if names:
                    log.info(f"ETF {stock_code} 구성종목 상위 {len(names)}개: {names}")
                    return names
            except Exception as e:
                log.debug(f"ETF 구성종목 조회 실패 ({stock_code}, {trd}): {e}")
        # pykrx 실패 시 KR_ETF_UNIVERSE 하드코드 top_holdings fallback
        for etf in KR_ETF_UNIVERSE:
            if etf.get("code") == stock_code:
                holdings = etf.get("top_holdings", [])
                if holdings:
                    log.info(f"ETF {stock_code} 구성종목 하드코드 fallback: {holdings[:top_n]}")
                    return holdings[:top_n]
                break
        return []

    def get_us_etf_metrics(self, ticker: str) -> Optional[Dict]:
        """US ETF 전용: yfinance .info 기반. ETF 아니면 None."""
        try:
            info = yf.Ticker(ticker).info or {}
            if info.get("quoteType") not in ("ETF", "MUTUALFUND"):
                return None

            def _f(key):
                v = info.get(key)
                return float(v) if isinstance(v, (int, float)) and v == v else None

            ta = _f("totalAssets")
            er = _f("expenseRatio")
            dy = _f("yield")
            ytd = _f("ytdReturn")
            r3 = _f("threeYearAverageReturn")
            r5 = _f("fiveYearAverageReturn")

            return {
                "is_etf": True,
                "market": "US",
                "fund_name": info.get("longName") or info.get("shortName"),
                "fund_family": info.get("fundFamily"),
                "category": info.get("category"),
                "total_assets_billion": round(ta / 1e9, 2) if ta else None,  # USD 십억
                "expense_ratio_pct": round(er * 100, 3) if er else None,
                "dividend_yield_pct": round(dy * 100, 2) if dy else None,
                "return_ytd": round(ytd * 100, 2) if ytd is not None else None,
                "return_3y_ann": round(r3 * 100, 2) if r3 is not None else None,
                "return_5y_ann": round(r5 * 100, 2) if r5 is not None else None,
                "beta": _f("beta3Year"),
            }
        except Exception as e:
            log.debug(f"US ETF 메트릭 실패 ({ticker}): {e}")
            return None

    def _get_us_annual_financials(self, ticker: str) -> Dict:
        """yfinance 연간 재무제표로 US 종목 정확한 지표 계산.
        영업이익률·매출성장률: 연간 Income Statement 기준 (분기 왜곡 방지)
        부채비율: (총자산-자기자본)/자기자본 = 총부채/자기자본"""
        result: Dict = {}
        try:
            tk = yf.Ticker(ticker)

            # 손익계산서 (연간)
            try:
                inc = tk.financials  # columns=연도 내림차순
                if inc is not None and not inc.empty and inc.shape[1] >= 1:
                    def _ival(df, *keys):
                        for k in keys:
                            if k in df.index:
                                v = df.loc[k].iloc[0]
                                try:
                                    f = float(v)
                                    if f == f:
                                        return f
                                except Exception:
                                    pass
                        return None

                    rev = _ival(inc, "Total Revenue", "Revenue")
                    # ★ "EBIT"를 폴백으로 쓰면 안 됨 — EBIT는 영업이익과 다른 개념(이자·법인세 차감전
                    # 이익으로, 비영업 손익까지 포함돼 영업이익보다 클 수 있음). 실제로 영업이익률이
                    # 매출총이익률보다 높게 나오는 논리적으로 불가능한 값이 나온 원인이 이 폴백이었음
                    # (2026-07 Micron 검증 사례: 영업이익률 80.37% > 매출총이익률 72.60%).
                    op_inc = _ival(inc, "Operating Income", "Operating Income Or Loss")
                    gross_profit = _ival(inc, "Gross Profit")
                    if rev and rev != 0 and op_inc is not None:
                        om = round(op_inc / rev * 100, 2)
                        # 안전장치: 영업이익률은 매출총이익률을 넘을 수 없음(정의상 영업이익=매출총이익-판관비)
                        if gross_profit is not None and rev != 0:
                            gm = gross_profit / rev * 100
                            if om > gm + 0.5:
                                log.warning(f"US 연간 영업이익률 이상치 폐기 ({ticker}): op_margin={om}% > gross_margin={gm:.2f}%")
                                om = None
                        if om is not None:
                            result["operating_margin_pct"] = om

                    if inc.shape[1] >= 2:
                        rev_prev = None
                        for k in ("Total Revenue", "Revenue"):
                            if k in inc.index:
                                v = inc.loc[k].iloc[1]
                                try:
                                    f = float(v)
                                    if f == f:
                                        rev_prev = f
                                        break
                                except Exception:
                                    pass
                        if rev and rev_prev and rev_prev != 0:
                            result["revenue_growth_pct"] = round((rev - rev_prev) / abs(rev_prev) * 100, 2)
            except Exception as e:
                log.debug(f"US income stmt 실패 ({ticker}): {e}")

            # 재무상태표 (연간)
            try:
                bal = tk.balance_sheet
                if bal is not None and not bal.empty and bal.shape[1] >= 1:
                    def _bval(*keys):
                        for k in keys:
                            if k in bal.index:
                                v = bal.loc[k].iloc[0]
                                try:
                                    f = float(v)
                                    if f == f:
                                        return f
                                except Exception:
                                    pass
                        return None

                    total_assets = _bval("Total Assets")
                    equity = _bval(
                        "Stockholders Equity",
                        "Total Stockholders Equity",
                        "Common Stock Equity",
                    )
                    if total_assets and equity and equity != 0:
                        total_liab = total_assets - equity
                        result["debt_to_equity_pct"] = round(total_liab / equity * 100, 2)
            except Exception as e:
                log.debug(f"US balance sheet 실패 ({ticker}): {e}")

        except Exception as e:
            log.debug(f"US annual financials 실패 ({ticker}): {e}")
        return result

    def _get_us_ttm_financials(self, ticker: str) -> Optional[Dict]:
        """최근 4개 분기 합산(TTM) 영업이익률 + 최신 분기 재무상태표 부채비율.
        _get_us_annual_financials()는 회계연도 '연간' 컬럼(최대 11개월 전 마감)을 쓰는데,
        메모리 반도체처럼 분기마다 마진이 급변하는 업종은 연간 수치가 현재 수익성을
        몇 배씩 과소/과대평가할 수 있음(예: 2026-07 Micron 사례 — 연간 영업이익률 26%대
        vs 최근 분기 67%대). TTM(최근 4분기 합산)으로 계절성은 완화하면서 최신성은 유지."""
        result: Dict = {}
        try:
            tk = yf.Ticker(ticker)

            # 손익: 최근 4개 분기 합산
            try:
                q_inc = tk.quarterly_financials  # columns=최근 분기부터 내림차순
                if q_inc is not None and not q_inc.empty:
                    def _sum4(keys):
                        for k in keys:
                            if k in q_inc.index:
                                s = q_inc.loc[k].dropna()
                                if len(s) >= 4:
                                    return float(s.iloc[:4].sum())
                        return None
                    ttm_rev = _sum4(["Total Revenue", "Revenue", "Operating Revenue"])
                    # ★ "EBIT" 폴백 제거 — EBIT는 이자·법인세 차감전 이익으로 비영업 손익까지
                    # 포함돼 진짜 영업이익보다 클 수 있음 (영업이익률이 매출총이익률을 넘는
                    # 논리적 모순의 원인이었음. 2026-07 Micron 검증: 80.37% > 매출총이익률 72.60%).
                    ttm_op = _sum4(["Operating Income", "Operating Income Or Loss"])
                    ttm_gross = _sum4(["Gross Profit"])
                    if ttm_rev and ttm_op is not None and ttm_rev != 0:
                        om = round(ttm_op / ttm_rev * 100, 2)
                        # 안전장치: 영업이익률이 매출총이익률을 넘으면(정의상 불가능) 폐기
                        if ttm_gross is not None:
                            gm = ttm_gross / ttm_rev * 100
                            if om > gm + 0.5:
                                log.warning(f"US TTM 영업이익률 이상치 폐기 ({ticker}): op_margin={om}% > gross_margin={gm:.2f}%")
                                om = None
                        if om is not None:
                            result["operating_margin_pct"] = om
            except Exception as e:
                log.debug(f"US TTM 손익 실패 ({ticker}): {e}")

            # 부채비율: 최신 "분기" 재무상태표 (연간 재무상태표도 사실 시점값이라
            # 회계연도 말 스냅샷일 뿐 — 분기 재무상태표가 항상 더 최신 시점)
            try:
                q_bal = tk.quarterly_balance_sheet
                if q_bal is not None and not q_bal.empty and q_bal.shape[1] >= 1:
                    def _bval(*keys):
                        for k in keys:
                            if k in q_bal.index:
                                v = q_bal.loc[k].iloc[0]
                                try:
                                    f = float(v)
                                    if f == f:
                                        return f
                                except Exception:
                                    pass
                        return None
                    total_assets = _bval("Total Assets")
                    equity = _bval("Stockholders Equity", "Total Stockholders Equity", "Common Stock Equity")
                    if total_assets and equity and equity != 0:
                        result["debt_to_equity_pct"] = round((total_assets - equity) / equity * 100, 2)
            except Exception as e:
                log.debug(f"US 분기 재무상태표 실패 ({ticker}): {e}")
        except Exception as e:
            log.debug(f"US TTM financials 실패 ({ticker}): {e}")

        return result or None

    def get_us_financials(self, ticker: str) -> Dict:
        """US 종목 재무제표 (yfinance 연간) → DART와 동일한 indicators/ratios 구조로 반환.
        analyze_financials 가 그대로 소비 가능."""
        try:
            tk = yf.Ticker(ticker)
            inc = tk.financials      # 손익계산서 (열=연도 내림차순)
            bs = tk.balance_sheet    # 재무상태표

            def _col(df, keys, i=0):
                if df is None or getattr(df, "empty", True) or df.shape[1] <= i:
                    return None
                for k in keys:
                    if k in df.index:
                        try:
                            v = float(df.loc[k].iloc[i])
                            if v == v:  # NaN 체크
                                return v
                        except Exception:
                            pass
                return None

            rev_c = _col(inc, ["Total Revenue", "Revenue", "Operating Revenue"], 0)
            rev_p = _col(inc, ["Total Revenue", "Revenue", "Operating Revenue"], 1)
            # ★ "EBIT" 폴백 제거 — EBIT ≠ 영업이익(비영업 손익 포함돼 더 클 수 있음).
            # 영업이익률이 매출총이익률을 넘는 논리적 모순의 원인이었음(2026-07 Micron 검증).
            op_c  = _col(inc, ["Operating Income", "Operating Income Or Loss"], 0)
            op_p  = _col(inc, ["Operating Income", "Operating Income Or Loss"], 1)
            gross_c = _col(inc, ["Gross Profit"], 0)
            ni_c  = _col(inc, ["Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operation Net Minority Interest"], 0)
            ni_p  = _col(inc, ["Net Income", "Net Income Common Stockholders"], 1)
            eq_c  = _col(bs, ["Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity"], 0)
            li_c  = _col(bs, ["Total Liabilities Net Minority Interest", "Total Liab", "Total Liabilities"], 0)

            if not any([rev_c, op_c, ni_c]):
                return {"ticker": ticker, "error": "no US financials (yfinance)"}

            year = None
            try:
                year = int(str(inc.columns[0])[:4])
            except Exception:
                pass

            ratios = {}
            if rev_c and op_c is not None:
                om = round(op_c / rev_c * 100, 2)
                # 안전장치: 영업이익률은 매출총이익률을 넘을 수 없음(정의상)
                if gross_c is not None:
                    gm = gross_c / rev_c * 100
                    if om > gm + 0.5:
                        log.warning(f"US 영업이익률 이상치 폐기 ({ticker}): op_margin={om}% > gross_margin={gm:.2f}%")
                        om = None
                if om is not None:
                    ratios["operating_margin_pct"] = om
            if rev_c and ni_c is not None:
                ratios["net_margin_pct"] = round(ni_c / rev_c * 100, 2)
            if eq_c and li_c is not None and eq_c != 0:
                ratios["debt_to_equity_pct"] = round(li_c / eq_c * 100, 2)

            return {
                "ticker": ticker,
                "year": year,
                "source": "yfinance",
                "indicators": {
                    "revenue": {"current": rev_c, "previous": rev_p},
                    "operating_income": {"current": op_c, "previous": op_p},
                    "net_income": {"current": ni_c, "previous": ni_p},
                    "total_equity": {"current": eq_c},
                    "total_liabilities": {"current": li_c},
                },
                "ratios": ratios,
            }
        except Exception as e:
            log.debug(f"US financials 실패 ({ticker}): {e}")
            return {"ticker": ticker, "error": f"us financials: {e}"}

    def collect_all(
        self,
        ticker: str,
        news_query: Optional[str] = None,
        stock_code: Optional[str] = None,
        year: Optional[int] = None,
    ) -> Dict:
        # ★ ETF 감지 — KR: pykrx 목록, US: yfinance quoteType
        etf_info = None
        kr_etf = stock_code and is_kr_etf(stock_code)

        price = self.get_price_data(ticker)
        # 뉴스: KR=네이버, US=yfinance(영어) → 비면 네이버 폴백
        if not news_query:
            news = None
        elif _is_kr_ticker(ticker):
            news = self.get_news_data(news_query, display=25)
        else:
            news = self.get_us_news(ticker, display=25)
            if not (news and news.get("items")):
                news = self.get_news_data(news_query, display=25)
        # ETF는 DART 재무제표 불필요
        # 재무제표: KR=DART, US=yfinance 연간, ETF=불필요
        if kr_etf:
            fin = None
        elif _is_kr_ticker(ticker):
            fin = self.get_financial_statements(stock_code, year) if stock_code else None
        else:
            fin = self.get_us_financials(ticker)
        mm = self.get_market_metrics(ticker)

        etf_constituent_news = None  # ETF 구성종목 뉴스 (별도 키)
        if kr_etf:
            etf_info = self.get_kr_etf_metrics(stock_code)
            # ★ ETF 구성종목 뉴스 별도 수집 (상위 3개 종목, 각 5건)
            # ETF 자체 뉴스(news)와 분리해서 앱에 각각 3개씩 표시
            constituents = self.get_kr_etf_constituents(stock_code, top_n=3)
            const_items: List[Dict] = []
            for cname in constituents:
                try:
                    cnews = self.get_news_data(cname, display=5)
                    extra = cnews.get("items") or []
                    for it in extra:
                        it["_constituent"] = cname
                        it["title"] = f"[{cname}] {it.get('title', '')}"
                    const_items.extend(extra)
                except Exception as e:
                    log.debug(f"ETF 구성종목 뉴스 실패 ({cname}): {e}")
            if const_items:
                etf_constituent_news = {"items": const_items}
            # 구성종목 이름 리스트를 etf_info에도 저장 (리포트 텍스트에 포함)
            if constituents and etf_info:
                etf_info["constituents"] = constituents
        elif not stock_code:
            # US 종목 — ETF 여부 mm 에서 확인 (yfinance quoteType)
            us_etf = self.get_us_etf_metrics(ticker)
            if us_etf:
                etf_info = us_etf

        # ★ KR 종목: 네이버 금융을 1순위로 — yfinance 한국 데이터 부정확 문제 해결
        # _ns_cache: get_summary 결과 캐시 (이 함수 호출 1회만 API 요청)
        # ★ _is_kr_ticker(ticker) 추가(2026-07-30) — stock_code는 US 티커도 항상
        # truthy(예: "MU")라서 이 가드만으론 US 종목에서도 불필요하게 Naver를
        # "MU"라는 잘못된 코드로 조회 시도했었음 (아래 US 전용 override 블록과 동일 버그).
        _ns_cache: Optional[Dict] = None
        if stock_code and _is_kr_ticker(ticker) and isinstance(mm, dict) and _NAVER_AVAILABLE and hasattr(_naver, "get_summary"):
            try:
                _ns_cache = _naver.get_summary(stock_code)
                ns = _ns_cache
                if ns:
                    if ns.get("per") is not None:
                        mm["per"]        = ns["per"]
                        mm["per_source"] = "naver_scrape"
                        mm["per_basis"]  = "TTM"
                    if ns.get("pbr") is not None:
                        mm["pbr"]        = ns["pbr"]
                        mm["pbr_source"] = "naver_scrape"
                        mm["pbr_basis"]  = "분기말"
                    if ns.get("eps") is not None:
                        mm["eps"] = ns["eps"]
                    if ns.get("bps") is not None:
                        mm["bps"] = ns["bps"]
                    if ns.get("dividend_yield_pct") is not None:
                        mm["dividend_yield_pct"] = ns["dividend_yield_pct"]
                        mm["dividend_yield_source"] = "naver_scrape"
                    if ns.get("operating_margin_pct") is not None:
                        mm["operating_margin_pct"]    = ns["operating_margin_pct"]
                        mm["operating_margin_source"] = "naver_scrape"
                        mm["operating_margin_basis"]  = "연간"
                    if ns.get("roe_pct") is not None:
                        mm["roe_pct"]    = ns["roe_pct"]
                        mm["roe_source"] = "naver_scrape"
                        mm["roe_basis"]  = "연간"
                    if ns.get("revenue_growth_pct") is not None:
                        mm["revenue_growth_pct"]    = ns["revenue_growth_pct"]
                        mm["revenue_growth_source"] = "naver_scrape"
                        mm["revenue_growth_basis"]  = "YoY"
            except Exception as e:
                log.debug(f"Naver summary 1순위 실패 ({stock_code}): {e}")

        # KR 종목이고 네이버에서도 PER/PBR/시총을 못 채웠으면 KRX 직통 폴백
        if stock_code and _is_kr_ticker(ticker) and isinstance(mm, dict):
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
                        mm["per"]        = krx["per"]
                        mm["per_source"] = "krx"
                        mm["per_basis"]  = "TTM"
                    if mm.get("pbr") is None and krx.get("pbr") is not None:
                        mm["pbr"]        = krx["pbr"]
                        mm["pbr_source"] = "krx"
                        mm["pbr_basis"]  = "분기말"
                    mm["eps"] = krx.get("eps")
                    mm["bps"] = krx.get("bps")
                    mm["shares_outstanding"] = krx.get("shares_outstanding")

        # KRX 직통도 못 채웠으면 pykrx 폴백 (코스닥 소형주 강함)
        if stock_code and _is_kr_ticker(ticker) and isinstance(mm, dict):
            still_need = mm.get("per") is None or mm.get("pbr") is None
            if still_need:
                pk = self.get_kr_fundamentals_pykrx(stock_code)
                if pk:
                    if mm.get("per") is None and pk.get("per") is not None:
                        mm["per"]        = pk["per"]
                        mm["per_source"] = "pykrx"
                        mm["per_basis"]  = "TTM"
                    if mm.get("pbr") is None and pk.get("pbr") is not None:
                        mm["pbr"]        = pk["pbr"]
                        mm["pbr_source"] = "pykrx"
                        mm["pbr_basis"]  = "분기말"
                    if mm.get("eps") is None and pk.get("eps") is not None:
                        mm["eps"] = pk["eps"]
                    if mm.get("bps") is None and pk.get("bps") is not None:
                        mm["bps"] = pk["bps"]
                    if mm.get("dividend_yield_pct") is None and pk.get("div_yield_pct") is not None:
                        mm["dividend_yield_pct"] = pk["div_yield_pct"]
                        mm["dividend_yield_source"] = "pykrx"

        # pykrx 까지도 못 채우면 Naver Finance 페이지 스크래핑 (최후 수단)
        # ★ PER/PBR뿐 아니라 영업이익률·ROE·매출성장률도 Naver에서 채움
        if stock_code and _is_kr_ticker(ticker) and isinstance(mm, dict) and _NAVER_AVAILABLE and hasattr(_naver, "get_summary"):
            need_basic = mm.get("per") is None or mm.get("pbr") is None
            need_margins = (
                mm.get("operating_margin_pct") is None
                or mm.get("roe_pct") is None
                or mm.get("revenue_growth_pct") is None
            )
            if need_basic or need_margins:
                try:
                    # ★ _ns_cache 재사용 — API 중복 호출 방지
                    ns = _ns_cache if _ns_cache is not None else _naver.get_summary(stock_code)
                    if ns:
                        if mm.get("per") is None and ns.get("per") is not None:
                            mm["per"]        = ns["per"]
                            mm["per_source"] = "naver_scrape"
                            mm["per_basis"]  = "TTM"
                        if mm.get("pbr") is None and ns.get("pbr") is not None:
                            mm["pbr"]        = ns["pbr"]
                            mm["pbr_source"] = "naver_scrape"
                            mm["pbr_basis"]  = "분기말"
                        if mm.get("eps") is None and ns.get("eps") is not None:
                            mm["eps"] = ns["eps"]
                        if mm.get("bps") is None and ns.get("bps") is not None:
                            mm["bps"] = ns["bps"]
                        if mm.get("dividend_yield_pct") is None and ns.get("dividend_yield_pct") is not None:
                            mm["dividend_yield_pct"] = ns["dividend_yield_pct"]
                            mm["dividend_yield_source"] = "naver_scrape"
                        # ★ 추가: 영업이익률·ROE·매출성장률
                        if mm.get("operating_margin_pct") is None and ns.get("operating_margin_pct") is not None:
                            mm["operating_margin_pct"]    = ns["operating_margin_pct"]
                            mm["operating_margin_source"] = "naver_scrape"
                            mm["operating_margin_basis"]  = "연간"
                        if mm.get("roe_pct") is None and ns.get("roe_pct") is not None:
                            mm["roe_pct"]    = ns["roe_pct"]
                            mm["roe_source"] = "naver_scrape"
                            mm["roe_basis"]  = "연간"
                        if mm.get("revenue_growth_pct") is None and ns.get("revenue_growth_pct") is not None:
                            mm["revenue_growth_pct"]    = ns["revenue_growth_pct"]
                            mm["revenue_growth_source"] = "naver_scrape"
                            mm["revenue_growth_basis"]  = "YoY"
                except Exception as e:
                    log.debug(f"Naver summary fallback 실패 ({stock_code}): {e}")

        # ★ _is_kr_ticker(ticker) 추가 — fin(get_us_financials)도 "ratios" 키를 갖고
        # 있어서 이 가드가 없으면 US 종목에서 값이 None일 때 "dart_calc"로 잘못 라벨링됨
        if fin and "ratios" in fin and _is_kr_ticker(ticker) and isinstance(mm, dict):
            r = fin["ratios"]
            if mm.get("roe_pct") is None and r.get("roe_pct") is not None:
                mm["roe_pct"]    = r["roe_pct"]
                mm["roe_source"] = "dart_calc"
                mm["roe_basis"]  = "연간"
            mc = mm.get("market_cap")
            ni = fin["indicators"].get("net_income", {}).get("current")
            eq = fin["indicators"].get("total_equity", {}).get("current")
            if mm.get("per") is None and mc and ni and ni > 0:
                mm["per"]        = round(mc / ni, 2)
                mm["per_source"] = "dart_calc"
                mm["per_basis"]  = "연간"
            if mm.get("pbr") is None and mc and eq and eq > 0:
                mm["pbr"]        = round(mc / eq, 2)
                mm["pbr_source"] = "dart_calc"
                mm["pbr_basis"]  = "분기말"

        # ★ KR 종목 TTM: PER / ROE / 영업이익률을 TTM 기준으로 통일
        if stock_code and _is_kr_ticker(ticker) and fin and not fin.get("error") and isinstance(mm, dict):
            mc = mm.get("market_cap")
            ttm = self._get_ttm_income_statement(stock_code, fin)
            if ttm:
                ttm_ni  = ttm["ttm_net_income"]
                ttm_rev = ttm["ttm_revenue"]
                ttm_op  = ttm["ttm_operating_income"]
                eq_c    = ttm["equity_curr"]
                eq_p    = ttm["equity_prev"]

                # TTM PER
                if mc and ttm_ni > 0:
                    mm["per"]        = round(mc / ttm_ni, 2)
                    mm["per_source"] = "dart_ttm"
                    mm["per_basis"]  = "TTM"
                    log.debug(f"TTM PER ({stock_code}): mc={mc}, ni={ttm_ni}, per={mm['per']}")

                # TTM ROE = TTM순이익 / 평균자기자본 (음수 자본/음수 이익 모두 허용)
                if eq_c is not None and eq_p is not None:
                    avg_eq = (eq_c + eq_p) / 2
                    if avg_eq != 0:
                        mm["roe_pct"]    = round(ttm_ni / avg_eq * 100, 2)
                        mm["roe_source"] = "dart_ttm"
                        mm["roe_basis"]  = "TTM"

                # TTM 영업이익률 (음수 허용)
                if ttm_rev and ttm_op is not None and ttm_rev != 0:
                    mm["operating_margin_pct"]    = round(ttm_op / ttm_rev * 100, 2)
                    mm["operating_margin_source"] = "dart_ttm"
                    mm["operating_margin_basis"]  = "TTM"

                # PBR = 현재 시총 / 최신 분기 자본총계 (stale KRX BPS 대체)
                latest_eq = ttm.get("latest_equity")
                if mc and latest_eq and latest_eq > 0:
                    mm["pbr"]        = round(mc / latest_eq, 2)
                    mm["pbr_source"] = "dart_q1"
                    mm["pbr_basis"]  = "분기말"

                # 부채비율 = 최신 분기 부채총계 / 최신 분기 자본총계 (연간 → 분기 교체)
                latest_li = ttm.get("latest_liabilities")
                if latest_li and latest_eq and latest_eq != 0:
                    mm["debt_to_equity_pct"]    = round(latest_li / latest_eq * 100, 2)
                    mm["debt_to_equity_source"] = "dart_q1"
                    mm["debt_to_equity_basis"]  = "분기말"

        # ★ US 종목: yfinance 재무제표로 정확한 지표 덮어쓰기
        # revenueGrowth(분기YoY)/operatingMargins(분기)/debtToEquity(금융부채만) 오류 수정
        # 매출성장률은 연간 YoY(계절성 완화 목적으로 유지), 영업이익률·부채비율은
        # TTM/최신분기 우선 — 연간(최대 11개월 전 마감) 그대로 쓰면 마진 급변 업종(예:
        # 메모리 반도체 슈퍼사이클)에서 실제 수익성을 몇 배씩 틀리게 보여줄 수 있음
        # (2026-07 Micron 실사례: 연간 26%대 vs 최근 분기 67%대 영업이익률).
        # ★★ 진짜 원인 발견(2026-07-30): find_watch_entry()가 US 티커도 code 필드에
        # 항상 값을 채움(KR=6자리 숫자코드, US=티커심볼 자체, 예 "MU") — 즉 stock_code는
        # US 종목에서도 항상 truthy라서 "not stock_code"가 절대 True가 안 되고 이 블록
        # 전체가 매번 스킵되고 있었음. yfinance의 원본 info["operatingMargins"]/
        # ["debtToEquity"]가 그대로(80.37%/6.33 등) 노출된 이유가 이것 — EBIT 폴백
        # 제거나 TTM 계산 자체는 다 맞았는데 애초에 실행이 안 됐던 것. 아래 다른 곳(fin
        # 변수 할당)처럼 ticker 형식(_is_kr_ticker)으로 판별하도록 수정.
        if not _is_kr_ticker(ticker) and isinstance(mm, dict):
            us_fin = self._get_us_annual_financials(ticker)
            if us_fin and us_fin.get("revenue_growth_pct") is not None:
                mm["revenue_growth_pct"]    = us_fin["revenue_growth_pct"]
                mm["revenue_growth_source"] = "yf_annual"
                mm["revenue_growth_basis"]  = "연간 YoY"

            us_ttm = self._get_us_ttm_financials(ticker)
            if us_ttm and us_ttm.get("operating_margin_pct") is not None:
                mm["operating_margin_pct"]    = us_ttm["operating_margin_pct"]
                mm["operating_margin_source"] = "yf_ttm"
                mm["operating_margin_basis"]  = "TTM"
            elif us_fin and us_fin.get("operating_margin_pct") is not None:
                # TTM(분기 4개) 데이터를 못 구하면 연간으로 폴백 (없는 것보단 나음)
                mm["operating_margin_pct"]    = us_fin["operating_margin_pct"]
                mm["operating_margin_source"] = "yf_annual"
                mm["operating_margin_basis"]  = "연간"

            if us_ttm and us_ttm.get("debt_to_equity_pct") is not None:
                mm["debt_to_equity_pct"]    = us_ttm["debt_to_equity_pct"]
                mm["debt_to_equity_source"] = "yf_quarterly"
                mm["debt_to_equity_basis"]  = "분기말"
            elif us_fin and us_fin.get("debt_to_equity_pct") is not None:
                mm["debt_to_equity_pct"]    = us_fin["debt_to_equity_pct"]
                mm["debt_to_equity_source"] = "yf_annual"
                mm["debt_to_equity_basis"]  = "분기말(연간보고서 시점)"

        return {
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "price": price,
            "news": news,
            "financials": fin,
            "market_metrics": mm,
            "etf_info": etf_info,                       # ETF면 Dict, 아니면 None
            "etf_constituent_news": etf_constituent_news,  # ETF 구성종목 뉴스 (별도)
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

# ─────────────────────────────────────────────────────────────
# ETF / 레버리지 / 인버스 유니버스
# ─────────────────────────────────────────────────────────────
US_ETF_UNIVERSE: List[Dict] = [
    # S&P 500 계열
    {"code": "VOO",  "name": "Vanguard S&P 500 ETF",         "market": "NYSEARCA", "type": "ETF"},
    {"code": "IVV",  "name": "iShares S&P 500 ETF",          "market": "NYSEARCA", "type": "ETF"},
    {"code": "SPXL", "name": "S&P 500 3x 레버리지 (SPXL)",   "market": "NYSEARCA", "type": "LEV"},
    {"code": "UPRO", "name": "S&P 500 3x 레버리지 (UPRO)",   "market": "NYSEARCA", "type": "LEV"},
    {"code": "SSO",  "name": "S&P 500 2x 레버리지 (SSO)",    "market": "NYSEARCA", "type": "LEV"},
    {"code": "SPXS", "name": "S&P 500 3x 인버스 (SPXS)",     "market": "NYSEARCA", "type": "INV"},
    {"code": "SH",   "name": "S&P 500 인버스 (SH)",           "market": "NYSEARCA", "type": "INV"},
    # 나스닥 계열
    {"code": "TQQQ", "name": "Nasdaq 100 3x 레버리지 (TQQQ)", "market": "NASDAQ",  "type": "LEV"},
    {"code": "QLD",  "name": "Nasdaq 100 2x 레버리지 (QLD)",  "market": "NASDAQ",  "type": "LEV"},
    {"code": "SQQQ", "name": "Nasdaq 100 3x 인버스 (SQQQ)",   "market": "NASDAQ",  "type": "INV"},
    {"code": "PSQ",  "name": "Nasdaq 100 인버스 (PSQ)",        "market": "NASDAQ",  "type": "INV"},
    # 반도체
    {"code": "SOXX", "name": "반도체 ETF (SOXX)",              "market": "NASDAQ",  "type": "ETF"},
    {"code": "SMH",  "name": "반도체 ETF (SMH)",               "market": "NYSEARCA", "type": "ETF"},
    {"code": "SOXL", "name": "반도체 3x 레버리지 (SOXL)",     "market": "NYSEARCA", "type": "LEV"},
    {"code": "SOXS", "name": "반도체 3x 인버스 (SOXS)",       "market": "NYSEARCA", "type": "INV"},
    # 소형주/다우
    {"code": "TNA",  "name": "Russell 2000 3x 레버리지 (TNA)", "market": "NYSEARCA", "type": "LEV"},
    # 테크/성장
    {"code": "XLK",  "name": "테크 섹터 ETF (XLK)",            "market": "NYSEARCA", "type": "ETF"},
    {"code": "TECL", "name": "테크 3x 레버리지 (TECL)",        "market": "NYSEARCA", "type": "LEV"},
    {"code": "ARKK", "name": "ARK Innovation ETF",             "market": "NYSEARCA", "type": "ETF"},
    # 채권/금리
    {"code": "TLT",  "name": "장기국채 ETF (TLT)",             "market": "NASDAQ",  "type": "ETF"},
    {"code": "TMF",  "name": "장기국채 3x 레버리지 (TMF)",     "market": "NYSEARCA", "type": "LEV"},
    {"code": "TBT",  "name": "장기국채 2x 인버스 (TBT)",       "market": "NYSEARCA", "type": "INV"},
    # 원자재/금
    {"code": "GLD",  "name": "금 ETF (GLD)",                   "market": "NYSEARCA", "type": "ETF"},
    {"code": "NUGT", "name": "금광주 2x 레버리지 (NUGT)",      "market": "NYSEARCA", "type": "LEV"},
    {"code": "USO",  "name": "원유 ETF (USO)",                 "market": "NYSEARCA", "type": "ETF"},
    # 변동성
    {"code": "UVXY", "name": "VIX 1.5x 레버리지 (UVXY)",      "market": "NASDAQ",  "type": "LEV"},
    {"code": "SVXY", "name": "VIX 인버스 (SVXY)",              "market": "NYSEARCA", "type": "INV"},
]

# ─────────────────────────────────────────────────────────────
# 국내 ETF 유니버스 (KODEX / TIGER / SOL / ARIRANG / KBSTAR 등)
# pykrx 동적 검색 실패 시 폴백으로도 사용
# ─────────────────────────────────────────────────────────────
KR_ETF_UNIVERSE: List[Dict] = [
    # KODEX (삼성자산운용)
    {"code": "069500", "name": "KODEX 200",                           "market": "ETF", "type": "ETF"},
    {"code": "229200", "name": "KODEX 코스닥150",                     "market": "ETF", "type": "ETF"},
    {"code": "252670", "name": "KODEX 200선물인버스2X",               "market": "ETF", "type": "INV"},
    {"code": "233740", "name": "KODEX 코스닥150레버리지",             "market": "ETF", "type": "LEV"},
    {"code": "278540", "name": "KODEX MSCI Korea TR",                 "market": "ETF", "type": "ETF"},
    {"code": "091160", "name": "KODEX 반도체",                       "market": "ETF", "type": "ETF"},
    {"code": "266370", "name": "KODEX 200IT",                         "market": "ETF", "type": "ETF"},
    {"code": "364980", "name": "KODEX 2차전지산업",                   "market": "ETF", "type": "ETF"},
    {"code": "371460", "name": "KODEX AI반도체핵심장비",              "market": "ETF", "type": "ETF"},
    {"code": "463890", "name": "KODEX AI전력핵심인프라",              "market": "ETF", "type": "ETF"},
    {"code": "486290", "name": "KODEX AI핵심설비",                    "market": "ETF", "type": "ETF"},
    {"code": "487240", "name": "KODEX AI전력핵심설비",                "market": "ETF", "type": "ETF"},
    {"code": "489600", "name": "KODEX 미국AI반도체핵심기술",          "market": "ETF", "type": "ETF"},
    # TIGER (미래에셋자산운용)
    {"code": "102110", "name": "TIGER 200",                           "market": "ETF", "type": "ETF"},
    {"code": "232080", "name": "TIGER 코스닥150",                     "market": "ETF", "type": "ETF"},
    {"code": "143860", "name": "TIGER 코스닥150레버리지",             "market": "ETF", "type": "LEV"},
    {"code": "218420", "name": "TIGER 반도체",                       "market": "ETF", "type": "ETF",
     "top_holdings": ["SK하이닉스", "삼성전자", "DB하이텍", "리노공업", "HPSP"]},
    {"code": "305540", "name": "TIGER 2차전지테마",                   "market": "ETF", "type": "ETF",
     "top_holdings": ["LG에너지솔루션", "삼성SDI", "SK이노베이션", "에코프로비엠", "포스코퓨처엠"]},
    {"code": "381180", "name": "TIGER AI반도체핵심공정",              "market": "ETF", "type": "ETF",
     "top_holdings": ["한미반도체", "HPSP", "이오테크닉스", "원익IPS", "코미코"]},
    {"code": "473530", "name": "TIGER AI반도체TOP10",                 "market": "ETF", "type": "ETF",
     "top_holdings": ["삼성전자", "SK하이닉스", "한미반도체", "HPSP", "리노공업"]},
    {"code": "396500", "name": "TIGER 반도체TOP10",                   "market": "ETF", "type": "ETF",
     "top_holdings": ["SK하이닉스", "삼성전자", "HPSP", "리노공업", "DB하이텍"]},
    {"code": "494790", "name": "TIGER 피지컬AI",                     "market": "ETF", "type": "ETF",
     "top_holdings": ["레인보우로보틱스", "두산로보틱스", "HD현대로보틱스", "삼성전자", "LG전자"]},
    # SOL (신한자산운용)
    {"code": "476010", "name": "SOL 반도체AI TOP2 플러스",            "market": "ETF", "type": "ETF",
     "top_holdings": ["SK하이닉스", "삼성전자"]},
    {"code": "468380", "name": "SOL AI반도체칩메이커",                "market": "ETF", "type": "ETF",
     "top_holdings": ["엔비디아", "TSMC", "브로드컴", "SK하이닉스", "삼성전자"]},
    {"code": "442010", "name": "SOL 2차전지소부장Fn",                 "market": "ETF", "type": "ETF",
     "top_holdings": ["에코프로비엠", "포스코퓨처엠", "엘앤에프", "SK이노베이션", "삼성SDI"]},
    {"code": "411060", "name": "SOL 한국형글로벌반도체",              "market": "ETF", "type": "ETF"},
    # ARIRANG (한화자산운용)
    {"code": "152100", "name": "ARIRANG 200",                         "market": "ETF", "type": "ETF"},
    {"code": "253150", "name": "ARIRANG 코스닥150",                   "market": "ETF", "type": "ETF"},
    # KBSTAR (KB자산운용)
    {"code": "261220", "name": "KBSTAR 200",                          "market": "ETF", "type": "ETF"},
    {"code": "381170", "name": "KBSTAR AI&로봇",                      "market": "ETF", "type": "ETF"},
    # HANARO (NH아문디)
    {"code": "292150", "name": "HANARO 200",                          "market": "ETF", "type": "ETF"},
    {"code": "411900", "name": "HANARO 반도체TOP10",                  "market": "ETF", "type": "ETF"},
    # 현대차자산운용
    {"code": "494490", "name": "현대차 피지컬AI",                     "market": "ETF", "type": "ETF"},
    {"code": "486490", "name": "현대차 AI코어인프라",                  "market": "ETF", "type": "ETF"},
]


def to_yf_ticker(code: str, market: str = "KR") -> str:
    """6자리 한국 코드면 .KS / 미국 코드면 그대로."""
    code = (code or "").strip().upper()
    if code.isdigit() and len(code) == 6:
        return f"{code}.KS"
    return code


def _token_match(tokens: List[str], name: str) -> bool:
    """공백으로 분리된 토큰이 모두 이름에 포함되면 True (대소문자 무관).
    예) tokens=["현대차","피지컬"] → "현대차 피지컬AI Active" 매칭"""
    name_u = name.upper()
    return all(t in name_u for t in tokens)


def search_stocks(query: str, limit: int = 20) -> List[Dict]:
    """종목 코드/이름 부분 일치 검색. 국장 + 미국 + ETF/레버리지 통합.
    유니버스에 없는 티커를 정확히 입력한 경우에도 결과를 반환한다."""
    q = (query or "").strip()
    if not q:
        return []
    qu = q.upper()
    # 공백 토큰 분리 (ETF 이름 부분 검색에 활용)
    q_tokens = [t for t in qu.split() if t]
    out: List[Dict] = []
    seen: set = set()

    def _matches(name: str, code: str) -> bool:
        nu = name.upper()
        return (qu in code.upper() or qu in nu or
                (len(q_tokens) > 1 and _token_match(q_tokens, name)))

    # 1) KR 주식 (하드코딩 — 정확)
    for s in KR_STOCK_UNIVERSE:
        code = s.get("code", "")
        name = s.get("name", "")
        if _matches(name, code) and code not in seen:
            seen.add(code)
            out.append({"code": code, "name": name, "market": s.get("market"),
                        "ticker": to_yf_ticker(code), "region": "KR", "type": s.get("type", "STOCK")})
        if len(out) >= limit:
            return out

    # 2) KR ETF — pykrx 동적 목록 우선 (KRX 실제 이름, 신규 ETF 자동 포함)
    for etf in get_kr_etf_info_list():
        code = etf.get("code", "")
        name = etf.get("name", "")
        if _matches(name, code) and code not in seen:
            seen.add(code)
            out.append({"code": code, "name": name, "market": "ETF",
                        "ticker": f"{code}.KS", "region": "KR", "type": "ETF"})
        if len(out) >= limit:
            return out

    # 3) KR ETF — 하드코딩 fallback (pykrx 로드 전 or 누락 코드용)
    for s in KR_ETF_UNIVERSE:
        code = s.get("code", "")
        name = s.get("name", "")
        if _matches(name, code) and code not in seen:
            seen.add(code)
            out.append({"code": code, "name": name, "market": s.get("market"),
                        "ticker": to_yf_ticker(code), "region": "KR", "type": s.get("type", "ETF")})
        if len(out) >= limit:
            return out

    # 4) 미국
    for s in US_STOCK_UNIVERSE + US_ETF_UNIVERSE:
        code = s.get("code", "")
        name = s.get("name", "")
        if _matches(name, code) and code not in seen:
            seen.add(code)
            out.append({"code": code, "name": name, "market": s.get("market"),
                        "ticker": to_yf_ticker(code), "region": "KR" if code.isdigit() and len(code) == 6 else "US",
                        "type": s.get("type", "STOCK")})
        if len(out) >= limit:
            return out

    # 유니버스에 없는 티커를 정확히 입력한 경우 — 그대로 허용
    if not out and qu:
        if qu.isdigit() and len(qu) == 6:
            # 6자리 숫자: KR 종목(주식 or ETF) 직접 입력
            out.append({
                "code": qu,
                "name": qu,
                "market": "KR",
                "ticker": f"{qu}.KS",
                "region": "KR",
                "type": "UNKNOWN",
            })
        elif not qu.isdigit():
            out.append({
                "code": qu,
                "name": qu,
                "market": "US",
                "ticker": qu,
                "region": "US",
                "type": "UNKNOWN",
            })
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
