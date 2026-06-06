"""
backtester.py — 이미지 조건 기반 백테스트 엔진
================================================
yfinance로 과거 OHLC 데이터를 받아서
매수/매도 조건을 날짜별로 시뮬레이션하고
수익률·승률·MDD 등 지표를 반환한다.

지원 조건 형식 (auto_trader.py와 동일):
  "현재가 < 70000"  / "price < 70000"
  "현재가 > 80000"  / "price > 80000"
  "항상" / "always"  → 매일 실행
  "수익률 > 5%"      → 매수 후 5% 이상이면 매도
  "손실률 > 3%"      → 매수 후 3% 이하 손실이면 손절
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("backtester")

# ─────────────────────────────────────────────
# 기술적 지표 계산
# ─────────────────────────────────────────────
def _calc_indicators(df, idx: int) -> dict:
    """
    df의 idx번째 행까지 데이터로 기술적 지표 계산.
    반환: rsi, ma20, ma50, ma200, bb_upper, bb_lower, bb_mid,
          peak_52w, drawdown_pct, golden_cross, dead_cross
    """
    import numpy as np
    closes = df["Close"].iloc[:idx+1]
    n = len(closes)
    result = {}

    def _ma(period):
        if n >= period:
            return float(closes.iloc[-period:].mean())
        return float(closes.mean())

    result["ma20"]  = _ma(20)
    result["ma50"]  = _ma(50)
    result["ma200"] = _ma(200)

    # 볼린저밴드 (20일)
    if n >= 20:
        mid = closes.iloc[-20:].mean()
        std = closes.iloc[-20:].std()
        result["bb_mid"]   = float(mid)
        result["bb_upper"] = float(mid + 2 * std)
        result["bb_lower"] = float(mid - 2 * std)
    else:
        result["bb_mid"] = result["bb_upper"] = result["bb_lower"] = float(closes.iloc[-1])

    # 52주 고점 및 고점 대비 하락률
    peak_window = min(n, 252)
    peak = float(closes.iloc[-peak_window:].max())
    current = float(closes.iloc[-1])
    result["peak_52w"]      = peak
    result["drawdown_pct"]  = (current - peak) / peak * 100 if peak else 0.0

    # RSI (14일)
    if n >= 15:
        deltas = closes.diff().dropna().iloc[-14:]
        gains  = deltas.clip(lower=0).mean()
        losses = (-deltas.clip(upper=0)).mean()
        rs     = gains / losses if losses != 0 else 100
        result["rsi"] = float(100 - 100 / (1 + rs))
    else:
        result["rsi"] = 50.0

    # 골든/데드크로스: ma50 vs ma200 방향 전환
    result["golden_cross"] = False
    result["dead_cross"]   = False
    if n >= 201:
        prev_ma50  = float(closes.iloc[-51:-1].mean())
        prev_ma200 = float(closes.iloc[-201:-1].mean())
        cur_ma50   = result["ma50"]
        cur_ma200  = result["ma200"]
        if prev_ma50 <= prev_ma200 and cur_ma50 > cur_ma200:
            result["golden_cross"] = True
        if prev_ma50 >= prev_ma200 and cur_ma50 < cur_ma200:
            result["dead_cross"] = True

    return result


# ─────────────────────────────────────────────
# 조건 평가 (확장판)
# ─────────────────────────────────────────────
def _eval_condition(condition_str: str, current_price: float,
                    indicators: dict,
                    buy_price: Optional[float] = None,
                    ref_indicators: Optional[dict] = None) -> bool:
    """
    자연어 조건 문자열을 해석해 True/False 반환.

    지원 조건:
      항상 / always / 즉시
      수익률 > X% / TP+X% / +X%
      손절 -X% / 손실률 > X%
      고점 대비 -X% / 고점 대비 X% 하락
      X일선 대비 -Y% 이하 / X일 이동평균 이하
      RSI < X / RSI > X
      골든크로스 / GC / 데드크로스 / DC
      볼린저 상단 이탈 / 볼린저 하단 이탈
      현재가 < X / price > X
    """
    import re
    cond = condition_str.strip()
    cond_l = cond.lower()

    # 항상 실행
    if cond_l in ("항상", "always", "즉시", "매일"):
        return True

    # ── 수익률 / TP 익절 ──
    profit_patterns = [
        r"tp\s*[+\(]?\s*(\d+(?:\.\d+)?)\s*%",          # TP+10%, TP(+10%)
        r"[+\+]\s*(\d+(?:\.\d+)?)\s*%\s*[:\)매도]",     # +10%: 매도
        r"수익률\s*[>≥]\s*(\d+(?:\.\d+)?)\s*%",
        r"수익\s*(\d+(?:\.\d+)?)\s*%\s*이상",
    ]
    if buy_price:
        ret_pct = (current_price - buy_price) / buy_price * 100
        for pat in profit_patterns:
            m = re.search(pat, cond_l)
            if m:
                threshold = float(m.group(1))
                return ret_pct >= threshold

    # ── 손절 / 손실 ──
    loss_patterns = [
        r"손절\s*-?\s*(\d+(?:\.\d+)?)\s*%",
        r"손실률?\s*[>≥]\s*(\d+(?:\.\d+)?)\s*%",
        r"-\s*(\d+(?:\.\d+)?)\s*%\s*(손절|스탑|stop)",
    ]
    if buy_price:
        ret_pct = (current_price - buy_price) / buy_price * 100
        for pat in loss_patterns:
            m = re.search(pat, cond_l)
            if m:
                threshold = float(m.group(1))
                return ret_pct <= -threshold

    # ── 고점 대비 하락 ──
    # ref_indicators: 다른 종목(예: QQQ) 기준일 때
    ind = ref_indicators if ref_indicators else indicators
    dd_patterns = [
        r"고점\s*대비\s*-\s*(\d+(?:\.\d+)?)\s*%",
        r"고점\s*대비\s*(\d+(?:\.\d+)?)\s*%\s*하락",
        r"-\s*(\d+(?:\.\d+)?)\s*%\s*하락",
    ]
    for pat in dd_patterns:
        m = re.search(pat, cond_l)
        if m:
            threshold = -float(m.group(1))
            return ind.get("drawdown_pct", 0) <= threshold

    # ── 이동평균 관련 ──
    # "200일선 대비 -7% 이하"
    ma_pct_m = re.search(r"(\d+)일선?\s*대비\s*-?\s*(\d+(?:\.\d+)?)\s*%\s*(이하|아래|미만)", cond_l)
    if ma_pct_m:
        period  = int(ma_pct_m.group(1))
        pct     = float(ma_pct_m.group(2))
        ma_key  = f"ma{period}"
        ma_val  = ind.get(ma_key, indicators.get(ma_key))
        if ma_val:
            return current_price <= ma_val * (1 - pct / 100)

    # "200일선 이하 / 아래"
    ma_below_m = re.search(r"(\d+)일선?\s*(이하|아래|미만|하회)", cond_l)
    if ma_below_m:
        period = int(ma_below_m.group(1))
        ma_val = indicators.get(f"ma{period}")
        if ma_val:
            return current_price < ma_val

    # "이전 고점 돌파 / 고점 돌파"
    if re.search(r"고점\s*돌파|신고가", cond_l):
        return current_price >= indicators.get("peak_52w", current_price)

    # ── RSI ──
    rsi_m = re.search(r"rsi\s*([<>≤≥]=?)\s*(\d+(?:\.\d+)?)", cond_l)
    if rsi_m:
        op, val = rsi_m.group(1), float(rsi_m.group(2))
        rsi = indicators.get("rsi", 50)
        if "<=" in op or "≤" in op: return rsi <= val
        if ">=" in op or "≥" in op: return rsi >= val
        if "<"  in op: return rsi < val
        if ">"  in op: return rsi > val

    # ── 골든크로스 / 데드크로스 ──
    if re.search(r"골든\s*크로스|golden\s*cross|\bgc\b", cond_l):
        return indicators.get("golden_cross", False)
    if re.search(r"데드\s*크로스|dead\s*cross|\bdc\b", cond_l):
        return indicators.get("dead_cross", False)

    # ── 볼린저밴드 ──
    if re.search(r"볼린저.*(상단|상향)\s*(이탈|돌파|초과)", cond_l):
        return current_price >= indicators.get("bb_upper", current_price + 1)
    if re.search(r"볼린저.*(하단|하향)\s*(이탈|터치|이하|붕괴)", cond_l):
        return current_price <= indicators.get("bb_lower", current_price - 1)

    # ── 현재가 단순 비교 ──
    for kw in ["현재가", "price", "가격", "종가"]:
        if kw in cond_l:
            try:
                if "<=" in cond: return current_price <= float(re.search(r"<=\s*([\d,]+)", cond).group(1).replace(",",""))
                if ">=" in cond: return current_price >= float(re.search(r">=\s*([\d,]+)", cond).group(1).replace(",",""))
                if "<"  in cond: return current_price <  float(re.search(r"<\s*([\d,]+)",  cond).group(1).replace(",",""))
                if ">"  in cond: return current_price >  float(re.search(r">\s*([\d,]+)",  cond).group(1).replace(",",""))
            except Exception:
                pass

    log.debug("미지원 조건 (건너뜀): %s", condition_str)
    return False


# ─────────────────────────────────────────────
# 백테스트 실행
# ─────────────────────────────────────────────
def run_backtest(conditions: dict, period_days: int = 90,
                 initial_cash: float = 10_000_000) -> dict:
    """
    conditions: auto_trader / Gemini Vision이 추출한 조건 dict
    period_days: 과거 몇 일치 데이터 (기본 90일)
    initial_cash: 초기 자본 (원화 기준, 기본 1000만원)

    반환:
    {
      "summary": { total_return_pct, win_rate, trade_count, mdd_pct, final_value },
      "per_stock": [ { ticker, name, trades, return_pct, ... } ],
      "trade_log": [ { date, action, ticker, price, qty, pnl, ... } ]
    }
    """
    from yahoo_direct import download_tiingo
    from data_collector import _yf_history_safe

    end_date   = datetime.now()
    start_date = end_date - timedelta(days=period_days + 5)  # 여유 5일

    buy_conds  = conditions.get("buy_conditions", [])
    sell_conds = conditions.get("sell_conditions", [])

    # 모든 종목 수집
    tickers = {}
    for c in buy_conds + sell_conds:
        t = c.get("ticker") or c.get("name")  # name 폴백: AI가 ticker 대신 name에 종목코드 넣는 경우 대응
        if t and isinstance(t, str):
            t = t.strip().upper()
        if t:
            if t.isdigit() and len(t) == 6:
                yf_ticker = t + ".KS"
            elif "." not in t and len(t) <= 6 and t.isalpha():
                yf_ticker = t
            else:
                yf_ticker = t
            tickers[t] = {"yf": yf_ticker, "name": c.get("name", t)}

    if not tickers:
        return {
            "summary": {"error": "종목 정보가 없습니다. 이미지에 종목코드가 포함되어 있는지 확인하세요."},
            "per_stock": [],
            "trade_log": [],
        }

    all_trade_log = []
    per_stock_results = []
    portfolio_values = []

    cash = initial_cash
    holdings = {}   # ticker → {"qty": n, "avg_price": p}

    # 날짜별 가격 데이터 로드 — Tiingo(미국) / _yf_history_safe(국내)
    price_data = {}
    for orig_t, info in tickers.items():
        try:
            yf_t = info["yf"]
            is_kr = yf_t.endswith(".KS") or yf_t.endswith(".KQ")
            period_str = f"{period_days}d"
            if is_kr:
                df = _yf_history_safe(yf_t, period=period_str)
            else:
                df = download_tiingo(yf_t, period=period_str)
            # 컬럼 정규화
            if not df.empty and "Close" not in df.columns:
                df.columns = [c.capitalize() for c in df.columns]
            if df.empty:
                log.warning(f"데이터 없음: {yf_t}")
                continue
            price_data[orig_t] = df
        except Exception as e:
            log.warning(f"데이터 로드 실패 ({info['yf']}): {e}")

    if not price_data:
        return {
            "summary": {"error": "주가 데이터를 가져오지 못했습니다. 종목코드를 확인하세요."},
            "per_stock": [],
            "trade_log": [],
        }

    # 공통 날짜 추출 (어느 종목이든 데이터 있는 날)
    all_dates = set()
    for df in price_data.values():
        all_dates.update(df.index.tolist())
    all_dates = sorted(all_dates)

    # 조건에서 참조되는 크로스 티커 수집 (예: "QQQ 고점 대비" → QQQ 데이터 필요)
    import re as _re
    ref_tickers_needed = set()
    for c in buy_conds + sell_conds:
        cond_text = c.get("condition", "")
        # "QQQ 고점 대비", "SPY 기준" 등 패턴 감지
        m = _re.search(r'\b([A-Z]{2,5})\s*(고점|기준|대비)', cond_text.upper())
        if m:
            ref_t = m.group(1)
            if ref_t not in price_data:
                ref_tickers_needed.add(ref_t)

    # 참조 티커 데이터 추가 로드
    for ref_t in ref_tickers_needed:
        try:
            ref_df = download_tiingo(ref_t, period=f"{period_days + 10}d")
            if ref_df.empty and _is_kr_ticker(ref_t + ".KS"):
                ref_df = _yf_history_safe(ref_t + ".KS", period=f"{period_days + 10}d")
            if not ref_df.empty:
                price_data[ref_t] = ref_df
                log.info(f"참조 티커 로드: {ref_t}")
        except Exception as e:
            log.warning(f"참조 티커 로드 실패 ({ref_t}): {e}")

    # ── 날짜별 시뮬레이션 ──
    for date in all_dates:
        day_str = date.strftime("%Y-%m-%d") if hasattr(date, 'strftime') else str(date)[:10]

        # 매수 체크
        for cond in buy_conds:
            ticker = cond.get("ticker")
            if not ticker or ticker not in price_data:
                continue
            df = price_data[ticker]
            if date not in df.index:
                continue

            try:
                close = float(df.loc[date, "Close"])
            except Exception:
                continue
            if not close or close != close:
                continue

            idx = df.index.get_loc(date)
            indicators = _calc_indicators(df, idx)

            # 참조 티커 지표 (예: QQQ 기준)
            cond_text = cond.get("condition", "")
            ref_ind = None
            m = _re.search(r'\b([A-Z]{2,5})\s*(고점|기준|대비)', cond_text.upper())
            if m and m.group(1) in price_data:
                ref_t = m.group(1)
                ref_df = price_data[ref_t]
                ref_dates = ref_df.index[ref_df.index <= date]
                if len(ref_dates):
                    ref_idx = ref_df.index.get_loc(ref_dates[-1])
                    ref_ind = _calc_indicators(ref_df, ref_idx)

            if _eval_condition(cond_text, close, indicators, ref_indicators=ref_ind):
                qty_raw = cond.get("qty", 1)
                qty = max(1, int(cash // close)) if str(qty_raw) == "all" else int(qty_raw)
                cost = close * qty
                if cash >= cost:
                    cash -= cost
                    if ticker not in holdings:
                        holdings[ticker] = {"qty": 0, "avg_price": 0.0}
                    prev_qty = holdings[ticker]["qty"]
                    prev_avg = holdings[ticker]["avg_price"]
                    new_qty  = prev_qty + qty
                    holdings[ticker]["avg_price"] = (prev_avg * prev_qty + close * qty) / new_qty
                    holdings[ticker]["qty"] = new_qty
                    all_trade_log.append({
                        "date": day_str, "action": "매수",
                        "ticker": ticker, "name": tickers[ticker]["name"],
                        "price": round(close, 2), "qty": qty,
                        "pnl": None, "condition": cond_text,
                    })

        # 매도 체크
        for cond in sell_conds:
            ticker = cond.get("ticker")
            if not ticker or ticker not in price_data:
                continue
            if ticker not in holdings or holdings[ticker]["qty"] <= 0:
                continue
            df = price_data[ticker]
            if date not in df.index:
                continue

            try:
                close = float(df.loc[date, "Close"])
            except Exception:
                continue
            if not close or close != close:
                continue

            idx = df.index.get_loc(date)
            indicators = _calc_indicators(df, idx)
            buy_price  = holdings[ticker]["avg_price"]
            cond_text  = cond.get("condition", "")

            if _eval_condition(cond_text, close, indicators, buy_price=buy_price):
                qty_raw = cond.get("qty", 1)
                qty = holdings[ticker]["qty"] if str(qty_raw) == "all" else min(int(qty_raw), holdings[ticker]["qty"])
                proceeds = close * qty
                pnl      = (close - buy_price) * qty
                cash    += proceeds
                holdings[ticker]["qty"] -= qty
                if holdings[ticker]["qty"] <= 0:
                    del holdings[ticker]
                all_trade_log.append({
                    "date": day_str, "action": "매도",
                    "ticker": ticker, "name": tickers[ticker]["name"],
                    "price": round(close, 2), "qty": qty,
                    "pnl": round(pnl, 0),
                    "pnl_pct": round((close - buy_price) / buy_price * 100, 2),
                    "condition": cond_text,
                })

        # 포트폴리오 평가액 기록
        holding_value = 0
        for t, h in holdings.items():
            if t in price_data and date in price_data[t].index:
                try:
                    p = float(price_data[t].loc[date, "Close"])
                    holding_value += p * h["qty"]
                except Exception:
                    pass
        portfolio_values.append(cash + holding_value)

    # ── 최종 청산 (마지막 날 종가로) ──
    last_date = all_dates[-1] if all_dates else None
    for ticker, h in list(holdings.items()):
        if h["qty"] <= 0 or not last_date:
            continue
        if ticker not in price_data or last_date not in price_data[ticker].index:
            continue
        try:
            close = float(price_data[ticker].loc[last_date, "Close"])
        except Exception:
            continue
        pnl = (close - h["avg_price"]) * h["qty"]
        cash += close * h["qty"]
        all_trade_log.append({
            "date": last_date.strftime("%Y-%m-%d") if hasattr(last_date, 'strftime') else str(last_date)[:10],
            "action": "청산",
            "ticker": ticker,
            "name": tickers[ticker]["name"],
            "price": round(close, 2),
            "qty": h["qty"],
            "pnl": round(pnl, 0),
            "pnl_pct": round((close - h["avg_price"]) / h["avg_price"] * 100, 2),
            "condition": "기간 종료 자동 청산",
        })

    final_value = cash
    total_return_pct = round((final_value - initial_cash) / initial_cash * 100, 2)

    # 승률
    sell_trades = [t for t in all_trade_log if t["action"] in ("매도", "청산") and t.get("pnl") is not None]
    win_trades  = [t for t in sell_trades if (t.get("pnl") or 0) > 0]
    win_rate    = round(len(win_trades) / len(sell_trades) * 100, 1) if sell_trades else 0

    # MDD (최대낙폭)
    mdd_pct = 0.0
    if portfolio_values:
        peak = portfolio_values[0]
        for v in portfolio_values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > mdd_pct:
                mdd_pct = dd

    # 종목별 집계
    ticker_pnl = {}
    for t in all_trade_log:
        if t["action"] in ("매도", "청산") and t.get("pnl") is not None:
            tk = t["ticker"]
            if tk not in ticker_pnl:
                ticker_pnl[tk] = {"name": t["name"], "pnl": 0, "trades": 0, "wins": 0}
            ticker_pnl[tk]["pnl"] += t["pnl"]
            ticker_pnl[tk]["trades"] += 1
            if (t.get("pnl") or 0) > 0:
                ticker_pnl[tk]["wins"] += 1

    per_stock_results = [
        {
            "ticker": tk,
            "name": v["name"],
            "pnl": round(v["pnl"], 0),
            "trade_count": v["trades"],
            "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0,
        }
        for tk, v in ticker_pnl.items()
    ]

    return {
        "summary": {
            "initial_cash":      initial_cash,
            "final_value":       round(final_value, 0),
            "total_return_pct":  total_return_pct,
            "total_pnl":         round(final_value - initial_cash, 0),
            "trade_count":       len(all_trade_log),
            "sell_count":        len(sell_trades),
            "win_rate":          win_rate,
            "mdd_pct":           round(mdd_pct, 2),
            "period_days":       period_days,
        },
        "per_stock":  per_stock_results,
        "trade_log":  all_trade_log,
        "portfolio_values": [round(v, 0) for v in portfolio_values[-60:]],  # 최근 60개 포인트
    }
