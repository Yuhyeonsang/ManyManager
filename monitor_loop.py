"""
4단계: 실시간 모니터링 + SQLite 저장 + 토큰/비용 관리 + 안전장치
실행: python monitor_loop.py
"""

import os
import sys
import time
import json
import sqlite3
import hashlib
import signal
import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple

# 기존 1~3단계 모듈
from data_collector import collect_news        # 가정: List[Dict] 반환
from analyzer import (
    GeminiClient,
    GeminiNewsFilter,
    # 필요시 추가 import
)

# ─────────────────────────────────────────────
# 설정 (환경변수 우선)
# ─────────────────────────────────────────────
DB_PATH         = os.getenv("FUND_DB", "fund_manager.db")
INTERVAL_MIN    = int(os.getenv("LOOP_INTERVAL_MIN", "15"))
DAILY_BUDGET_USD = float(os.getenv("DAILY_BUDGET_USD", "1.0"))
TZ_OFFSET       = 9 * 3600   # KST
USD_KRW         = float(os.getenv("USD_KRW", "1380"))

# 모델별 단가 ($/1M tokens, 2026 기준 추정 — 필요시 갱신)
PRICING = {
    "gemini-1.5-flash":     {"in": 0.075,  "out": 0.30},
    "gemini-1.5-pro":       {"in": 1.25,   "out": 5.00},
    "claude-haiku-4-5":     {"in": 1.00,   "out": 5.00},
    "claude-sonnet-4-6":    {"in": 3.00,   "out": 15.00},
    "claude-opus-4-6":      {"in": 15.00,  "out": 75.00},
}

# Gemini 무료 티어 일일 호출 한도 (Flash 기준)
GEMINI_FREE_DAILY_CALLS = 1500

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("loop")

# ─────────────────────────────────────────────
# DB 스키마
# ─────────────────────────────────────────────
SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    hash        TEXT PRIMARY KEY,         -- title+url 기반 dedupe key
    title       TEXT NOT NULL,
    url         TEXT,
    source      TEXT,
    published   TEXT,
    sentiment   REAL,                     -- -1 ~ +1
    summary     TEXT,                     -- 2~3줄 요약만 저장
    created_at  TEXT DEFAULT (datetime('now','+9 hours'))
);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT DEFAULT (datetime('now','+9 hours')),
    market_view TEXT,                     -- bullish/bearish/neutral
    risk_score  REAL,                     -- 0~10
    top_picks   TEXT,                     -- JSON array
    digest      TEXT                      -- 핵심 코멘트(<500자)
);

CREATE TABLE IF NOT EXISTS api_usage (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT DEFAULT (datetime('now','+9 hours')),
    day         TEXT,                     -- YYYY-MM-DD (KST)
    model       TEXT,
    in_tokens   INTEGER,
    out_tokens  INTEGER,
    usd         REAL,
    krw         REAL
);

