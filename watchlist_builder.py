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
    return None


# ─────────────────────────────────────────────
# 뉴스 → 종목 추론
# ─────────────────────────────────────────────
def build_news_inferred(
    queries: List[str],
    limit: int = 10,
    news_per_query: int = 5,
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

    candidates = inferer.infer_related_stocks(all_items, max_candidates=limit * 2)
    if not candidates or (candidates and candidates[0].get("error")):
        return []

    out: List[Dict] = []
    seen = set()
    for c in candidates:
        entry = None
        if c.get("ticker"):
            entry = _ticker_to_entry(c["ticker"])
        if not entry and c.get("name"):
            entry = _name_to_entry(c["name"])
        if entry and entry["code"] not in seen:
            seen.add(entry["code"])
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

    # ⭐ 핫 뉴스 기반 — 시장 전체에서 영향력 있는 뉴스 → 종목 추론
    if mode == "news_hot":
        return build_news_inferred(HOT_MARKET_QUERIES, limit=total)[:total]

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
        return build_news_inferred(queries, limit=total)[:total]

    # 키워드 기반
    if mode == "news_keywords":
        kw_raw = get_setting(db_path, "user_keywords", "[]")
        try:
            keywords = json.loads(kw_raw)
        except Exception:
            keywords = []
        if not keywords:
            return []
        return build_news_inferred(keywords, limit=total)[:total]

    # 알 수 없는 모드 → news_hot 으로 폴백
    return build_news_inferred(HOT_MARKET_QUERIES, limit=total)[:total]
