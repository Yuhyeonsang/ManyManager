"""
backtester.py — 비중 기반 백테스트 엔진 (v2)
================================================
weight_pct / sell_pct 기반 포트폴리오 전략 지원.

신규 지원:
  - weight_pct + weight_mode (target/add): 포트폴리오 비중으로 매수
  - sell_pct + sell_mode (current/initial_qty): 비율로 매도
  - ref_ticker: QQQ 기준으로 TQQQ 매수 조건 판단
  - sub_conditions + condition_logic (AND/OR): 복합 조건
  - lock_conditions: 매수 잠금 / 전량 청산

레거시 호환:
  - qty 기반 조건도 그대로 동작
"""

import logging
import re as _re
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("backtester")


# ─────────────────────────────────────────────
# 기술적 지표 계산
# ─────────────────────────────────────────────
def _calc_indicators(df, idx: int) -> dict:
    closes = df["Close"].iloc[:idx + 1]
    n = len(closes)
    result = {}

    def _ma(period):
        if n >= period:
            return float(closes.iloc[-period:].mean())
        return float(closes.mean())

    result["ma5"]   = _ma(5)
    result["ma20"]  = _ma(20)
    result["ma50"]  = _ma(50)
    result["ma200"] = _ma(200)
    result["ma220"] = _ma(220)

    if n >= 20:
        mid = closes.iloc[-20:].mean()
        std = closes.iloc[-20:].std()
        result["bb_mid"]   = float(mid)
        result["bb_upper"] = float(mid + 2 * std)
        result["bb_lower"] = float(mid - 2 * std)
    else:
        result["bb_mid"] = result["bb_upper"] = result["bb_lower"] = float(closes.iloc[-1])

    peak_window = min(n, 252)
    peak = float(closes.iloc[-peak_window:].max())
    current = float(closes.iloc[-1])
    result["peak_52w"]     = peak
    result["drawdown_pct"] = (current - peak) / peak * 100 if peak else 0.0

    if n >= 15:
        deltas = closes.diff().dropna().iloc[-14:]
        gains  = deltas.clip(lower=0).mean()
        losses = (-deltas.clip(upper=0)).mean()
        rs     = gains / losses if losses != 0 else 100
        result["rsi"] = float(100 - 100 / (1 + rs))
    else:
        result["rsi"] = 50.0

    result["golden_cross"] = False
    result["dead_cross"]   = False
    if n >= 221:
        prev_ma5   = float(closes.iloc[-6:-1].mean())
        prev_ma220 = float(closes.iloc[-221:-1].mean())
        cur_ma5    = result["ma5"]
        cur_ma220  = result["ma220"]
        if prev_ma5 <= prev_ma220 and cur_ma5 > cur_ma220:
            result["golden_cross"] = True
        if prev_ma5 >= prev_ma220 and cur_ma5 < cur_ma220:
            result["dead_cross"] = True

    return result


