"""
condition_evaluator.py — 자동매매 조건 평가 엔진
==================================================
LLM이 추출한 조건 문자열(한국어)을 실제 지표로 계산해 True/False 반환.

지원 조건 목록
──────────────────────────────────────────────────
[가격]
  현재가 < X  /  현재가 > X  /  현재가 <= X  /  현재가 >= X

[RSI]
  RSI < X  /  RSI > X  /  RSI <= X  /  RSI >= X
  RSI 과매도  →  RSI < 30
  RSI 과매수  →  RSI > 70

[이동평균]
  N일 이동평균 / N일선 대비 -X% 이하 / N일선 대비 +X% 이상
  현재가 < N일 이동평균  /  현재가 > N일 이동평균

[골든크로스 / 데드크로스]
  골든크로스 발생  (기본: 5일선이 20일선 상향돌파)
  데드크로스 발생  (기본: 5일선이 20일선 하향돌파)
  N일/M일 골든크로스  (일수 명시 가능)

[고점/저점 대비]
  고점 대비 -X% 이하
  52주 고점 대비 -X%
  N일 고점 대비 -X%
  저점 대비 +X% 이상

[수익률] (매수가 buy_price 필요)
  수익률 +X% 달성  /  +X% 도달
  수익률 -X% 이하  (손절)
  TP+X%  /  TP1 +X%  /  TP2 +X% 등 라벨 포함

[MACD]
  MACD > 0  /  MACD < 0
  MACD 골든크로스  /  MACD 데드크로스
  MACD 히스토그램 > 0

[볼린저밴드]
  볼린저밴드 하단 터치  /  하단 이탈
  볼린저밴드 상단 터치  /  상단 돌파
  볼린저밴드 중간선 이상  /  중간선 이하

[거래량]
  거래량 X배 이상  (N일 평균 대비)
  거래량 급증  →  3배 기준

[항상 / 즉시]
  항상  /  즉시  /  always  →  True

[미지원 조건]
  → False 반환 + 경고 로그
──────────────────────────────────────────────────

사용 예:
    from condition_evaluator import evaluate

    ok = evaluate("RSI < 35", ticker="TQQQ")
    ok = evaluate("고점 대비 -10% 이하", ticker="TQQQ", ref_ticker="QQQ")
    ok = evaluate("수익률 +15% 달성", ticker="TQQQ", buy_price=45.0)
    ok = evaluate("골든크로스 발생", ticker="005930")
"""

import re
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple

import numpy as np

log = logging.getLogger("condition-evaluator")

# ─────────────────────────────────────────────
# OHLCV 캐시 (메모리)
# ─────────────────────────────────────────────
_cache: Dict[str, dict] = {}
CACHE_TTL = 300  # 5분


def _is_kr(ticker: str) -> bool:
    """6자리 숫자면 한국 종목."""
    return bool(re.match(r'^\d{6}$', ticker.split('.')[0].split('-')[0]))


def _fetch_ohlcv(ticker: str, days: int = 300) -> Optional[Dict]:
    """
    OHLCV 데이터 딕셔너리 반환.
    {
      "close":  [float, ...],   # 최근순 정렬 (index 0 = 오늘)
      "high":   [float, ...],
      "low":    [float, ...],
      "volume": [float, ...],
    }
    한국 종목은 pykrx, 미국/기타는 yfinance 사용.
    """
    key = f"{ticker}_{days}"
    now = time.time()

    # 캐시 히트
    cached = _cache.get(key)
    if cached and now - cached["ts"] < CACHE_TTL:
        return cached["data"]

    data = None

    if _is_kr(ticker):
        data = _fetch_pykrx(ticker, days)
    else:
        data = _fetch_yfinance(ticker, days)

    if data:
        _cache[key] = {"data": data, "ts": now}
    return data


