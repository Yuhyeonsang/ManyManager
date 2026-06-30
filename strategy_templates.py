"""
strategy_templates.py — 전략 템플릿 저장/불러오기
==================================================
- 내장 템플릿: 눈덩이 TQQQ (하드코딩, 수정/삭제 불가)
- 사용자 템플릿: JSON 파일로 저장 (strategy_templates/ 디렉토리)
"""

import json
import os

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "strategy_templates")
os.makedirs(TEMPLATES_DIR, exist_ok=True)


# ═══════════════════════════════════════════════
# 눈덩이 TQQQ 내장 템플릿
# ═══════════════════════════════════════════════
SNOWBALL_TQQQ = {
    "name": "눈덩이 TQQQ",
    "description": "QQQ 낙폭 기반 TQQQ 분할 매수 + GC 풀매수 + 분할 익절 전략",
    "ticker": "TQQQ",
    "ref_ticker": "QQQ",
    # 고점 기준: QQQ 126일 장중 최고가 (Lookback Period)
    "lookback_days": 126,
    "lookback_price": "intraday_high",
    "initial_phase": 0,
    "phases": {
        "0": "대기 — 현금 100%, 진입 대기",
        "1": "Dip 1 진입 — 30% 보유",
        "2": "Dip 2 진입 — 70% 보유",
        "3": "GC 풀매수 — 100% 보유",
        "4": "TP1 익절 완료",
        "5": "TP2 익절 완료",
        "6": "TP3 전량 매도 — 재진입 잠금",
    },
    "conditions": [
        # ── 매수 ──────────────────────────────────
        {
            "id": "dip1",
            "name": "[Dip 1] TQQQ",
            "type": "buy",
            "ticker": "TQQQ",
            "ref_ticker": "QQQ",
            # 고점 = 126일 장중 최고가 기준
            "condition": "126일 고점 대비 -10% 이하",
            "action": {"type": "buy", "weight_pct": 30, "weight_mode": "target"},
            "required_phase": [0],
            "next_phase": 1,
            "one_time": True,
            "reset_on_trigger": False,
        },
        {
            "id": "dip2",
            "name": "[Dip 2] TQQQ",
            "type": "buy",
            "ticker": "TQQQ",
            "ref_ticker": "QQQ",
            # 고점 = 126일 장중 최고가 기준
            "condition": "126일 고점 대비 -22% 이하 & 200일선 대비 -7% 이하",
            # "&" 복합조건: sub_conditions로 분리해야 둘 다 AND 평가됨 (없으면 200일선 무시됨)
            "sub_conditions": ["126일 고점 대비 -22% 이하", "200일선 대비 -7% 이하"],
            "condition_logic": "AND",
            "action": {"type": "buy", "weight_pct": 70, "weight_mode": "target"},
            "required_phase": [1],
            "next_phase": 2,
            "one_time": True,
            "reset_on_trigger": False,
        },
        {
            "id": "rsi35",
            "name": "[RSI 35] TQQQ 추가매수",
            "type": "buy",
            "ticker": "TQQQ",
            "ref_ticker": "TQQQ",
            "condition": "RSI 35 이하",
            "action": {"type": "buy", "weight_pct": 10, "weight_mode": "add"},
            "required_phase": [1, 2],
            "next_phase": None,
            # "중복 적용 불가" = RSI 35 레벨은 1회만. RSI 25는 별도로 1회 가능
            "one_time": True,
            "reset_on_trigger": False,
        },
        {
            "id": "rsi25",
            "name": "[RSI 25] TQQQ 추가매수",
            "type": "buy",
            "ticker": "TQQQ",
            "ref_ticker": "TQQQ",
            "condition": "RSI 25 이하",
            "action": {"type": "buy", "weight_pct": 15, "weight_mode": "add"},
            "required_phase": [1, 2],
            "next_phase": None,
            # "중복 적용 불가" = RSI 25 레벨은 1회만. RSI 35와 독립 적용
            "one_time": True,
            "reset_on_trigger": False,
        },
        {
            "id": "gc",
            "name": "[GC 풀매수] TQQQ",
            "type": "buy",
            "ticker": "TQQQ",
            "ref_ticker": "TQQQ",
            "condition": "5일선 220일선 골든크로스",
            "action": {"type": "buy", "weight_pct": 100, "weight_mode": "target"},
            # Phase 0: 현금 100%여도 Dip1 거치지 않고 즉시 100% 풀매수 (target 100%)
            # Phase 1,2: Dip 진입 후 GC 발생 시 목표비중 100%로 채움
            # Phase 4,5: TP 익절 중 새 골든크로스 발생 시 100% 재매수(물타기) — 시나리오2/3
            #   ※ GC는 교차 당일(edge)에만 발동 → TP 직후 즉시 재매수 아님
            "required_phase": [0, 1, 2, 4, 5],
            "next_phase": 3,
            # GC 발동 시 TP1/2/3 재무장 → 물타기 후 다시 분할 익절 가능 (재익절 자동)
            "reset_triggers": ["tp1", "tp2", "tp3"],
            "one_time": False,
            "reset_on_trigger": False,
            "phase0_immediate_entry": True,
        },
        # ── 매도 ──────────────────────────────────
        {
            "id": "tp1",
            "name": "[TP1] +15% 분할 익절",
            "type": "sell",
            "ticker": "TQQQ",
            "ref_ticker": "TQQQ",
            "condition": "수익률 +15% 도달",
            # 최초 수량의 50% 매도
            "action": {"type": "sell", "sell_pct": 50, "sell_mode": "initial_qty"},
            "required_phase": [1, 2, 3],
            "next_phase": 4,
            "one_time": True,
            "reset_on_trigger": False,
        },
        {
            "id": "tp2",
            "name": "[TP2] +100% 분할 익절",
            "type": "sell",
            "ticker": "TQQQ",
            "ref_ticker": "TQQQ",
            "condition": "수익률 +100% 도달",
            # 다이어그램: "최초 수량의 35% 매도" = initial_qty(총 누적 보유량) 기준
            # TP1(50%)+TP2(35%)+TP3(나머지15%) = 최초 수량 100% 분할 청산
            "action": {"type": "sell", "sell_pct": 35, "sell_mode": "initial_qty"},
            "required_phase": [4],
            "next_phase": 5,
            "one_time": True,
            "reset_on_trigger": False,
        },
        {
            "id": "tp3",
            "name": "[TP3] +350% 전량 청산",
            "type": "sell",
            "ticker": "TQQQ",
            "ref_ticker": "TQQQ",
            "condition": "수익률 +350% 도달",
            "action": {"type": "sell", "sell_pct": 100, "sell_mode": "current_qty"},
            "required_phase": [5],
            "next_phase": 6,
            "one_time": True,
            "reset_on_trigger": False,
        },
        {
            "id": "dc",
            "name": "[DC 비상탈출] 전량 매도",
            "type": "sell",
            "ticker": "TQQQ",
            "ref_ticker": "TQQQ",
            "condition": "5일선 220일선 데드크로스",
            "action": {"type": "sell", "sell_pct": 100, "sell_mode": "current_qty"},
            # 이미지 명시: Phase 1~5에서만 DC 발동 (Phase 6은 이미 전량 매도 상태)
            "required_phase": [1, 2, 3, 4, 5],
            "next_phase": 0,
            "one_time": False,
            # Phase 0 리셋: rsi35/rsi25/tp1/tp2 의 1회 카운트 모두 초기화
            # 쿨다운 없음 — DC 후 조건 충족 즉시 재진입 가능 (cooldown_days=0)
            "reset_on_trigger": True,
            "cooldown_days": 0,
            "reset_flags": ["rsi35", "rsi25", "tp1", "tp2"],
        },
    ],
    "lock_conditions": [
        {
            "id": "extreme_drop",
            # QQQ 126일 고점 대비 -40% 이하 시 모든 매수 잠금
            "condition": "126일 고점 대비 -40% 이하",
            "ticker": "QQQ",
            "ref_ticker": "QQQ",
            "action": "lock_buy",
            # QQQ 낙폭이 -40% 위로 반등 시 즉시 해제 (예: -39%)
            "release_condition": "126일 고점 대비 -39% 이상",
            "required_phase": "any",
        },
        {
            "id": "tp3_lock",
            # TP3 전량 청산 후 재진입 잠금
            "condition": "phase == 6",
            "ticker": "TQQQ",
            "ref_ticker": "QQQ",
            "action": "lock_buy",
            # 해제 조건: 순서 조건 — ① Dip1(-10%) 먼저 발생 → ② 그 다음 GC 발생
            # release_condition은 표시용, 실제 평가는 release_steps가 담당
            "release_condition": "",
            "release_steps": [
                {"step": 1, "condition": "126일 고점 대비 -10% 이하", "ticker": "QQQ", "ref_ticker": "QQQ"},
                {"step": 2, "condition": "5일선 220일선 골든크로스", "ticker": "TQQQ", "ref_ticker": "TQQQ"},
            ],
            "required_phase": [6],
        },
    ],
}

