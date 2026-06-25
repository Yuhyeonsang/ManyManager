"""
종목 선정 빌더 — 4가지 모드 지원

  volume           : 거래대금 상위 (기존 방식)
  news_categories  : 카테고리별 핫 뉴스 → Gemini 추론
  news_keywords    : 사용자 키워드 → Naver 검색 → Gemini 추론
  hybrid           : 뉴스 추론 + 거래량 혼합

설정은 SQLite app_settings 테이블에 저장됨.
"""
import os
import json
import sqlite3
from typing import List, Dict, Optional

from data_collector import (
    StockDataCollector, KR_STOCK_UNIVERSE, US_STOCK_UNIVERSE,
    to_yf_ticker, get_hot_stocks_kr, get_hot_stocks_us,
)
from analyzer import RelatedStockInferer

try:
    import universe as _universe
except Exception:
    _universe = None


def _norm_universe_entry(u):
    """universe.py 항목 → 빌더가 쓰는 entry 포맷."""
    if not u:
        return None
    if u.get("region") == "KR":
        return {"code": u["code"], "name": u["name"], "market": u.get("market"),
                "region": "KR", "ticker": to_yf_ticker(u["code"])}
    return {"code": u["code"], "name": u["name"], "market": u.get("market"),
            "region": "US", "ticker": u["code"]}


def _universe_entry_by_code(t):
    if not _universe:
        return None
    try:
        return _norm_universe_entry(_universe.lookup_by_code(t))
    except Exception:
        return None


def _universe_entry_by_name(n):
    if not _universe:
        return None
    try:
        return _norm_universe_entry(_universe.lookup_by_name(n))
    except Exception:
        return None


# 기본 카테고리 → 검색 키워드 매핑 (news_categories 모드용)
DEFAULT_CATEGORIES: Dict[str, List[str]] = {
    "AI/반도체":  ["AI 반도체", "엔비디아 한국", "HBM 메모리"],
    "2차전지":    ["2차전지", "전기차 배터리", "양극재"],
    "바이오":     ["신약 임상", "FDA 승인", "바이오 수주"],
    "방산":       ["방위산업 수주", "K2 전차", "FA-50"],
    "조선":       ["조선 수주", "LNG선"],
    "엔터/콘텐츠": ["K팝", "한류", "OTT"],
    "원전":       ["원전 수주", "SMR"],
    "로봇":       ["로봇 산업", "협동로봇"],
}

# 시장 전체에서 종목 영향력 큰 뉴스를 찾기 위한 키워드 (news_hot 모드용)
HOT_MARKET_QUERIES: List[str] = [
    "주가 급등",
    "신고가",
    "상한가",
    "어닝 서프라이즈",
    "실적 발표",
    "대규모 수주",
    "신제품 출시",
    "M&A 인수합병",
    "테마주",
]


