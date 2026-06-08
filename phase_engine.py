"""
phase_engine.py — Phase 기반 자동매매 상태머신 엔진
=====================================================
전략을 Phase(단계) 단위로 관리.
각 조건은 "어느 Phase에서만 유효한지", "1회성인지", "실행 후 어느 Phase로 전환하는지"를 정의.

Phase State 구조:
{
    "phase": 0,                         # 현재 단계
    "triggered": {"rsi35": True, ...},  # 1회성 조건 실행 여부
    "locked": False,                    # 매수 잠금 여부
    "lock_reason": None,                # 잠금 원인 조건 ID
    "initial_qty": {"TQQQ": 100},       # 최초 매수 수량 (TP 기준)
    "entry_price": {"TQQQ": 45.0},      # 평균 매수가
}
"""

import logging
import re
from typing import Optional

log = logging.getLogger("phase-engine")


# ── 초기 Phase 상태 ──────────────────────────────
def initial_phase_state() -> dict:
    return {
        "phase": 0,
        "triggered": {},
        "locked": False,
        "lock_reason": None,
        "initial_qty": {},
        "entry_price": {},
    }


# ── 전략 평가: 현재 상태 + 가격 → 실행할 액션 목록 ──
def evaluate_strategy(
    strategy: dict,
    phase_state: dict,
    prices: dict,           # {"TQQQ": 45.0, "QQQ": 490.0, ...}
    portfolio_value: float = 1_000_000,
) -> list:
    """
    전략 + Phase 상태 + 현재 가격 → 실행할 액션 목록

    Returns list of action dicts:
      buy:   {"type":"buy",  "ticker":..., "weight_pct":..., "weight_mode":..., "condition_id":..., "next_phase":..., ...}
      sell:  {"type":"sell", "ticker":..., "sell_pct":...,   "sell_mode":...,   "condition_id":..., "next_phase":..., ...}
      lock:  {"type":"lock",   "reason":..., "lock_id":...}
      unlock:{"type":"unlock", "lock_id":...}
    """
    actions = []
    current_phase = phase_state.get("phase", 0)
    triggered = phase_state.get("triggered", {})
    locked = phase_state.get("locked", False)

    # ── 1) 락 조건 체크 (매수보다 먼저) ──────────
    for lc in strategy.get("lock_conditions", []):
        lid = lc.get("id", "lock")
        req_phase = lc.get("required_phase", "any")

        # Phase 필터
        if req_phase != "any" and current_phase not in req_phase:
            # 락 해제 조건만 체크 (현재 이 락으로 잠겨있을 경우)
            if locked and phase_state.get("lock_reason") == lid:
                pass  # 해제 체크는 아래에서
            else:
                continue

        cond_str = lc.get("condition", "")

        # Phase 기반 조건 (예: "phase == 6")
        if cond_str.startswith("phase"):
            m = re.search(r"phase\s*==\s*(\d+)", cond_str)
            cond_met = (m and current_phase == int(m.group(1)))
        else:
            ref_ticker = lc.get("ref_ticker", strategy.get("ref_ticker", ""))
            ticker = lc.get("ticker", ref_ticker)
            price = prices.get(ticker, prices.get(ref_ticker, 0))
            ref_price = prices.get(ref_ticker, price)
            buy_price = phase_state.get("entry_price", {}).get(ticker, 0)
            cond_met = _eval_cond(cond_str, ticker, ref_ticker, price, ref_price, buy_price)

        if cond_met and not locked:
            actions.append({"type": "lock", "reason": cond_str, "lock_id": lid})
            return actions  # 락이면 즉시 반환

        # 잠금 해제 체크
        if locked and phase_state.get("lock_reason") == lid:
            release = lc.get("release_condition", "")
            if release:
                ref_ticker = lc.get("ref_ticker", strategy.get("ref_ticker", ""))
                ticker = lc.get("ticker", ref_ticker)
                price = prices.get(ticker, prices.get(ref_ticker, 0))
                ref_price = prices.get(ref_ticker, price)
                buy_price = phase_state.get("entry_price", {}).get(ticker, 0)
                # release_condition은 "phase == N" 또는 일반 조건
                if release.startswith("phase"):
                    m = re.search(r"phase\s*==\s*(\d+)", release)
                    release_met = (m and current_phase == int(m.group(1)))
                else:
                    release_met = _eval_cond(release, ticker, ref_ticker, price, ref_price, buy_price)
                if release_met:
                    actions.append({"type": "unlock", "lock_id": lid})

    # 잠겨 있으면 매수/매도 없음
    if locked and not any(a["type"] == "unlock" for a in actions):
        return actions

    # ── 2) 일반 조건 체크 ─────────────────────────
    for cond_def in strategy.get("conditions", []):
        cid = cond_def.get("id", "")
        req_phase = cond_def.get("required_phase", [0])
        one_time = cond_def.get("one_time", False)
        cond_type = cond_def.get("type", "buy")

        # Phase 필터
        if current_phase not in req_phase:
            continue

        # 1회성 이미 실행됐으면 스킵
        if one_time and triggered.get(cid, False):
            continue

        ticker = cond_def.get("ticker", strategy.get("ticker", ""))
        ref_ticker = cond_def.get("ref_ticker", strategy.get("ref_ticker", ticker))
        cond_str = cond_def.get("condition", "")
        price = prices.get(ticker, 0)
        ref_price = prices.get(ref_ticker, price)
        buy_price = phase_state.get("entry_price", {}).get(ticker, 0)

        cond_met = _eval_cond(cond_str, ticker, ref_ticker, price, ref_price, buy_price)
        if not cond_met:
            continue

        # 액션 구성
        action_def = cond_def.get("action", {})
        action = {
            **action_def,
            "ticker": ticker,
            "condition_id": cid,
            "condition_name": cond_def.get("name", cid),
            "next_phase": cond_def.get("next_phase"),
            "one_time": one_time,
            "reset_on_trigger": cond_def.get("reset_on_trigger", False),
            "price": price,
        }
        actions.append(action)

        # 같은 체크 사이클에서 매도/매수 중 하나만 (가장 먼저 트리거된 것)
        # DC 같은 리셋 조건은 즉시 반환
        if cond_def.get("reset_on_trigger", False):
            return actions

    return actions


