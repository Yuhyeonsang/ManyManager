"""
전체 종목 유니버스 (동적, 일 1회 자동 갱신).

소스:
  - KR (KOSPI/KOSDAQ): pykrx get_market_ticker_list + get_market_ticker_name
  - US (NASDAQ/NYSE) : nasdaqtrader.com 공식 심볼 디렉토리 (키 불필요, 매일 갱신)

특징:
  - 결과를 universe_cache.json 에 캐시 (date 기록). 같은 날이면 재사용, 날 바뀌면 재빌드.
  - 재빌드 = 그날의 전체 상장 목록 → 신규 상장 자동 추가 / 폐지 자동 제거.
  - 빌드 실패 시 직전 캐시 유지. 캐시도 없으면 빈 리스트(호출측이 하드코딩 폴백).

사용:
  from universe import get_indexes
  by_code, by_name = get_indexes()   # {code/symbol(대문자): entry}, {name(소문자): entry}
  entry = by_code.get("005930")      # {"code","name","market","region"}
"""
import os
import json
import logging
import datetime

import requests

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universe_cache.json")

_NASDAQ_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_LISTED = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
# otherlisted Exchange 코드 → 우리가 쓰는 시장명 (NYSE 계열만)
_EXCH_MAP = {"N": "NYSE", "A": "NYSE American", "P": "NYSE Arca"}

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


# ─────────────────────────────────────────────
# 소스별 수집
# ─────────────────────────────────────────────
def _fetch_kr():
    """KR(코스피/코스닥) 전체 종목. 1순위: 로그인 불필요한 KRX 상장목록 CSV,
    실패 시 pykrx 폴백(신버전 pykrx는 KRX_ID/PW 요구해 실패할 수 있음)."""
    out = _fetch_kr_krx_csv()
    if out:
        return out
    logger.warning("[universe] KRX CSV 실패 — pykrx 폴백 시도")
    return _fetch_kr_pykrx()


def _norm_kr_market(market_id, market_txt):
    s = (market_id or "").strip().upper()
    if s == "STK":
        return "KOSPI"
    if s == "KSQ":
        return "KOSDAQ"
    if s == "KNX":
        return "KONEX"
    t = (market_txt or "").strip()
    tu = t.upper()
    if "KOSPI" in tu or "\ucf54\uc2a4\ud53c" in t:
        return "KOSPI"
    if "KOSDAQ" in tu or "\ucf54\uc2a4\ub2e5" in t:
        return "KOSDAQ"
    return None


def _fetch_kr_krx_csv():
    """FinanceData가 GitHub에 매일 올리는 KRX 상장목록 CSV에서 코스피/코스닥 전 종목.
    최근 거래일 파일을 역순 탐색(주말·휴장 건너뜀)."""
    import io
    try:
        import pandas as pd
    except Exception as e:
        logger.warning("[universe] pandas 없음: %s", e)
        return []
    base = ("https://raw.githubusercontent.com/FinanceData/fdr_krx_data_cache/"
            "refs/heads/master/data/listing/krx/{date}.csv")
    df = None
    for i in range(0, 15):
        d = (datetime.date.today() - datetime.timedelta(days=i)).isoformat()
        try:
            r = requests.get(base.format(date=d), headers=_HEADERS, timeout=20)
            if r.status_code == 200 and len(r.content) > 1000:
                df = pd.read_csv(io.BytesIO(r.content), dtype=str)
                logger.info("[universe] KRX 목록 CSV: %s (%d행)", d, len(df))
                break
        except Exception as e:
            logger.warning("[universe] KRX CSV %s 실패: %s", d, e)
    if df is None:
        return []
    cols = {c.strip().lower(): c for c in df.columns}
    code_col = cols.get("code")
    name_col = cols.get("name")
    mid_col = cols.get("marketid")
    mkt_col = cols.get("market")
    if not code_col or not name_col:
        logger.warning("[universe] KRX CSV 컬럼 미상: %s", list(df.columns))
        return []
    out = []
    for _, row in df.iterrows():
        code = str(row[code_col]).strip()
        name = str(row[name_col]).strip()
        if not code or code.lower() == "nan" or not name or name.lower() == "nan":
            continue
        code = code.zfill(6)
        market = _norm_kr_market(
            str(row[mid_col]) if mid_col else "",
            str(row[mkt_col]) if mkt_col else "",
        )
        if market in ("KOSPI", "KOSDAQ"):
            out.append({"code": code, "name": name, "market": market, "region": "KR"})
    logger.info("[universe] KR 수집(CSV): %d종목", len(out))
    return out


