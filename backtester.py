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
# 조건 평가
# ─────────────────────────────────────────────
def _eval_price_condition(condition_str: str, current_price: float,
                           buy_price: Optional[float] = None) -> bool:
    cond = condition_str.strip().lower()

    if cond in ("항상", "always", "즉시", "매일"):
        return True

    # 수익률 조건 (보유 중일 때만)
    if ("수익률" in cond or "익절" in cond) and buy_price:
        try:
            pct = (current_price - buy_price) / buy_price * 100
            if ">=" in cond:
                threshold = float(cond.split(">=")[1].replace("%", "").strip())
                return pct >= threshold
            elif ">" in cond:
                threshold = float(cond.split(">")[1].replace("%", "").strip())
                return pct > threshold
        except Exception:
            pass

    # 손실률 조건
    if ("손실률" in cond or "손절" in cond) and buy_price:
        try:
            pct = (current_price - buy_price) / buy_price * 100
            if ">=" in cond:
                threshold = float(cond.split(">=")[1].replace("%", "").strip())
                return pct <= -threshold
            elif ">" in cond:
                threshold = float(cond.split(">")[1].replace("%", "").strip())
                return pct < -threshold
        except Exception:
            pass

    # 현재가 비교
    for keyword in ["현재가", "price", "가격", "종가"]:
        if keyword in cond:
            try:
                if "<=" in cond:
                    t = float(cond.split("<=")[1].strip().replace(",", ""))
                    return current_price <= t
                elif ">=" in cond:
                    t = float(cond.split(">=")[1].strip().replace(",", ""))
                    return current_price >= t
                elif "<" in cond:
                    t = float(cond.split("<")[1].strip().replace(",", ""))
                    return current_price < t
                elif ">" in cond:
                    t = float(cond.split(">")[1].strip().replace(",", ""))
                    return current_price > t
            except Exception:
                pass

    # RSI, MACD 등 미지원 → False
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
        t = c.get("ticker")
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
            if not close or close != close:  # NaN 체크
                continue

            if _eval_price_condition(cond.get("condition", ""), close):
                qty_raw = cond.get("qty", 1)
                qty = 1 if str(qty_raw) == "all" else int(qty_raw)
                cost = close * qty
                if cash >= cost:
                    cash -= cost
                    if ticker not in holdings:
                        holdings[ticker] = {"qty": 0, "avg_price": 0.0, "trades": []}
                    prev_qty = holdings[ticker]["qty"]
                    prev_avg = holdings[ticker]["avg_price"]
                    new_qty  = prev_qty + qty
                    holdings[ticker]["avg_price"] = (prev_avg * prev_qty + close * qty) / new_qty
                    holdings[ticker]["qty"] = new_qty
                    entry = {
                        "date": day_str,
                        "action": "매수",
                        "ticker": ticker,
                        "name": tickers[ticker]["name"],
                        "price": round(close, 2),
                        "qty": qty,
                        "pnl": None,
                        "condition": cond.get("condition", ""),
                    }
                    all_trade_log.append(entry)

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

            buy_price = holdings[ticker]["avg_price"]
            if _eval_price_condition(cond.get("condition", ""), close, buy_price):
                qty_raw = cond.get("qty", 1)
                qty = holdings[ticker]["qty"] if str(qty_raw) == "all" else min(int(qty_raw), holdings[ticker]["qty"])
                proceeds = close * qty
                pnl = (close - buy_price) * qty
                cash += proceeds
                holdings[ticker]["qty"] -= qty
                if holdings[ticker]["qty"] <= 0:
                    del holdings[ticker]
                entry = {
                    "date": day_str,
                    "action": "매도",
                    "ticker": ticker,
                    "name": tickers[ticker]["name"],
                    "price": round(close, 2),
                    "qty": qty,
                    "pnl": round(pnl, 0),
                    "pnl_pct": round((close - buy_price) / buy_price * 100, 2),
                    "condition": cond.get("condition", ""),
                }
                all_trade_log.append(entry)

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
