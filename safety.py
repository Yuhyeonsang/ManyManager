"""
서버 안전장치 모듈
==================
세 가지 보호 기능을 한 모듈에 모음:

1. RateLimiter   : Gemini 무료 한도 (분당 10회 / 일 250회) 자동 보호
2. DBCleaner     : SQLite report_cache 만료 행 자동 삭제
3. KeyedLock     : Cache stampede 방지 (같은 키 동시 분석 차단)

main.py 에서 monkey-patch 식으로 끼워넣기 때문에
기존 코드를 거의 안 건드리고 적용 가능.
"""
import os
import time
import threading
import sqlite3
import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Optional, Callable, Any

log = logging.getLogger("safety")


# ════════════════════════════════════════════
# 1) Gemini API rate limiter
# ════════════════════════════════════════════
class RateLimiter:
    """무료 Gemini API 한도 보호.

    - 분당 RPM 회를 초과하면 가장 오래된 호출이 60초 지날 때까지 sleep
    - 일 한도 초과면 즉시 거부 (False 리턴)
    - thread-safe (멀티 워커에서 동시 호출 OK)
    """

    def __init__(self, rpm: int = 10, daily: int = 250, name: str = "gemini"):
        self.rpm = max(1, rpm)
        self.daily = max(1, daily)
        self.name = name
        self._minute_window: deque = deque()  # 최근 60초 호출 타임스탬프
        self._daily_count = 0
        self._daily_reset_ts = self._next_midnight_ts()
        self._lock = threading.Lock()

    @staticmethod
    def _next_midnight_ts() -> float:
        now = datetime.now()
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return midnight.timestamp()

    def acquire(self, timeout: float = 15.0) -> bool:
        """호출 권한 획득. 한도 초과면 wait 후 재시도. timeout 초과시 False."""
        deadline = time.time() + timeout
        while True:
            wait_for = 0.0
            with self._lock:
                now = time.time()
                # 일 카운터 자정 리셋
                if now >= self._daily_reset_ts:
                    self._daily_count = 0
                    self._daily_reset_ts = self._next_midnight_ts()
                # 일 한도 초과
                if self._daily_count >= self.daily:
                    log.warning(
                        f"[{self.name}] 일 한도 {self.daily}회 도달 - 거부"
                    )
                    return False
                # 분당 윈도우 청소
                cutoff = now - 60
                while self._minute_window and self._minute_window[0] < cutoff:
                    self._minute_window.popleft()
                if len(self._minute_window) < self.rpm:
                    self._minute_window.append(now)
                    self._daily_count += 1
                    return True
                # 분당 한도 초과 - 가장 오래된 호출이 60초 지날 때까지 대기
                wait_for = 60 - (now - self._minute_window[0]) + 0.2

            remaining = deadline - time.time()
            if remaining <= 0:
                log.warning(
                    f"[{self.name}] RPM {self.rpm}회 한도 - timeout {timeout}s 초과"
                )
                return False
            sleep_for = min(wait_for, remaining)
            log.info(
                f"[{self.name}] RPM 한도 - {sleep_for:.1f}s 대기 (현재 {len(self._minute_window)}/{self.rpm})"
            )
            time.sleep(max(0.1, sleep_for))

    def stats(self) -> Dict:
        with self._lock:
            return {
                "rpm_limit": self.rpm,
                "rpm_current_window": len(self._minute_window),
                "daily_limit": self.daily,
                "daily_used": self._daily_count,
                "daily_resets_at": datetime.fromtimestamp(
                    self._daily_reset_ts
                ).isoformat(),
            }