def _fetch_yfinance(ticker: str, days: int) -> Optional[Dict]:
    try:
        import yfinance as yf
        period = "2y" if days > 300 else "1y"
        df = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        closes  = df["Close"].dropna().values[::-1].tolist()  # 최신→과거
        highs   = df["High"].dropna().values[::-1].tolist()
        lows    = df["Low"].dropna().values[::-1].tolist()
        volumes = df["Volume"].dropna().values[::-1].tolist()
        # flatten in case yfinance returns 2D
        def _flat(lst):
            try:
                return [float(x) if not hasattr(x, '__iter__') else float(list(x)[0]) for x in lst]
            except Exception:
                return [float(v) for v in lst]
        return {
            "close": _flat(closes),
            "high":  _flat(highs),
            "low":   _flat(lows),
            "volume": _flat(volumes),
        }
    except Exception as e:
        log.warning(f"yfinance 조회 실패 ({ticker}): {e}")
        return None


def _fetch_pykrx(ticker: str, days: int) -> Optional[Dict]:
    try:
        from pykrx import stock
        end   = datetime.today().strftime("%Y%m%d")
        start = (datetime.today() - timedelta(days=days + 100)).strftime("%Y%m%d")
        df = stock.get_market_ohlcv_by_date(start, end, ticker)
        if df is None or df.empty:
            return None
        closes  = df["종가"].values[::-1].tolist()
        highs   = df["고가"].values[::-1].tolist()
        lows    = df["저가"].values[::-1].tolist()
        volumes = df["거래량"].values[::-1].tolist()
        return {
            "close": [float(v) for v in closes],
            "high":  [float(v) for v in highs],
            "low":   [float(v) for v in lows],
            "volume": [float(v) for v in volumes],
        }
    except Exception as e:
        log.warning(f"pykrx 조회 실패 ({ticker}): {e}")
        return None


# ─────────────────────────────────────────────
# 지표 계산 유틸
# ─────────────────────────────────────────────

def _ema(values: list, period: int) -> list:
    """EMA 계산. values는 시간 오름차순(과거→최신)."""
    arr = np.array(values, dtype=float)
    k = 2.0 / (period + 1)
    ema = [arr[0]]
    for v in arr[1:]:
        ema.append(v * k + ema[-1] * (1 - k))
    return ema


def _sma(values: list, period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return float(np.mean(values[-period:]))


def calc_rsi(closes: list, period: int = 14) -> Optional[float]:
    """RSI 계산. closes는 과거→최신 순."""
    if len(closes) < period + 1:
        return None
    arr = np.array(closes[-period - 50:], dtype=float)
    deltas = np.diff(arr)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1 + rs), 2)


def calc_macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9
              ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """MACD 계산. returns (macd_line, signal_line, histogram). 과거→최신 순."""
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast   = _ema(closes, fast)
    ema_slow   = _ema(closes, slow)
    macd_line  = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_arr = _ema(macd_line, signal)
    hist = macd_line[-1] - signal_arr[-1]
    return round(macd_line[-1], 4), round(signal_arr[-1], 4), round(hist, 4)


