"""
phase_engine.py
"""

import logging
import re

log = logging.getLogger("phase-engine")


def initial_phase_state() -> dict:
    return {
        "phase": 0,
        "triggered": {},
        "locked": False,
        "lock_reason": None,
        "initial_qty": {},
        "entry_price": {},
        "lock_steps": {},   # release_steps 진행 상태: {lock_id: step_index}
    }


def evaluate_strategy(strategy, phase_state, prices, portfolio_value=1_000_000):
    actions = []
    current_phase = phase_state.get("phase", 0)
    triggered = phase_state.get("triggered", {})
    locked = phase_state.get("locked", False)

    for lc in strategy.get("lock_conditions", []):
        lid = lc.get("id", "lock")
        req_phase = lc.get("required_phase", "any")

        if req_phase != "any" and current_phase not in req_phase:
            if locked and phase_state.get("lock_reason") == lid:
                pass
            else:
                continue

        cond_str = lc.get("condition", "")
        sub_conds = lc.get("sub_conditions", [])
        cond_logic = lc.get("condition_logic", "AND") or "AND"

        if cond_str.startswith("phase"):
            m = re.search(r"phase\s*==\s*(\d+)", cond_str)
            cond_met = (m and current_phase == int(m.group(1)))
        else:
            ref_ticker = lc.get("ref_ticker", strategy.get("ref_ticker", ""))
            ticker = lc.get("ticker", ref_ticker)
            price = prices.get(ticker, prices.get(ref_ticker, 0))
            ref_price = prices.get(ref_ticker, price)
            buy_price = phase_state.get("entry_price", {}).get(ticker, 0)
            cond_met = _eval_cond(cond_str, ticker, ref_ticker, price, ref_price, buy_price,
                                  sub_conds, cond_logic)

        if cond_met and not locked:
            actions.append({"type": "lock", "reason": cond_str, "lock_id": lid})
            return actions

        if locked and phase_state.get("lock_reason") == lid:
            release = lc.get("release_condition", "")
            release_steps = lc.get("release_steps", [])
            if release_steps:
                # 순서 조건 처리: step_index 순서대로 모두 충족해야 해제
                ref_ticker = lc.get("ref_ticker", strategy.get("ref_ticker", ""))
                ticker = lc.get("ticker", ref_ticker)
                price = prices.get(ticker, prices.get(ref_ticker, 0))
                ref_price = prices.get(ref_ticker, price)
                buy_price = phase_state.get("entry_price", {}).get(ticker, 0)
                cur_step = phase_state.get("lock_steps", {}).get(lid, 0)
                if cur_step < len(release_steps):
                    step_cond = release_steps[cur_step]["condition"]
                    step_met = _eval_cond(step_cond, ticker, ref_ticker, price, ref_price, buy_price)
                    if step_met:
                        next_step = cur_step + 1
                        if next_step >= len(release_steps):
                            actions.append({"type": "unlock", "lock_id": lid})
                        else:
                            actions.append({"type": "advance_lock_step", "lock_id": lid, "step": next_step})
            elif release:
                ref_ticker = lc.get("ref_ticker", strategy.get("ref_ticker", ""))
                ticker = lc.get("ticker", ref_ticker)
                price = prices.get(ticker, prices.get(ref_ticker, 0))
                ref_price = prices.get(ref_ticker, price)
                buy_price = phase_state.get("entry_price", {}).get(ticker, 0)
                if release.startswith("phase"):
                    m = re.search(r"phase\s*==\s*(\d+)", release)
                    release_met = (m and current_phase == int(m.group(1)))
                else:
                    release_sub = lc.get("release_sub_conditions", [])
                    release_logic = lc.get("release_condition_logic", "AND") or "AND"
                    release_met = _eval_cond(release, ticker, ref_ticker, price, ref_price, buy_price,
                                             release_sub, release_logic)
                if release_met:
                    actions.append({"type": "unlock", "lock_id": lid})

    if locked and not any(a["type"] == "unlock" for a in actions):
        return actions

    for cond_def in strategy.get("conditions", []):
        cid = cond_def.get("id", "")
        req_phase = cond_def.get("required_phase", [0])
        one_time = cond_def.get("one_time", False)

        if current_phase not in req_phase:
            continue

        if one_time and triggered.get(cid, False):
            continue

        ticker = cond_def.get("ticker", strategy.get("ticker", ""))
        ref_ticker = cond_def.get("ref_ticker", strategy.get("ref_ticker", ticker))
        cond_str = cond_def.get("condition", "")
        price = prices.get(ticker, 0)
        ref_price = prices.get(ref_ticker, price)
        buy_price = phase_state.get("entry_price", {}).get(ticker, 0)

        sub_conds = cond_def.get("sub_conditions", [])
        cond_logic = cond_def.get("condition_logic", "AND") or "AND"
        cond_met = _eval_cond(cond_str, ticker, ref_ticker, price, ref_price, buy_price,
                              sub_conds, cond_logic)
        if not cond_met:
            continue

        action_def = cond_def.get("action", {})
        action = {
            **action_def,
            "ticker": ticker,
            "condition_id": cid,
            "condition_name": cond_def.get("name", cid),
            "next_phase": cond_def.get("next_phase"),
            "one_time": one_time,
            "reset_on_trigger": cond_def.get("reset_on_trigger", False),
            "reset_triggers": cond_def.get("reset_triggers"),
            "price": price,
        }
        actions.append(action)

        if cond_def.get("reset_on_trigger", False):
            return actions

    return actions