# ─────────────────────────────────────────────
# 단일 조건 문자열 평가
# ─────────────────────────────────────────────
def _eval_condition(condition_str: str, current_price: float,
                    indicators: dict,
                    buy_price: Optional[float] = None,
                    ref_indicators: Optional[dict] = None) -> bool:
    cond   = condition_str.strip()
    cond_l = cond.lower()

    if cond_l in ("항상", "always", "즉시", "매일"):
        return True

    profit_patterns = [
        r"tp\d*\s*[\(\s]*\+?\s*(\d+(?:\.\d+)?)\s*%",
        r"tp\s*\+?\s*(\d+(?:\.\d+)?)\s*%",
        r"수익률\s*[>≥]\s*(\d+(?:\.\d+)?)\s*%",
        r"수익\s*(\d+(?:\.\d+)?)\s*%\s*이상",
        r"\+\s*(\d+(?:\.\d+)?)\s*%\s*도달",
    ]
    if buy_price and buy_price > 0:
        ret_pct = (current_price - buy_price) / buy_price * 100
        for pat in profit_patterns:
            m = _re.search(pat, cond_l)
            if m:
                return ret_pct >= float(m.group(1))

    loss_patterns = [
        r"손절\s*-?\s*(\d+(?:\.\d+)?)\s*%",
        r"손실률?\s*[>≥]\s*(\d+(?:\.\d+)?)\s*%",
    ]
    if buy_price and buy_price > 0:
        ret_pct = (current_price - buy_price) / buy_price * 100
        for pat in loss_patterns:
            m = _re.search(pat, cond_l)
            if m:
                return ret_pct <= -float(m.group(1))

    ind = ref_indicators if ref_indicators else indicators
    dd_patterns = [
        r"고점\s*대비\s*-\s*(\d+(?:\.\d+)?)\s*%",
        r"고점\s*대비\s*(\d+(?:\.\d+)?)\s*%\s*하락",
        r"고점\s*대비\s*(\d+(?:\.\d+)?)\s*%\s*이하",
        r"-\s*(\d+(?:\.\d+)?)\s*%\s*하락",
    ]
    for pat in dd_patterns:
        m = _re.search(pat, cond_l)
        if m:
            return ind.get("drawdown_pct", 0) <= -float(m.group(1))

    ma_pct_m = _re.search(
        r"(\d+)일선?\s*대비\s*-?\s*(\d+(?:\.\d+)?)\s*%\s*(이하|아래|미만)", cond_l)
    if ma_pct_m:
        period = int(ma_pct_m.group(1))
        pct    = float(ma_pct_m.group(2))
        ma_val = ind.get(f"ma{period}", indicators.get(f"ma{period}"))
        if ma_val:
            return current_price <= ma_val * (1 - pct / 100)

    ma_below_m = _re.search(r"(\d+)일선?\s*(이하|아래|미만|하회)", cond_l)
    if ma_below_m:
        period = int(ma_below_m.group(1))
        ma_val = indicators.get(f"ma{period}")
        if ma_val:
            return current_price < ma_val

    gc_ma_m = _re.search(r"(\d+)일\s*이평선이?\s*(\d+)일\s*이평선\s*(상향\s*돌파|돌파)", cond_l)
    if gc_ma_m:
        fast = int(gc_ma_m.group(1))
        slow = int(gc_ma_m.group(2))
        return indicators.get(f"ma{fast}", 0) > indicators.get(f"ma{slow}", 0)

    dc_ma_m = _re.search(r"(\d+)일\s*이평선이?\s*(\d+)일\s*이평선\s*(하향\s*돌파|하락\s*돌파)", cond_l)
    if dc_ma_m:
        fast = int(dc_ma_m.group(1))
        slow = int(dc_ma_m.group(2))
        return indicators.get(f"ma{fast}", 0) < indicators.get(f"ma{slow}", 0)

    if _re.search(r"고점\s*돌파|신고가", cond_l):
        return current_price >= indicators.get("peak_52w", current_price)

    rsi_m = _re.search(r"rsi\s*([<>≤≥]=?)\s*(\d+(?:\.\d+)?)", cond_l)
    if rsi_m:
        op, val = rsi_m.group(1), float(rsi_m.group(2))
        rsi = indicators.get("rsi", 50)
        if "<=" in op or "≤" in op: return rsi <= val
        if ">=" in op or "≥" in op: return rsi >= val
        if "<"  in op: return rsi <  val
        if ">"  in op: return rsi >  val

    if _re.search(r"골든\s*크로스|golden\s*cross|\bgc\b", cond_l):
        return indicators.get("golden_cross", False)
    if _re.search(r"데드\s*크로스|dead\s*cross|\bdc\b", cond_l):
        return indicators.get("dead_cross", False)

    if _re.search(r"볼린저.*(상단|상향)\s*(이탈|돌파|초과)", cond_l):
        return current_price >= indicators.get("bb_upper", current_price + 1)
    if _re.search(r"볼린저.*(하단|하향)\s*(이탈|터치|이하|붕괴)", cond_l):
        return current_price <= indicators.get("bb_lower", current_price - 1)

    for kw in ["현재가", "price", "가격", "종가"]:
        if kw in cond_l:
            try:
                if "<=" in cond: return current_price <= float(_re.search(r"<=\s*([\d,]+)", cond).group(1).replace(",", ""))
                if ">=" in cond: return current_price >= float(_re.search(r">=\s*([\d,]+)", cond).group(1).replace(",", ""))
                if "<"  in cond: return current_price <  float(_re.search(r"<\s*([\d,]+)",  cond).group(1).replace(",", ""))
                if ">"  in cond: return current_price >  float(_re.search(r">\s*([\d,]+)",  cond).group(1).replace(",", ""))
            except Exception:
                pass

    log.debug("미지원 조건 (건너뜀): %s", condition_str)
    return False