def calc_bollinger(closes: list, period: int = 20, std_dev: float = 2.0
                   ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """볼린저밴드 (upper, mid, lower). 과거→최신 순."""
    if len(closes) < period:
        return None, None, None
    window = np.array(closes[-period:], dtype=float)
    mid   = float(np.mean(window))
    std   = float(np.std(window, ddof=1))
    return round(mid + std_dev * std, 4), round(mid, 4), round(mid - std_dev * std, 4)


def get_ma(closes: list, period: int) -> Optional[float]:
    """단순 이동평균(SMA)."""
    return _sma(closes, period)


def get_peak(highs: list, days: int) -> Optional[float]:
    """N일 고점."""
    if not highs:
        return None
    window = highs[:days]  # 최신→과거 순이므로 앞쪽이 최근
    return float(max(window))


def get_trough(lows: list, days: int) -> Optional[float]:
    """N일 저점."""
    if not lows:
        return None
    window = lows[:days]
    return float(min(window))


def is_golden_cross(closes: list, short: int, long_: int) -> bool:
    """골든크로스: 어제는 short_ma <= long_ma, 오늘은 short_ma > long_ma."""
    if len(closes) < long_ + 2:
        return False
    # closes는 최신→과거 순 → 역순으로 계산
    rev = list(reversed(closes[:long_ + 5]))
    short_today = _sma(rev, short)
    long_today  = _sma(rev, long_)
    short_prev  = _sma(rev[:-1], short)
    long_prev   = _sma(rev[:-1], long_)
    if None in (short_today, long_today, short_prev, long_prev):
        return False
    return (short_prev <= long_prev) and (short_today > long_today)


def is_dead_cross(closes: list, short: int, long_: int) -> bool:
    """데드크로스: 어제는 short_ma >= long_ma, 오늘은 short_ma < long_ma."""
    if len(closes) < long_ + 2:
        return False
    rev = list(reversed(closes[:long_ + 5]))
    short_today = _sma(rev, short)
    long_today  = _sma(rev, long_)
    short_prev  = _sma(rev[:-1], short)
    long_prev   = _sma(rev[:-1], long_)
    if None in (short_today, long_today, short_prev, long_prev):
        return False
    return (short_prev >= long_prev) and (short_today < long_today)


def is_macd_golden_cross(closes: list) -> bool:
    """MACD 골든크로스: 히스토그램이 음→양 전환."""
    if len(closes) < 40:
        return False
    rev = list(reversed(closes))
    _, _, hist_today = calc_macd(rev)
    _, _, hist_prev  = calc_macd(rev[:-1])
    if hist_today is None or hist_prev is None:
        return False
    return (hist_prev < 0) and (hist_today >= 0)


def is_macd_dead_cross(closes: list) -> bool:
    """MACD 데드크로스: 히스토그램이 양→음 전환."""
    if len(closes) < 40:
        return False
    rev = list(reversed(closes))
    _, _, hist_today = calc_macd(rev)
    _, _, hist_prev  = calc_macd(rev[:-1])
    if hist_today is None or hist_prev is None:
        return False
    return (hist_prev >= 0) and (hist_today < 0)


# ─────────────────────────────────────────────
# 파싱 헬퍼
# ─────────────────────────────────────────────

def _extract_number(text: str) -> Optional[float]:
    """텍스트에서 첫 번째 숫자 추출 (음수 포함)."""
    m = re.search(r'-?\d+\.?\d*', text)
    return float(m.group()) if m else None


def _extract_two_numbers(text: str) -> Tuple[Optional[float], Optional[float]]:
    """텍스트에서 숫자 두 개 추출 (N일/M일 같은 패턴용)."""
    nums = re.findall(r'\d+\.?\d*', text)
    a = float(nums[0]) if len(nums) > 0 else None
    b = float(nums[1]) if len(nums) > 1 else None
    return a, b


def _cmp(value: float, op: str, threshold: float) -> bool:
    ops = {
        "<": value < threshold,
        "<=": value <= threshold,
        ">": value > threshold,
        ">=": value >= threshold,
        "==": abs(value - threshold) < 0.001,
    }
    return ops.get(op, False)


def _parse_op(text: str) -> str:
    """조건 문자열에서 비교 연산자 추출."""
    for op in ["<=", ">=", "<", ">"]:
        if op in text:
            return op
    if "이하" in text or "미만" in text:
        return "<="
    if "이상" in text or "초과" in text:
        return ">="
    return "<"


# ─────────────────────────────────────────────
# 메인 평가 함수
# ─────────────────────────────────────────────

def evaluate(
    condition_str: str,
    ticker: str,
    ref_ticker: Optional[str] = None,
    buy_price: Optional[float] = None,
    current_price: Optional[float] = None,
) -> bool:
    """
    조건 문자열을 평가해 True/False 반환.

    Parameters
    ----------
    condition_str : str
        LLM이 추출한 조건 (한국어 자연어)
    ticker : str
        실제 매수/매도 대상 종목 코드
    ref_ticker : str, optional
        조건 판단에 쓰는 참조 종목 (예: "QQQ 고점 대비" → ref_ticker="QQQ")
    buy_price : float, optional
        매수가 (수익률 조건에 필요)
    current_price : float, optional
        이미 조회된 현재가 (없으면 내부에서 조회)
    """
    cond = condition_str.strip()
    cond_lower = cond.lower()

    # ── 항상/즉시 ──────────────────────────────
    if cond_lower in ("항상", "always", "즉시", "무조건"):
        return True

    # 조건 평가 대상 종목 (ref_ticker 우선)
    eval_ticker = ref_ticker if ref_ticker else ticker

    # OHLCV 로드 (가격 조건 외에는 필요)
    data = _fetch_ohlcv(eval_ticker)

    if current_price is None and data:
        current_price = data["close"][0]  # 가장 최신 종가

    # ── 가격 조건 ──────────────────────────────
    if any(k in cond_lower for k in ["현재가", "price", "가격"]):
        return _eval_price(cond, current_price)

    # ── RSI ───────────────────────────────────
    if "rsi" in cond_lower:
        return _eval_rsi(cond, cond_lower, data)

    # ── 수익률 / TP ────────────────────────────
    if any(k in cond_lower for k in ["수익률", "tp", "익절", "손절", "도달"]):
        return _eval_profit(cond, cond_lower, current_price, buy_price)

    # ── 고점 대비 ──────────────────────────────
    if any(k in cond_lower for k in ["고점 대비", "고점대비", "52주 고점", "최고점"]):
        return _eval_peak_drop(cond, cond_lower, data, current_price)

    # ── 저점 대비 ──────────────────────────────
    if any(k in cond_lower for k in ["저점 대비", "저점대비"]):
        return _eval_trough_rise(cond, cond_lower, data, current_price)

    # ── 이동평균 비교 ──────────────────────────
    if any(k in cond_lower for k in ["이동평균", "일선", "일 이동"]):
        return _eval_ma(cond, cond_lower, data, current_price)

    # ── 골든크로스 ────────────────────────────
    if "골든크로스" in cond_lower:
        return _eval_golden_cross(cond, cond_lower, data)

    # ── 데드크로스 ────────────────────────────
    if "데드크로스" in cond_lower:
        return _eval_dead_cross(cond, cond_lower, data)

    # ── MACD ─────────────────────────────────
    if "macd" in cond_lower:
        return _eval_macd(cond, cond_lower, data)

    # ── 볼린저밴드 ────────────────────────────
    if any(k in cond_lower for k in ["볼린저", "bollinger", "bb"]):
        return _eval_bollinger(cond, cond_lower, data, current_price)

    # ── 거래량 ───────────────────────────────
    if "거래량" in cond_lower:
        return _eval_volume(cond, cond_lower, data)

    # ── 미지원 ───────────────────────────────
    log.warning(f"[조건 미지원] '{condition_str}' → False 처리")
    return False


# ─────────────────────────────────────────────
# 개별 조건 평가 함수
# ─────────────────────────────────────────────

def _eval_price(cond: str, current_price: Optional[float]) -> bool:
    """현재가 비교."""
    if current_price is None:
        log.warning("현재가 조회 실패 → False")
        return False
    op = _parse_op(cond)
    threshold = _extract_number(re.sub(r'현재가|price|가격', '', cond, flags=re.I))
    if threshold is None:
        return False
    return _cmp(current_price, op, threshold)


def _eval_rsi(cond: str, cond_lower: str, data: Optional[dict]) -> bool:
    """RSI 조건 평가."""
    if data is None:
        log.warning("OHLCV 없음 → RSI 평가 불가")
        return False

    # 기간 추출 (RSI14, RSI(14) 등)
    period_m = re.search(r'rsi\s*[\(\[]?(\d+)', cond_lower)
    period = int(period_m.group(1)) if period_m else 14

    closes = list(reversed(data["close"]))  # 과거→최신
    rsi = calc_rsi(closes, period)
    if rsi is None:
        return False

    log.info(f"RSI({period}) = {rsi}")

    # 과매도/과매수 키워드
    if "과매도" in cond_lower:
        return rsi < 30
    if "과매수" in cond_lower:
        return rsi > 70

    # 숫자 기반 비교
    op = _parse_op(cond)
    threshold = _extract_number(re.sub(r'rsi\s*\(?\d*\)?', '', cond_lower).strip())
    if threshold is None:
        # "RSI < 35" 패턴에서 숫자 추출
        m = re.search(r'(\d+\.?\d*)', cond)
        threshold = float(m.group()) if m else None
    if threshold is None:
        return False
    return _cmp(rsi, op, threshold)


def _eval_profit(cond: str, cond_lower: str, current_price: Optional[float],
                  buy_price: Optional[float]) -> bool:
    """수익률 / TP / 손절 조건 평가."""
    if current_price is None or buy_price is None or buy_price == 0:
        log.warning(f"수익률 평가 불가 (current={current_price}, buy={buy_price})")
        return False

    profit_pct = (current_price - buy_price) / buy_price * 100
    log.info(f"수익률 = {profit_pct:.2f}%")

    # 수치 추출
    # "수익률 +15% 달성", "TP1 +15% 도달", "+350% 도달", "-10% 이하"
    m = re.search(r'([+-]?\d+\.?\d*)\s*%', cond)
    if not m:
        return False
    threshold = float(m.group(1))

    # 손절 조건
    if threshold < 0 or any(k in cond_lower for k in ["손절", "하락", "이하"]):
        return profit_pct <= threshold

    # 익절 조건 (TP, 수익률 달성)
    return profit_pct >= threshold


def _eval_peak_drop(cond: str, cond_lower: str, data: Optional[dict],
                     current_price: Optional[float]) -> bool:
    """고점 대비 하락률 조건."""
    if data is None or current_price is None:
        log.warning("데이터 없음 → 고점 대비 평가 불가")
        return False

    # N일 고점 기간 파싱 (기본 252일 = 52주)
    if "52주" in cond_lower:
        peak_days = 252
    else:
        m = re.search(r'(\d+)\s*일\s*고점', cond_lower)
        peak_days = int(m.group(1)) if m else 252

    highs = data["high"]
    peak = get_peak(highs, peak_days)
    if peak is None or peak == 0:
        return False

    drop_pct = (current_price - peak) / peak * 100  # 음수
    log.info(f"고점({peak_days}일) = {peak:.2f}, 현재 = {current_price:.2f}, 하락률 = {drop_pct:.2f}%")

    # 조건에서 하락률 추출: "-10%" or "10%"
    m = re.search(r'([+-]?\d+\.?\d*)\s*%', cond)
    if not m:
        return False
    threshold = float(m.group(1))
    if threshold > 0:
        threshold = -threshold  # "10%" → "-10%"

    op = _parse_op(cond)
    return _cmp(drop_pct, op, threshold)


def _eval_trough_rise(cond: str, cond_lower: str, data: Optional[dict],
                       current_price: Optional[float]) -> bool:
    """저점 대비 상승률 조건."""
    if data is None or current_price is None:
        return False
    m = re.search(r'(\d+)\s*일\s*저점', cond_lower)
    trough_days = int(m.group(1)) if m else 252
    lows = data["low"]
    trough = get_trough(lows, trough_days)
    if trough is None or trough == 0:
        return False
    rise_pct = (current_price - trough) / trough * 100
    log.info(f"저점({trough_days}일) = {trough:.2f}, 상승률 = {rise_pct:.2f}%")
    threshold_m = re.search(r'(\d+\.?\d*)\s*%', cond)
    threshold = float(threshold_m.group(1)) if threshold_m else None
    if threshold is None:
        return False
    op = _parse_op(cond)
    return _cmp(rise_pct, op, threshold)


def _eval_ma(cond: str, cond_lower: str, data: Optional[dict],
              current_price: Optional[float]) -> bool:
    """이동평균 조건 평가."""
    if data is None or current_price is None:
        return False

    # MA 기간 추출
    m = re.search(r'(\d+)\s*일', cond_lower)
    if not m:
        return False
    period = int(m.group(1))
    closes = list(reversed(data["close"]))  # 과거→최신
    ma = get_ma(closes, period)
    if ma is None:
        return False
    log.info(f"MA({period}) = {ma:.2f}, 현재 = {current_price:.2f}")

    # "N일선 대비 -X% 이하" 형태
    pct_m = re.search(r'([+-]?\d+\.?\d*)\s*%', cond)
    if pct_m:
        pct = float(pct_m.group(1))
        # 현재가가 MA 대비 몇 % 인지
        diff_pct = (current_price - ma) / ma * 100
        op = _parse_op(cond)
        return _cmp(diff_pct, op, pct)

    # "현재가 < N일 이동평균" 형태
    if "현재가" in cond_lower or "price" in cond_lower:
        op = _parse_op(cond)
        return _cmp(current_price, op, ma)

    # 기본: 현재가 vs MA
    op = _parse_op(cond)
    return _cmp(current_price, op, ma)


def _eval_golden_cross(cond: str, cond_lower: str, data: Optional[dict]) -> bool:
    """골든크로스 평가. 기본 5일/20일."""
    if data is None:
        return False
    closes = data["close"]  # 최신→과거

    # MACD 골든크로스
    if "macd" in cond_lower:
        return is_macd_golden_cross(closes)

    # 일수 명시: "5일/20일 골든크로스"
    nums = re.findall(r'(\d+)\s*일', cond_lower)
    if len(nums) >= 2:
        short, long_ = int(nums[0]), int(nums[1])
    else:
        short, long_ = 5, 20  # 기본

    return is_golden_cross(closes, short, long_)


def _eval_dead_cross(cond: str, cond_lower: str, data: Optional[dict]) -> bool:
    """데드크로스 평가. 기본 5일/20일."""
    if data is None:
        return False
    closes = data["close"]  # 최신→과거

    if "macd" in cond_lower:
        return is_macd_dead_cross(closes)

    nums = re.findall(r'(\d+)\s*일', cond_lower)
    if len(nums) >= 2:
        short, long_ = int(nums[0]), int(nums[1])
    else:
        short, long_ = 5, 20

    return is_dead_cross(closes, short, long_)


def _eval_macd(cond: str, cond_lower: str, data: Optional[dict]) -> bool:
    """MACD 조건 평가."""
    if data is None:
        return False
    closes = list(reversed(data["close"]))  # 과거→최신
    macd_line, signal_line, hist = calc_macd(closes)
    if macd_line is None:
        return False

    log.info(f"MACD = {macd_line:.4f}, Signal = {signal_line:.4f}, Hist = {hist:.4f}")

    if "골든크로스" in cond_lower:
        return is_macd_golden_cross(data["close"])
    if "데드크로스" in cond_lower:
        return is_macd_dead_cross(data["close"])
    if "히스토그램" in cond_lower:
        op = _parse_op(cond)
        threshold = _extract_number(re.sub(r'macd|히스토그램', '', cond_lower))
        return _cmp(hist, op, threshold if threshold is not None else 0)

    op = _parse_op(cond)
    threshold_m = re.search(r'([+-]?\d+\.?\d*)', re.sub(r'macd', '', cond_lower))
    threshold = float(threshold_m.group(1)) if threshold_m else 0.0
    return _cmp(macd_line, op, threshold)


def _eval_bollinger(cond: str, cond_lower: str, data: Optional[dict],
                     current_price: Optional[float]) -> bool:
    """볼린저밴드 조건 평가."""
    if data is None or current_price is None:
        return False
    closes = list(reversed(data["close"]))  # 과거→최신

    # 기간/표준편차 파싱 (기본 20일, 2σ)
    period_m = re.search(r'(\d+)\s*일', cond_lower)
    period = int(period_m.group(1)) if period_m else 20
    std_m = re.search(r'(\d+\.?\d*)\s*σ', cond_lower)
    std_dev = float(std_m.group(1)) if std_m else 2.0

    upper, mid, lower = calc_bollinger(closes, period, std_dev)
    if upper is None:
        return False

    log.info(f"볼린저 upper={upper:.2f}, mid={mid:.2f}, lower={lower:.2f}, 현재={current_price:.2f}")

    if any(k in cond_lower for k in ["하단 이탈", "하단이탈"]):
        return current_price < lower
    if any(k in cond_lower for k in ["하단 터치", "하단터치", "하단"]):
        return current_price <= lower * 1.01  # 1% 여유
    if any(k in cond_lower for k in ["상단 돌파", "상단돌파"]):
        return current_price > upper
    if any(k in cond_lower for k in ["상단 터치", "상단터치", "상단"]):
        return current_price >= upper * 0.99
    if any(k in cond_lower for k in ["중간선 이상", "중간선이상"]):
        return current_price >= mid
    if any(k in cond_lower for k in ["중간선 이하", "중간선이하"]):
        return current_price <= mid

    return False


def _eval_volume(cond: str, cond_lower: str, data: Optional[dict]) -> bool:
    """거래량 조건 평가."""
    if data is None:
        return False
    volumes = data["volume"]
    if len(volumes) < 2:
        return False

    today_vol = volumes[0]

    # "거래량 X배 이상" 패턴
    m = re.search(r'(\d+\.?\d*)\s*배', cond_lower)
    if m:
        multiple = float(m.group(1))
        # 기간 파싱 (기본 20일)
        days_m = re.search(r'(\d+)\s*일\s*평균', cond_lower)
        avg_days = int(days_m.group(1)) if days_m else 20
        avg_vol = float(np.mean(volumes[1: avg_days + 1])) if len(volumes) > avg_days else float(np.mean(volumes[1:]))
        if avg_vol == 0:
            return False
        ratio = today_vol / avg_vol
        log.info(f"거래량 = {today_vol:.0f}, {avg_days}일평균 = {avg_vol:.0f}, 비율 = {ratio:.2f}x")
        return ratio >= multiple

    # "거래량 급증" → 3배 기준
    if "급증" in cond_lower:
        avg_vol = float(np.mean(volumes[1:21])) if len(volumes) > 20 else float(np.mean(volumes[1:]))
        if avg_vol == 0:
            return False
        return (today_vol / avg_vol) >= 3.0

    return False


# ─────────────────────────────────────────────
# AND 복합 조건 평가
# ─────────────────────────────────────────────

def evaluate_compound(
    sub_conditions: list,
    condition_logic: str = "AND",
    **kwargs,
) -> bool:
    """
    여러 조건을 AND / OR 로 묶어 평가.

    sub_conditions: ["조건1", "조건2", ...]
    condition_logic: "AND" | "OR"
    kwargs: evaluate() 와 동일한 파라미터
    """
    results = [evaluate(cond, **kwargs) for cond in sub_conditions]
    if condition_logic.upper() == "OR":
        return any(results)
    return all(results)  # 기본 AND


# ─────────────────────────────────────────────
# 편의 함수: 캐시 강제 초기화
# ─────────────────────────────────────────────

def clear_cache():
    _cache.clear()
    log.info("조건 평가 캐시 초기화")