# ════════════════════════════════════════════
# 2) DB 만료 캐시 자동 청소
# ════════════════════════════════════════════
class DBCleaner:
    """cache_set 호출 N번마다 한 번씩 만료 행 청소.

    부하 거의 없음 (delete + commit 1회). 평균적으로 매 set의 1/N 빈도로만 동작.
    """

    def __init__(self, every_n: int = 50):
        self.every_n = max(1, every_n)
        self._counter = 0
        self._lock = threading.Lock()
        self._last_cleanup_ts = 0.0

    def maybe_clean(self, db_path: str, ttl_sec: int) -> int:
        """필요 시 청소. 실제 삭제된 행 수 리턴 (0이면 청소 스킵 또는 만료 행 없음)."""
        with self._lock:
            self._counter += 1
            if self._counter < self.every_n:
                return 0
            self._counter = 0

        try:
            cutoff = datetime.now() - timedelta(seconds=ttl_sec)
            cutoff_iso = cutoff.isoformat()
            with sqlite3.connect(db_path) as conn:
                # ISO 8601 문자열은 사전 정렬이 시간 순과 일치하므로 SQL 비교 OK
                cur = conn.execute(
                    "DELETE FROM report_cache WHERE updated_at < ?",
                    (cutoff_iso,),
                )
                deleted = cur.rowcount
                conn.commit()
            self._last_cleanup_ts = time.time()
            if deleted:
                log.info(f"[DBCleaner] 만료 캐시 {deleted}건 삭제")
            return deleted
        except Exception as e:
            log.warning(f"[DBCleaner] 실패: {e}")
            return 0

    def force_clean(self, db_path: str, ttl_sec: int) -> int:
        """카운터 무시하고 즉시 청소 (관리자/진단용)."""
        old = self._counter
        self._counter = self.every_n
        try:
            return self.maybe_clean(db_path, ttl_sec)
        finally:
            self._counter = max(0, old - 1)

    def stats(self) -> Dict:
        return {
            "every_n": self.every_n,
            "counter": self._counter,
            "last_cleanup": (
                datetime.fromtimestamp(self._last_cleanup_ts).isoformat()
                if self._last_cleanup_ts
                else None
            ),
        }


# ════════════════════════════════════════════
# 3) Per-key 락 (cache stampede 방지)
# ════════════════════════════════════════════
class KeyedLock:
    """key별 락. 같은 key에 대한 무거운 계산을 한 번에 한 스레드만 수행."""

    def __init__(self, max_keys: int = 1000):
        self._locks: Dict[str, threading.Lock] = {}
        self._meta_lock = threading.Lock()
        self.max_keys = max_keys

    def get(self, key: str) -> threading.Lock:
        with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                # 락 사전이 너무 커지면 안 쓰는 것부터 정리
                if len(self._locks) >= self.max_keys:
                    # 무작위로 절반 제거 (간단한 LRU 대체)
                    drop_count = self.max_keys // 2
                    for k in list(self._locks.keys())[:drop_count]:
                        if not self._locks[k].locked():
                            del self._locks[k]
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def stats(self) -> Dict:
        with self._meta_lock:
            return {"active_keys": len(self._locks), "max_keys": self.max_keys}


def compute_with_cache(
    key: str,
    cache_get_fn: Callable[[], Optional[Any]],
    compute_fn: Callable[[], Any],
    keyed_lock: "KeyedLock",
) -> Any:
    """Cache stampede 방지 헬퍼.

    1. cache_get → hit이면 즉시 리턴
    2. miss면 키별 락 획득
    3. 락 획득 후 cache_get 재시도 (다른 스레드가 그 사이 채웠을 수 있음)
    4. 여전히 miss면 compute_fn 호출 → 결과 리턴
    """
    cached = cache_get_fn()
    if cached is not None:
        return cached
    lock = keyed_lock.get(key)
    with lock:
        cached = cache_get_fn()
        if cached is not None:
            log.info(f"[stampede] '{key}' - 다른 워커가 채워둠 (대기 후 hit)")
            return cached
        return compute_fn()


# ════════════════════════════════════════════
# 싱글톤 (main.py 에서 import)
# ════════════════════════════════════════════
GEMINI_RPM = int(os.getenv("GEMINI_RPM", "10"))
GEMINI_DAILY = int(os.getenv("GEMINI_DAILY", "250"))
DB_CLEANUP_EVERY = int(os.getenv("DB_CLEANUP_EVERY", "30"))

gemini_limiter = RateLimiter(rpm=GEMINI_RPM, daily=GEMINI_DAILY, name="gemini")
db_cleaner = DBCleaner(every_n=DB_CLEANUP_EVERY)
keyed_lock = KeyedLock()


def install_gemini_rate_limit(*clients) -> None:
    """GeminiClient 인스턴스들의 call_json을 rate limiter로 wrap.

    사용:
        from safety import install_gemini_rate_limit
        install_gemini_rate_limit(gemini_filter, related_inferer)
    """
    for client in clients:
        if not hasattr(client, "call_json"):
            continue
        # 이미 wrap 됐는지 표시
        if getattr(client, "_safety_wrapped", False):
            continue
        original = client.call_json

        def make_wrapper(orig):
            def _wrapped(*args, **kwargs):
                if not gemini_limiter.acquire(timeout=15.0):
                    return {"error": "AI rate limit exceeded - 잠시 후 다시"}
                return orig(*args, **kwargs)
            return _wrapped

        client.call_json = make_wrapper(original)
        client._safety_wrapped = True
        log.info(f"[safety] Gemini rate limit 적용: {type(client).__name__}")