def apply_action_to_state(phase_state, action):
    state = {
        **phase_state,
        "triggered": dict(phase_state.get("triggered", {})),
        "initial_qty": dict(phase_state.get("initial_qty", {})),
        "entry_price": dict(phase_state.get("entry_price", {})),
        "lock_steps": dict(phase_state.get("lock_steps", {})),
    }

    atype = action.get("type")

    if atype == "lock":
        state["locked"] = True
        state["lock_reason"] = action.get("lock_id")
        # 잠금 시 해당 lock의 step 초기화
        state["lock_steps"].pop(action.get("lock_id", ""), None)
        return state

    if atype == "unlock":
        state["locked"] = False
        state["lock_reason"] = None
        state["lock_steps"].pop(action.get("lock_id", ""), None)
        return state

    if atype == "advance_lock_step":
        lid = action.get("lock_id")
        state["lock_steps"][lid] = action.get("step", 0)
        return state

    cid = action.get("condition_id")

    if action.get("one_time") and cid:
        state["triggered"][cid] = True

    next_phase = action.get("next_phase")
    if next_phase is not None:
        state["phase"] = next_phase

    # 특정 조건들의 1회성 트리거 해제 (예: GC 물타기 시 tp1/tp2/tp3 재무장)
    for rid in (action.get("reset_triggers") or []):
        state["triggered"].pop(rid, None)

    if action.get("reset_on_trigger"):
        state["triggered"] = {}
        state["initial_qty"] = {}
        state["entry_price"] = {}
        state["locked"] = False
        state["lock_reason"] = None

    return state


def record_buy(phase_state, ticker, qty, price):
    state = {
        **phase_state,
        "initial_qty": dict(phase_state.get("initial_qty", {})),
        "entry_price": dict(phase_state.get("entry_price", {})),
    }
    prev_qty = state["initial_qty"].get(ticker, 0)
    prev_price = state["entry_price"].get(ticker, 0)

    if prev_qty + qty > 0:
        state["entry_price"][ticker] = (
            (prev_price * prev_qty + price * qty) / (prev_qty + qty)
        )
    if prev_qty == 0:
        state["initial_qty"][ticker] = qty
    return state


def record_sell(phase_state, ticker, qty):
    return phase_state


def _eval_cond(condition, ticker, ref_ticker, price, ref_price,
               buy_price=0, sub_conditions=None, condition_logic="AND"):
    try:
        from condition_evaluator import evaluate, evaluate_compound
        kwargs = dict(
            ticker=ticker,
            ref_ticker=ref_ticker or None,
            buy_price=buy_price or None,
            current_price=price or None,
        )
        if sub_conditions:
            return evaluate_compound(sub_conditions, condition_logic, **kwargs)
        return evaluate(condition, **kwargs)
    except Exception as e:
        log.warning("condition eval error [%s]: %s", condition, e)
        return False


def get_phase_name(strategy, phase):
    phases = strategy.get("phases", {})
    return phases.get(str(phase), "Phase %d" % phase)