# ─────────────────────────────────────────────
# 설정 저장소 (SQLite app_settings 테이블)
# ─────────────────────────────────────────────
def _ensure_settings_table(db_path: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now','+9 hours'))
            )
        """)
        conn.commit()


def get_setting(db_path: str, key: str, default=None) -> Optional[str]:
    try:
        _ensure_settings_table(db_path)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
            return row[0] if row else default
    except Exception:
        return default


def set_setting(db_path: str, key: str, value: str) -> None:
    _ensure_settings_table(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings(key, value, updated_at) VALUES (?, ?, datetime('now','+9 hours'))",
            (key, value),
        )
        conn.commit()


# ─────────────────────────────────────────────
# Universe 매칭 헬퍼
# ─────────────────────────────────────────────
def _name_to_entry(name: str) -> Optional[Dict]:
    n = (name or "").strip().lower()
    if not n:
        return None
    # KR 정확 매칭 우선
    for s in KR_STOCK_UNIVERSE:
        if s["name"].lower() == n:
            return {**s, "region": "KR", "ticker": to_yf_ticker(s["code"])}
    # KR 부분 매칭
    for s in KR_STOCK_UNIVERSE:
        sn = s["name"].lower()
        if n in sn or sn in n:
            return {**s, "region": "KR", "ticker": to_yf_ticker(s["code"])}
    # US 매칭
    for s in US_STOCK_UNIVERSE:
        sn = s["name"].lower()
        if sn == n or n in sn:
            return {**s, "region": "US", "ticker": s["code"]}
    # 동적 유니버스(전체 상장) — 이름 정확 매칭
    u = _universe_entry_by_name(n)
    if u:
        return u
    return None


def _ticker_to_entry(ticker: str) -> Optional[Dict]:
    t = (ticker or "").strip()
    if not t:
        return None
    for s in KR_STOCK_UNIVERSE:
        if t == s["code"] or t.upper() == s["code"] + ".KS":
            return {**s, "region": "KR", "ticker": to_yf_ticker(s["code"])}
    for s in US_STOCK_UNIVERSE:
        if t.upper() == s["code"]:
            return {**s, "region": "US", "ticker": s["code"]}
    # 동적 유니버스(전체 상장) — 코드/심볼 매칭
    u = _universe_entry_by_code(t)
    if u:
        return u
    return None


# ─────────────────────────────────────────────
# 뉴스 → 종목 추론
# ─────────────────────────────────────────────
def _get_recent_codes(db_path: Optional[str], cooldown: int = 2) -> set:
    """최근 cooldown 사이클에 등장한 종목 코드 집합 반환. db_path 없으면 빈 셋."""
    if not db_path:
        return set()
    try:
        _ensure_settings_table(db_path)
        raw = get_setting(db_path, "recent_watchlist_codes", "[]")
        history: List[List[str]] = json.loads(raw)  # [[codes_cycle_n], [codes_cycle_n-1], ...]
        recent: set = set()
        for cycle in history[:cooldown]:
            recent.update(cycle)
        return recent
    except Exception:
        return set()


def _save_recent_codes(db_path: Optional[str], codes: List[str], max_history: int = 4) -> None:
    """현재 사이클 코드를 history 앞에 prepend하고 max_history 사이클만 유지."""
    if not db_path:
        return
    try:
        raw = get_setting(db_path, "recent_watchlist_codes", "[]")
        history: List[List[str]] = json.loads(raw)
        history.insert(0, codes)
        history = history[:max_history]
        set_setting(db_path, "recent_watchlist_codes", json.dumps(history))
    except Exception:
        pass


def build_news_inferred(
    queries: List[str],
    limit: int = 10,
    news_per_query: int = 5,
    db_path: Optional[str] = None,
    cooldown_cycles: int = 2,
) -> List[Dict]:
    if not queries:
        return []
    collector = StockDataCollector()
    inferer = RelatedStockInferer()

    all_items: List[Dict] = []
    for q in queries:
        news = collector.get_news_data(q, display=news_per_query, sort="date")
        if news and "items" in news:
            for it in news["items"]:
                it["_query"] = q
            all_items.extend(news["items"])

    if not all_items:
        return []

    candidates = inferer.infer_related_stocks(all_items, max_candidates=limit * 3)
    if not candidates or (candidates and candidates[0].get("error")):
        return []

    # 최근 사이클에 등장한 종목 제외 (쿨다운)
    recent_codes = _get_recent_codes(db_path, cooldown=cooldown_cycles)

    out: List[Dict] = []
    seen = set()
    for c in candidates:
        entry = None
        if c.get("ticker"):
            entry = _ticker_to_entry(c["ticker"])
        if not entry and c.get("name"):
            entry = _name_to_entry(c["name"])
        if not entry:
            continue
        code = entry["code"]
        if code in seen or code in recent_codes:
            continue
        seen.add(code)
        entry["news_inference"] = {
            "reason": c.get("reason", ""),
            "expected_impact": c.get("expected_impact", "중립"),
            "value_chain": c.get("value_chain", ""),
            "confidence": c.get("confidence", "중"),
        }
        out.append(entry)
        if len(out) >= limit:
            break

    return out


# ─────────────────────────────────────────────
# 거래량 폴백
# ─────────────────────────────────────────────
def build_volume_based(kr_limit: int = 4, us_limit: int = 2, include_us: bool = True) -> List[Dict]:
    items: List[Dict] = []
    try:
        kr = get_hot_stocks_kr(limit=kr_limit)
    except Exception:
        kr = []
    if not kr:
        kr = [
            {"code": w["code"], "name": w["name"], "ticker": to_yf_ticker(w["code"]), "region": "KR"}
            for w in KR_STOCK_UNIVERSE[:kr_limit]
        ]
    items.extend(kr[:kr_limit])
    if include_us:
        try:
            us = get_hot_stocks_us(limit=us_limit)
            items.extend(us[:us_limit])
        except Exception:
            items.extend([
                {"code": w["code"], "name": w["name"], "ticker": w["code"], "region": "US"}
                for w in US_STOCK_UNIVERSE[:us_limit]
            ])
    return items


# ─────────────────────────────────────────────
# 메인 빌더 — DB 설정 읽어서 모드 결정
# ─────────────────────────────────────────────
def build_watchlist(
    db_path: str,
    mode: Optional[str] = None,
    kr_limit: int = 4,
    us_limit: int = 2,
    include_us: bool = True,
) -> List[Dict]:
    """뉴스 기반 종목 선정만 사용. 추론 결과 없으면 빈 리스트."""
    if mode is None:
        mode = get_setting(db_path, "watchlist_mode", "news_hot") or "news_hot"

    total = kr_limit + us_limit

    def _finish(result: List[Dict]) -> List[Dict]:
        """결과를 자르고 최근 등장 기록을 DB에 저장."""
        final = result[:total]
        codes = [e.get("code", "") for e in final if e.get("code")]
        if codes:
            _save_recent_codes(db_path, codes)
        return final

    # ⭐ 핫 뉴스 기반 — 시장 전체에서 영향력 있는 뉴스 → 종목 추론
    if mode == "news_hot":
        return _finish(build_news_inferred(
            HOT_MARKET_QUERIES, limit=total, db_path=db_path))

    # 카테고리 기반
    if mode == "news_categories":
        cats_raw = get_setting(db_path, "active_categories",
                               json.dumps(list(DEFAULT_CATEGORIES.keys())))
        try:
            active_cats = json.loads(cats_raw)
        except Exception:
            active_cats = list(DEFAULT_CATEGORIES.keys())
        queries: List[str] = []
        for cat in active_cats:
            queries.extend(DEFAULT_CATEGORIES.get(cat, [cat]))
        return _finish(build_news_inferred(queries, limit=total, db_path=db_path))

    # 키워드 기반
    if mode == "news_keywords":
        kw_raw = get_setting(db_path, "user_keywords", "[]")
        try:
            keywords = json.loads(kw_raw)
        except Exception:
            keywords = []
        if not keywords:
            return []
        return _finish(build_news_inferred(keywords, limit=total, db_path=db_path))

    # 거래대금 기반 — 가장 단순, 항상 동작 (Pi 3B+ 권장)
    if mode == "volume":
        return build_volume_based(
            kr_limit=kr_limit,
            us_limit=us_limit,
            include_us=include_us,
        )

    # 하이브리드 — 뉴스 추론 + 거래대금 혼합
    if mode == "hybrid":
        half = max(total // 2, 1)
        news = build_news_inferred(HOT_MARKET_QUERIES, limit=half)
        vol = build_volume_based(
            kr_limit=kr_limit,
            us_limit=us_limit,
            include_us=include_us,
        )
        seen = {it.get("code") for it in news}
        merged = list(news) + [v for v in vol if v.get("code") not in seen]
        return merged[:total]

    # 알 수 없는 모드 → 안전한 거래대금 모드로 폴백 (news_hot 은 0개 위험)
    return build_volume_based(
        kr_limit=kr_limit,
        us_limit=us_limit,
        include_us=include_us,
    )