# ─────────────────────────────────────────────
# 복합 조건 dict 평가
# ─────────────────────────────────────────────
def _eval_cond_dict(cond_dict: dict, current_price: float,
                    indicators: dict,
                    buy_price: Optional[float] = None,
                    ref_indicators: Optional[dict] = None) -> bool:
    sub   = cond_dict.get("sub_conditions") or []
    logic = (cond_dict.get("condition_logic") or "AND").upper()
    main  = cond_dict.get("condition") or ""

    if sub and len(sub) > 1:
        results = [
            _eval_condition(s, current_price, indicators, buy_price, ref_indicators)
            for s in sub
        ]
        return any(results) if logic == "OR" else all(results)

    return _eval_condition(main, current_price, indicators, buy_price, ref_indicators)


# ─────────────────────────────────────────────
# 백테스트 실행
# ─────────────────────────────────────────────
def run_backtest(conditions: dict, period_days: int = 90,
                 initial_cash: float = 10_000_000) -> dict:
    import os
    from yahoo_direct import download_tiingo
    from data_collector import _yf_history_safe

    USD_KRW = float(os.environ.get("USD_KRW", 1450))

    buy_conds  = conditions.get("buy_conditions", [])
    sell_conds = conditions.get("sell_conditions", [])
    lock_conds = conditions.get("lock_conditions", [])
    is_weight  = conditions.get("strategy_type") == "weight_based" or any(
        c.get("weight_pct") is not None for c in buy_conds
    )

    # 종목 수집
    tickers = {}
    for c in buy_conds + sell_conds:
        for field in ("ticker", "name"):
            t = (c.get(field) or "").strip().upper()
            if t and len(t) >= 2:
                tickers[t] = {"name": c.get("name", t)}
                break
        ref = (c.get("ref_ticker") or "").strip().upper()
        if ref:
            tickers[ref] = {"name": ref}

    if not tickers:
        return {
            "summary": {"error": "종목 정보 없음. 이미지에 종목코드가 있는지 확인하세요."},
            "per_stock": [], "trade_log": [],
        }

    # 데이터 로드
    def _load(sym, days):
        yf_sym = sym + ".KS" if (sym.isdigit() and len(sym) == 6) else sym
        is_kr  = yf_sym.endswith(".KS") or yf_sym.endswith(".KQ")
        period_str = f"{days}d"
        if is_kr:
            df = _yf_history_safe(yf_sym, period=period_str)
        else:
            df = download_tiingo(yf_sym, period=period_str)
        if not df.empty and "Close" not in df.columns:
            df.columns = [c.capitalize() for c in df.columns]
        return df, yf_sym

    price_data = {}
    yf_map     = {}
    for sym in list(tickers.keys()):
        try:
            df, yf_sym = _load(sym, period_days + 260)
            if not df.empty:
                price_data[sym] = df
                yf_map[sym]     = yf_sym
        except Exception as e:
            log.warning(f"데이터 로드 실패 ({sym}): {e}")

    # 조건 텍스트에서 참조 티커 추가 감지
    for c in buy_conds + sell_conds:
        m = _re.search(r'\b([A-Z]{2,5})\s*(고점|기준|대비)', (c.get("condition") or "").upper())
        if m:
            ref_t = m.group(1)
            if ref_t not in price_data:
                try:
                    df, yf_sym = _load(ref_t, period_days + 260)
                    if not df.empty:
                        price_data[ref_t] = df
                        yf_map[ref_t]     = yf_sym
                        tickers[ref_t]    = {"name": ref_t}
                except Exception:
                    pass

    if not price_data:
        return {
            "summary": {"error": "주가 데이터를 가져오지 못했습니다."},
            "per_stock": [], "trade_log": [],
        }

    def _is_usd(sym):
        yf = yf_map.get(sym, sym)
        return not (yf.endswith(".KS") or yf.endswith(".KQ") or
                    (sym.isdigit() and len(sym) == 6))

    def _to_krw(price, sym):
        return price * USD_KRW if _is_usd(sym) else price

    def _get_ref_ind(cond_dict, date, fallback_ind):
        ref_t = (cond_dict.get("ref_ticker") or "").strip().upper()
        if not ref_t:
            m = _re.search(r'\b([A-Z]{2,5})\s*(고점|기준|대비)',
                           (cond_dict.get("condition") or "").upper())
            if m:
                ref_t = m.group(1)
        if ref_t and ref_t in price_data:
            ref_df    = price_data[ref_t]
            ref_dates = ref_df.index[ref_df.index <= date]
            if len(ref_dates):
                ref_idx = ref_df.index.get_loc(ref_dates[-1])
                return _calc_indicators(ref_df, ref_idx)
        return None

    # 공통 날짜
    all_dates = set()
    for df in price_data.values():
        all_dates.update(df.index.tolist())
    all_dates = sorted(all_dates)

    # 시뮬레이션 상태
    cash            = initial_cash
    holdings        = {}   # ticker -> {qty, avg_price, initial_qty}
    buy_locked      = False
    cond_triggered  = {}   # (ticker, cond_idx) -> last_date

    all_trade_log    = []
    portfolio_values = []

    for date in all_dates:
        day_str = date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)[:10]

        def _portfolio_value():
            val = cash
            for sym, h in holdings.items():
                if sym in price_data and date in price_data[sym].index:
                    try:
                        p = float(price_data[sym].loc[date, "Close"])
                        val += _to_krw(p, sym) * h["qty"]
                    except Exception:
                        pass
            return val

        total_value = _portfolio_value()

        # 1. 락 조건 체크
        for lc in lock_conds:
            lc_text = lc.get("condition") or ""
            lc_ref  = None
            m = _re.search(r'\b([A-Z]{2,5})\s*(고점|기준|대비)', lc_text.upper())
            if m and m.group(1) in price_data:
                ref_t  = m.group(1)
                ref_df = price_data[ref_t]
                ref_dates = ref_df.index[ref_df.index <= date]
                if len(ref_dates):
                    lc_ref = _calc_indicators(ref_df, ref_df.index.get_loc(ref_dates[-1]))

            triggered = _eval_condition(lc_text, 100.0, lc_ref or {}, ref_indicators=lc_ref)
            if triggered:
                action = lc.get("action", "lock_buy")
                if not buy_locked:
                    log.info(f"[{day_str}] 락 발동: {lc_text}")
                buy_locked = True
                if action == "liquidate":
                    for sym, h in list(holdings.items()):
                        if h["qty"] <= 0:
                            continue
                        if sym not in price_data or date not in price_data[sym].index:
                            continue
                        try:
                            close     = float(price_data[sym].loc[date, "Close"])
                            close_krw = _to_krw(close, sym)
                            pnl       = (close_krw - h["avg_price"]) * h["qty"]
                            cash     += close_krw * h["qty"]
                            all_trade_log.append({
                                "date": day_str, "action": "락청산",
                                "ticker": sym, "name": tickers.get(sym, {}).get("name", sym),
                                "price": round(close_krw, 0), "qty": h["qty"],
                                "pnl": round(pnl, 0),
                                "pnl_pct": round((close_krw - h["avg_price"]) / h["avg_price"] * 100, 2),
                                "condition": lc_text,
                            })
                        except Exception:
                            pass
                    holdings.clear()
                break

        # 2. 매도 조건 체크
        for cond in sell_conds:
            ticker = (cond.get("ticker") or cond.get("name") or "").strip().upper()
            if not ticker or ticker not in price_data:
                continue
            if ticker not in holdings or holdings[ticker]["qty"] <= 0:
                continue
            if date not in price_data[ticker].index:
                continue

            try:
                close     = float(price_data[ticker].loc[date, "Close"])
                close_krw = _to_krw(close, ticker)
            except Exception:
                continue

            idx_i      = price_data[ticker].index.get_loc(date)
            indicators = _calc_indicators(price_data[ticker], idx_i)
            buy_price  = holdings[ticker]["avg_price"]
            ref_ind    = _get_ref_ind(cond, date, indicators)

            if not _eval_cond_dict(cond, close_krw, indicators,
                                   buy_price=buy_price, ref_indicators=ref_ind):
                continue

            h         = holdings[ticker]
            sell_pct  = cond.get("sell_pct")
            sell_mode = cond.get("sell_mode", "current")
            qty_raw   = cond.get("qty", None)

            if sell_pct is not None:
                base_qty = h.get("initial_qty", h["qty"]) if sell_mode == "initial_qty" else h["qty"]
                sell_qty = max(1, round(base_qty * sell_pct / 100))
            elif qty_raw is not None:
                sell_qty = h["qty"] if str(qty_raw) == "all" else int(qty_raw)
            else:
                sell_qty = h["qty"]

            sell_qty  = min(sell_qty, h["qty"])
            if sell_qty <= 0:
                continue

            proceeds = close_krw * sell_qty
            pnl      = (close_krw - buy_price) * sell_qty
            cash    += proceeds
            h["qty"] -= sell_qty
            if h["qty"] <= 0:
                del holdings[ticker]

            all_trade_log.append({
                "date": day_str, "action": "매도",
                "ticker": ticker, "name": tickers.get(ticker, {}).get("name", ticker),
                "price": round(close_krw, 0), "qty": sell_qty,
                "pnl": round(pnl, 0),
                "pnl_pct": round((close_krw - buy_price) / buy_price * 100, 2),
                "condition": cond.get("condition") or "",
                "label": cond.get("label"),
            })

        # 3. 매수 조건 체크
        if not buy_locked:
            total_value = _portfolio_value()

            for cond_idx, cond in enumerate(buy_conds):
                ticker = (cond.get("ticker") or cond.get("name") or "").strip().upper()
                if not ticker or ticker not in price_data:
                    continue
                if date not in price_data[ticker].index:
                    continue

                cond_key = (ticker, cond_idx)
                if cond_triggered.get(cond_key) == day_str:
                    continue

                try:
                    close     = float(price_data[ticker].loc[date, "Close"])
                    close_krw = _to_krw(close, ticker)
                except Exception:
                    continue

                idx_i      = price_data[ticker].index.get_loc(date)
                indicators = _calc_indicators(price_data[ticker], idx_i)
                ref_ind    = _get_ref_ind(cond, date, indicators)

                if not _eval_cond_dict(cond, close_krw, indicators, ref_indicators=ref_ind):
                    continue

                cond_triggered[cond_key] = day_str

                weight_pct  = cond.get("weight_pct")
                weight_mode = cond.get("weight_mode", "target")
                qty_raw     = cond.get("qty", 1)

                if is_weight and weight_pct is not None:
                    if weight_mode == "target":
                        target_value  = total_value * weight_pct / 100
                        current_value = holdings.get(ticker, {}).get("qty", 0) * close_krw
                        buy_value     = target_value - current_value
                        if buy_value <= 0:
                            continue
                        qty = max(1, int(buy_value // close_krw))
                    else:
                        add_value = total_value * weight_pct / 100
                        qty       = max(1, int(add_value // close_krw))
                else:
                    qty = max(1, int(cash // close_krw)) if str(qty_raw) == "all" else int(qty_raw)

                cost = close_krw * qty
                if cash < cost:
                    qty  = max(0, int(cash // close_krw))
                    cost = close_krw * qty
                if qty <= 0:
                    continue

                cash -= cost
                prev_qty = holdings.get(ticker, {}).get("qty", 0)
                prev_avg = holdings.get(ticker, {}).get("avg_price", 0.0)
                new_qty  = prev_qty + qty
                new_avg  = (prev_avg * prev_qty + close_krw * qty) / new_qty

                if ticker not in holdings:
                    holdings[ticker] = {"qty": 0, "avg_price": 0.0, "initial_qty": qty}
                holdings[ticker]["qty"]       = new_qty
                holdings[ticker]["avg_price"] = new_avg
                if weight_mode != "add" and prev_qty == 0:
                    holdings[ticker]["initial_qty"] = qty

                all_trade_log.append({
                    "date": day_str, "action": "매수",
                    "ticker": ticker, "name": tickers.get(ticker, {}).get("name", ticker),
                    "price": round(close_krw, 0), "qty": qty,
                    "weight_pct": weight_pct,
                    "pnl": None,
                    "condition": cond.get("condition") or "",
                    "label": cond.get("label"),
                })

        portfolio_values.append(_portfolio_value())

    # 최종 청산
    last_date = all_dates[-1] if all_dates else None
    for ticker, h in list(holdings.items()):
        if h["qty"] <= 0 or not last_date:
            continue
        if ticker not in price_data or last_date not in price_data[ticker].index:
            continue
        try:
            close     = float(price_data[ticker].loc[last_date, "Close"])
            close_krw = _to_krw(close, ticker)
            pnl       = (close_krw - h["avg_price"]) * h["qty"]
            cash     += close_krw * h["qty"]
            last_str  = last_date.strftime("%Y-%m-%d") if hasattr(last_date, "strftime") else str(last_date)[:10]
            all_trade_log.append({
                "date": last_str, "action": "청산",
                "ticker": ticker, "name": tickers.get(ticker, {}).get("name", ticker),
                "price": round(close_krw, 0), "qty": h["qty"],
                "pnl": round(pnl, 0),
                "pnl_pct": round((close_krw - h["avg_price"]) / h["avg_price"] * 100, 2),
                "condition": "기간 종료 자동 청산",
            })
        except Exception:
            pass

    final_value      = cash
    total_return_pct = round((final_value - initial_cash) / initial_cash * 100, 2)

    sell_trades = [t for t in all_trade_log if t["action"] in ("매도", "청산", "락청산") and t.get("pnl") is not None]
    win_trades  = [t for t in sell_trades if (t.get("pnl") or 0) > 0]
    win_rate    = round(len(win_trades) / len(sell_trades) * 100, 1) if sell_trades else 0

    mdd_pct = 0.0
    if portfolio_values:
        peak = portfolio_values[0]
        for v in portfolio_values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100 if peak else 0
            if dd > mdd_pct:
                mdd_pct = dd

    ticker_pnl = {}
    for t in all_trade_log:
        if t["action"] in ("매도", "청산", "락청산") and t.get("pnl") is not None:
            tk = t["ticker"]
            if tk not in ticker_pnl:
                ticker_pnl[tk] = {"name": t.get("name", tk), "pnl": 0, "trades": 0, "wins": 0}
            ticker_pnl[tk]["pnl"]    += t["pnl"]
            ticker_pnl[tk]["trades"] += 1
            if (t.get("pnl") or 0) > 0:
                ticker_pnl[tk]["wins"] += 1

    per_stock_results = [
        {
            "ticker": tk, "name": v["name"],
            "pnl": round(v["pnl"], 0),
            "trade_count": v["trades"],
            "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0,
        }
        for tk, v in ticker_pnl.items()
    ]

    return {
        "summary": {
            "initial_cash":     initial_cash,
            "final_value":      round(final_value, 0),
            "total_return_pct": total_return_pct,
            "total_pnl":        round(final_value - initial_cash, 0),
            "trade_count":      len(all_trade_log),
            "sell_count":       len(sell_trades),
            "win_rate":         win_rate,
            "mdd_pct":          round(mdd_pct, 2),
            "period_days":      period_days,
            "buy_locked":       buy_locked,
        },
        "per_stock":        per_stock_results,
        "trade_log":        all_trade_log,
        "portfolio_values": [round(v, 0) for v in portfolio_values[-60:]],
    }