def _fetch_kr_pykrx():
    """pykrx 폴백."""
    try:
        from pykrx import stock  # type: ignore
    except Exception as e:
        logger.warning("[universe] pykrx 미설치: %s", e)
        return []
    out = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            codes = stock.get_market_ticker_list(market=market)
        except Exception as e:
            logger.warning("[universe] pykrx ticker list 실패 (%s): %s", market, e)
            continue
        for code in codes:
            try:
                name = stock.get_market_ticker_name(code)
            except Exception:
                name = None
            if name:
                out.append({"code": str(code), "name": str(name).strip(),
                            "market": market, "region": "KR"})
    logger.info("[universe] KR 수집(pykrx): %d종목", len(out))
    return out


def _parse_pipe(text, want_exchange_col=False):
    """nasdaqtrader pipe 파일 파싱. 헤더 1줄 + 'File Creation Time' 푸터 제외."""
    rows = []
    lines = text.splitlines()
    if not lines:
        return rows
    for line in lines[1:]:
        if not line or line.startswith("File Creation Time"):
            continue
        f = line.split("|")
        if len(f) < 2:
            continue
        sym = f[0].strip()
        name = f[1].strip()
        if not sym or not name:
            continue
        exch = f[2].strip() if (want_exchange_col and len(f) > 2) else None
        rows.append((sym, name, exch))
    return rows


def _fetch_us():
    """nasdaqtrader 공식 디렉토리로 NASDAQ + NYSE 전체."""
    out = []
    # NASDAQ
    try:
        r = requests.get(_NASDAQ_LISTED, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        for sym, name, _ in _parse_pipe(r.text, want_exchange_col=False):
            out.append({"code": sym, "name": name, "market": "NASDAQ", "region": "US"})
    except Exception as e:
        logger.warning("[universe] nasdaqlisted 실패: %s", e)
    # NYSE 계열 (otherlisted: Exchange 컬럼으로 구분)
    try:
        r = requests.get(_OTHER_LISTED, headers=_HEADERS, timeout=20)
        r.raise_for_status()
        for sym, name, exch in _parse_pipe(r.text, want_exchange_col=True):
            mk = _EXCH_MAP.get(exch)
            if mk:
                out.append({"code": sym, "name": name, "market": mk, "region": "US"})
    except Exception as e:
        logger.warning("[universe] otherlisted 실패: %s", e)
    logger.info("[universe] US 수집: %d종목", len(out))
    return out


def _build():
    return _fetch_kr() + _fetch_us()


# ─────────────────────────────────────────────
# 캐시 + 로드
# ─────────────────────────────────────────────
def _read_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def load_universe(force=False):
    """오늘자 캐시 있으면 재사용, 없으면(or force) 재빌드.
    빌드 실패 시 직전 캐시, 그것도 없으면 빈 리스트."""
    today = datetime.date.today().isoformat()
    cache = _read_cache()
    if not force and cache and cache.get("date") == today and cache.get("stocks"):
        return cache["stocks"]

    try:
        stocks = _build()
    except Exception as e:
        logger.warning("[universe] 빌드 예외: %s", e)
        stocks = []

    if len(stocks) > 100:  # sanity — 부분 실패로 빈약하면 캐시 유지
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({"date": today, "count": len(stocks), "stocks": stocks},
                          f, ensure_ascii=False)
            logger.info("[universe] 갱신 완료: %d종목 (%s)", len(stocks), today)
        except Exception as e:
            logger.warning("[universe] 캐시 저장 실패: %s", e)
        return stocks

    logger.warning("[universe] 빌드 결과 빈약(%d) — 직전 캐시 사용", len(stocks))
    if cache and cache.get("stocks"):
        return cache["stocks"]
    return []


# ─────────────────────────────────────────────
# 빠른 조회 인덱스 (당일 1회 구성)
# ─────────────────────────────────────────────
_index = None        # (by_code, by_name)
_index_date = None


def get_indexes(force=False):
    """({code/symbol(UPPER): entry}, {name(lower): entry}) 반환."""
    global _index, _index_date
    today = datetime.date.today().isoformat()
    if not force and _index is not None and _index_date == today:
        return _index
    stocks = load_universe(force=force)
    by_code, by_name = {}, {}
    for s in stocks:
        c = (s.get("code") or "").upper()
        n = (s.get("name") or "").strip().lower()
        if c and c not in by_code:
            by_code[c] = s
        if n and n not in by_name:
            by_name[n] = s
    _index = (by_code, by_name)
    _index_date = today
    return _index


def lookup_by_code(code):
    by_code, _ = get_indexes()
    key = str(code or "").split(".")[0].upper()
    return by_code.get(key)


def lookup_by_name(name):
    _, by_name = get_indexes()
    return by_name.get(str(name or "").strip().lower())


def stats():
    s = load_universe()
    by_mkt = {}
    for x in s:
        by_mkt[x.get("market", "?")] = by_mkt.get(x.get("market", "?"), 0) + 1
    return {"total": len(s), "by_market": by_mkt}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json as _j
    print(_j.dumps(stats(), ensure_ascii=False, indent=2))