# ── Phase 상태 업데이트 ──────────────────────────
def apply_action_to_state(phase_state: dict, action: dict) -> dict:
    """
    액션 실행 결과를 Phase 상태에 반영.
    (실제 주문 실행은 auto_trader.py 담당, 여기선 상태만 변경)
    """
    state = {
        **phase_state,
        "triggered": dict(phase_state.get("triggered", {})),
        "initial_qty": dict(phase_state.get("initial_qty", {})),
        "entry_price": dict(phase_state.get("entry_price", {})),
    }

    atype = action.get("type")

    if atype == "lock":
        state["locked"] = True
        state["lock_reason"] = action.get("lock_id")
        return state

    if atype == "unlock":
        state["locked"] = False
        state["lock_reason"] = None
        return state

    cid = action.get("condition_id")

    # 1회성 기록
    if action.get("one_time") and cid:
        state["triggered"][cid] = True

    # Phase 전환
    next_phase = action.get("next_phase")
    if next_phase is not None:
        state["phase"] = next_phase

    # 리셋 (DC 등)
    if action.get("reset_on_trigger"):
        state["triggered"] = {}
        state["initial_qty"] = {}
        state["entry_price"] = {}
        state["locked"] = False
        state["lock_reason"] = None

    return state


def record_buy(phase_state: dict, ticker: str, qty: int, price: float) -> dict:
    """매수 실행 후 entry_price / initial_qty 기록."""
    state = {
        **phase_state,
        "initial_qty": dict(phase_state.get("initial_qty", {})),
        "entry_price": dict(phase_state.get("entry_price", {})),
    }
    prev_qty = state["initial_qty"].get(ticker, 0)
    prev_price = state["entry_price"].get(ticker, 0)

    # 평균단가 갱신
    if prev_qty + qty > 0:
        state["entry_price"][ticker] = (
            (prev_price * prev_qty + price * qty) / (prev_qty + qty)
        )
    # initial_qty는 Phase 0 리셋 후 첫 매수 시에만 세팅
    if prev_qty == 0:
        state["initial_qty"][ticker] = qty
    return state


def record_sell(phase_state: dict, ticker: str, qty: int) -> dict:
    """매도 실행 후 보유수량 반영 (entry_price는 유지)."""
    # initial_qty는 TP 계산 기준이므로 매도해도 줄이지 않음
    # 실제 보유는 KIS API에서 조회
    return phase_state


# ── 내부: 조건 평가 ──────────────────────────────
def _eval_cond(
    condition: str,
    ticker: str,
    ref_ticker: str,
    price: float,
    ref_price: float,
    buy_price: float = 0,
) -> bool:
    """condition_evaluator.evaluate()를 호출."""
    try:
        from condition_evaluator import evaluate
        return evaluate(condition, ticker, ref_ticker, buy_price, price)
    except Exception as e:
        log.warning(f"조건 평가 오류 [{condition}]: {e}")
        return False


# ── Phase 이름 조회 헬퍼 ──────────────────────────
def get_phase_name(strategy: dict, phase: int) -> str:
    phases = strategy.get("phases", {})
    return phases.get(str(phase), f"Phase {phase}")