# ── 내장 템플릿 목록 ─────────────────────────────
_BUILTIN: dict = {
    "눈덩이 TQQQ": SNOWBALL_TQQQ,
}


# ═══════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════

def list_templates() -> list:
    """내장 + 사용자 저장 템플릿 이름 목록."""
    names = list(_BUILTIN.keys())
    for f in sorted(os.listdir(TEMPLATES_DIR)):
        if f.endswith(".json"):
            names.append(f[:-5])
    return names


def get_template(name: str) -> dict:
    """템플릿 이름으로 전략 dict 반환."""
    if name in _BUILTIN:
        return _BUILTIN[name]
    path = os.path.join(TEMPLATES_DIR, f"{name}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    raise KeyError(f"템플릿 없음: {name}")


def save_template(name: str, strategy: dict) -> None:
    """사용자 템플릿 저장. 내장 템플릿 이름은 불가."""
    if name in _BUILTIN:
        raise ValueError(f"'{name}'은 내장 템플릿입니다. 다른 이름을 사용하세요.")
    strategy = dict(strategy)
    strategy["name"] = name
    path = os.path.join(TEMPLATES_DIR, f"{name}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(strategy, f, ensure_ascii=False, indent=2)


def delete_template(name: str) -> None:
    """사용자 템플릿 삭제. 내장 템플릿은 불가."""
    if name in _BUILTIN:
        raise ValueError(f"'{name}'은 내장 템플릿으로 삭제할 수 없습니다.")
    path = os.path.join(TEMPLATES_DIR, f"{name}.json")
    if not os.path.exists(path):
        raise KeyError(f"템플릿 없음: {name}")
    os.remove(path)


def is_builtin(name: str) -> bool:
    return name in _BUILTIN


def conditions_to_template(name: str, conditions: dict) -> dict:
    """
    기존 조건 JSON(AI 추출 포맷) → Phase 템플릿 포맷 변환.
    AI 추출 결과를 템플릿으로 저장할 때 사용.
    Phase 정보가 없으면 모든 조건을 Phase [0]에 배치.
    """
    template = {
        "name": name,
        "description": conditions.get("summary", "사용자 정의 전략"),
        "ticker": "",
        "ref_ticker": "",
        "initial_phase": 0,
        "phases": {"0": "실행 중"},
        "conditions": [],
        "lock_conditions": [],
    }

    for c in conditions.get("buy_conditions", []):
        template["conditions"].append({
            "id": f"buy_{c.get('name','').replace(' ', '_').lower() or len(template['conditions'])}",
            "name": c.get("name") or c.get("label", ""),
            "type": "buy",
            "ticker": c.get("ticker", ""),
            "ref_ticker": c.get("ref_ticker", ""),
            "condition": c.get("condition", ""),
            "action": {
                "type": "buy",
                "weight_pct": c.get("weight_pct", 10),
                "weight_mode": c.get("weight_mode", "add"),
            },
            "required_phase": [0],
            "next_phase": None,
            "one_time": False,
            "reset_on_trigger": False,
        })

    for c in conditions.get("sell_conditions", []):
        template["conditions"].append({
            "id": f"sell_{c.get('name','').replace(' ', '_').lower() or len(template['conditions'])}",
            "name": c.get("name") or c.get("label", ""),
            "type": "sell",
            "ticker": c.get("ticker", ""),
            "ref_ticker": c.get("ref_ticker", ""),
            "condition": c.get("condition", ""),
            "action": {
                "type": "sell",
                "sell_pct": c.get("sell_pct", 100),
                "sell_mode": c.get("sell_mode", "current_qty"),
            },
            "required_phase": [0],
            "next_phase": None,
            "one_time": False,
            "reset_on_trigger": False,
        })

    for c in conditions.get("lock_conditions", []):
        template["lock_conditions"].append({
            "id": f"lock_{len(template['lock_conditions'])}",
            "condition": c.get("condition", ""),
            "ticker": c.get("ticker", ""),
            "ref_ticker": c.get("ref_ticker", ""),
            "action": "lock_buy",
            "release_condition": "",
            "required_phase": "any",
        })

    return template