CREATE INDEX IF NOT EXISTS idx_usage_day ON api_usage(day);
CREATE INDEX IF NOT EXISTS idx_news_created ON news(created_at);
"""


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ─────────────────────────────────────────────
# 토큰/비용 계산기
# ─────────────────────────────────────────────
class UsageMonitor:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @staticmethod
    def _today() -> str:
        return date.today().isoformat()

    @staticmethod
    def estimate_tokens(text: str) -> int:
        # 보수적 추정: 한국어 ~2자/token, 영어 ~4자/token → 평균 2.8
        return max(1, int(len(text) / 2.8))

    def log_call(self, model: str, in_text: str, out_text: str,
                 in_tokens: Optional[int] = None,
                 out_tokens: Optional[int] = None) -> Dict:
        in_tok  = in_tokens  if in_tokens  is not None else self.estimate_tokens(in_text)
        out_tok = out_tokens if out_tokens is not None else self.estimate_tokens(out_text)

        p = PRICING.get(model, {"in": 0, "out": 0})
        usd = (in_tok * p["in"] + out_tok * p["out"]) / 1_000_000
        krw = usd * USD_KRW

        self.conn.execute(
            "INSERT INTO api_usage(day,model,in_tokens,out_tokens,usd,krw) "
            "VALUES (?,?,?,?,?,?)",
            (self._today(), model, in_tok, out_tok, usd, krw),
        )
        self.conn.commit()
        return {"in": in_tok, "out": out_tok, "usd": usd, "krw": krw}

    def today_total(self) -> Dict:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(usd),0) usd, COALESCE(SUM(krw),0) krw, "
            "COALESCE(SUM(in_tokens+out_tokens),0) tok, COUNT(*) calls "
            "FROM api_usage WHERE day=?",
            (self._today(),),
        ).fetchone()
        gem_calls = self.conn.execute(
            "SELECT COUNT(*) c FROM api_usage WHERE day=? AND model LIKE 'gemini%'",
            (self._today(),),
        ).fetchone()["c"]
        return {
            "usd": row["usd"],
            "krw": row["krw"],
            "tokens": row["tok"],
            "calls": row["calls"],
            "gemini_remaining": max(0, GEMINI_FREE_DAILY_CALLS - gem_calls),
        }


# ─────────────────────────────────────────────
# 계측이 적용된 Gemini 래퍼
# ─────────────────────────────────────────────
class TrackedGemini(GeminiClient):
    def __init__(self, monitor: UsageMonitor, model: str = "gemini-1.5-flash"):
        super().__init__()
        self.monitor = monitor
        self.model = model

    def call_json(self, prompt: str, temperature: float = 0.2) -> Dict:
        result = super().call_json(prompt, temperature)
        out_text = json.dumps(result, ensure_ascii=False)
        self.monitor.log_call(self.model, prompt, out_text)
        return result


# ─────────────────────────────────────────────
# DB 저장 헬퍼
# ─────────────────────────────────────────────
def news_hash(title: str, url: str = "") -> str:
    key = (title or "").strip() + "|" + (url or "").strip()
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def save_news(conn: sqlite3.Connection, items: List[Dict]) -> int:
    """중복 제외 신규만 저장. 반환: 신규 저장 건수."""
    inserted = 0
    for it in items:
        h = news_hash(it.get("title", ""), it.get("url", ""))
        try:
            conn.execute(
                "INSERT INTO news(hash,title,url,source,published,sentiment,summary) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    h,
                    (it.get("title") or "")[:300],
                    (it.get("url") or "")[:500],
                    (it.get("source") or "")[:100],
                    it.get("published"),
                    it.get("sentiment"),
                    (it.get("summary") or "")[:600],   # 요약만 → 용량 최소화
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass  # 중복
    conn.commit()
    return inserted


def filter_unseen(conn: sqlite3.Connection, items: List[Dict]) -> List[Dict]:
    """이미 DB에 있는 뉴스는 제거 → API 호출 절감."""
    fresh = []
    for it in items:
        h = news_hash(it.get("title", ""), it.get("url", ""))
        if not conn.execute("SELECT 1 FROM news WHERE hash=?", (h,)).fetchone():
            it["_hash"] = h
            fresh.append(it)
    return fresh


def save_report(conn: sqlite3.Connection, report: Dict) -> int:
    cur = conn.execute(
        "INSERT INTO reports(market_view,risk_score,top_picks,digest) VALUES (?,?,?,?)",
        (
            (report.get("market_view") or "neutral")[:20],
            float(report.get("risk_score") or 0),
            json.dumps(report.get("top_picks") or [], ensure_ascii=False)[:1000],
            (report.get("digest") or "")[:500],
        ),
    )
    conn.commit()
    return cur.lastrowid


# ─────────────────────────────────────────────
# 분석 파이프라인 (3단계 결과를 4단계에 연결)
# ─────────────────────────────────────────────
def run_analysis(conn: sqlite3.Connection, monitor: UsageMonitor) -> Optional[Dict]:
    log.info("뉴스 수집 시작")
    raw = collect_news()
    if not raw:
        log.warning("수집된 뉴스 없음")
        return None
    log.info(f"수집 {len(raw)}건")

    fresh = filter_unseen(conn, raw)
    log.info(f"신규 {len(fresh)}건 (중복 {len(raw)-len(fresh)}건 스킵)")
    if not fresh:
        return None

    gemini = TrackedGemini(monitor)
    flt = GeminiNewsFilter()
    flt.api_key = gemini.api_key
    # 토큰 추적을 위해 GeminiNewsFilter 내부 호출도 monitor 경유시키려면
    # GeminiNewsFilter(call_json) 을 monkey-patch
    flt.call_json = gemini.call_json  # type: ignore

    picked = flt.filter_news(fresh, top_k=3)
    if not picked:
        log.info("선별 결과 없음")
        save_news(conn, fresh)   # 그래도 dedupe용으로 저장
        return None

    # 종합 리포트 (Gemini로 간단 합성 — 토큰 절약)
    digest_prompt = (
        "다음 뉴스들을 종합해 한국어 JSON으로 답하라. "
        '필드: market_view(bullish|bearish|neutral), risk_score(0~10), '
        'top_picks(["종목/섹터", ...] 3개 이내), digest(<200자 핵심).\n\n'
        + json.dumps(picked, ensure_ascii=False)[:4000]
    )
    report = gemini.call_json(digest_prompt)
    if "error" in report:
        log.error(f"리포트 생성 실패: {report['error']}")
        save_news(conn, fresh)
        return None

    # 저장
    for p in picked:
        p["summary"] = p.get("reason") or p.get("summary") or ""
    save_news(conn, fresh)
    rid = save_report(conn, report)
    log.info(f"리포트 저장 #{rid}: view={report.get('market_view')} risk={report.get('risk_score')}")
    return report


# ─────────────────────────────────────────────
# 메인 루프 + 안전 장치
# ─────────────────────────────────────────────
_running = True

def _stop(signum, frame):
    global _running
    _running = False
    log.info("정지 신호 수신 — 다음 사이클 후 종료")

signal.signal(signal.SIGINT, _stop)
signal.signal(signal.SIGTERM, _stop)


def main_loop():
    conn = db_conn()
    monitor = UsageMonitor(conn)
    log.info(f"DB={DB_PATH}  주기={INTERVAL_MIN}분  일일한도=${DAILY_BUDGET_USD}")

    while _running:
        usage = monitor.today_total()
        log.info(
            f"[USAGE] 오늘 ${usage['usd']:.4f} / ₩{usage['krw']:.1f} "
            f"({usage['calls']}회, {usage['tokens']} tok, "
            f"Gemini 무료 잔여 {usage['gemini_remaining']}회)"
        )

        # 안전장치: 일일 예산 초과
        if usage["usd"] >= DAILY_BUDGET_USD:
            log.warning(
                f"일일 예산 ${DAILY_BUDGET_USD} 초과 → 루프 정지 "
                f"(현재 ${usage['usd']:.4f})"
            )
            break

        try:
            run_analysis(conn, monitor)
        except Exception as e:
            log.exception(f"사이클 오류: {e}")

        # 슬립 (1초씩 끊어서 정지 신호 빠르게 반응)
        for _ in range(INTERVAL_MIN * 60):
            if not _running:
                break
            time.sleep(1)

    conn.close()
    log.info("종료 완료")


if __name__ == "__main__":
    if "--status" in sys.argv:
        c = db_conn()
        m = UsageMonitor(c)
        print(json.dumps(m.today_total(), indent=2, ensure_ascii=False))
        c.close()
    else:
        main_loop()
