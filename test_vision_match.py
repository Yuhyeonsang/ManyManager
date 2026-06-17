"""
test_vision_match.py
====================
눈덩이 TQQQ 전략 이미지 → Vision AI 추출 → SNOWBALL_TQQQ와 100% 비교

사용법:
    python test_vision_match.py <이미지경로>
    python test_vision_match.py snowball.jpg

출력: 각 조건별 일치 여부 + 최종 점수
"""

import sys
import json
import os

# 1) auto_trader의 analyze_image_conditions 임포트
sys.path.insert(0, os.path.dirname(__file__))
from auto_trader import analyze_image_conditions
from strategy_templates import SNOWBALL_TQQQ


# ─────────────────────────────────────────────────────────────────
# 비교 헬퍼
# ─────────────────────────────────────────────────────────────────

def normalize(s):
    """공백·부호 정규화 (비교용)"""
    if not isinstance(s, str):
        return str(s)
    return s.strip().replace(" ", "").replace("　", "").lower()


def score_field(label, expected, got, points=1):
    ok = normalize(str(expected)) == normalize(str(got))
    status = "✅" if ok else "❌"
    print(f"  {status} {label}: 기대={expected!r}  추출={got!r}")
    return points if ok else 0


# ─────────────────────────────────────────────────────────────────
# SNOWBALL_TQQQ → 기대값 dict로 변환
# ─────────────────────────────────────────────────────────────────

EXPECTED_BUYS = []
EXPECTED_SELLS = []
EXPECTED_LOCKS = []

for c in SNOWBALL_TQQQ["conditions"]:
    if c["type"] == "buy":
        EXPECTED_BUYS.append({
            "id": c["id"],
            "label": c.get("name", ""),
            "condition": c["condition"],
            "weight_pct": c["action"]["weight_pct"],
            "weight_mode": c["action"]["weight_mode"],
            "ticker": c["ticker"],
            "ref_ticker": c.get("ref_ticker", ""),
        })
    elif c["type"] == "sell":
        EXPECTED_SELLS.append({
            "id": c["id"],
            "label": c.get("name", ""),
            "condition": c["condition"],
            "sell_pct": c["action"]["sell_pct"],
            "sell_mode": c["action"]["sell_mode"],
            "ticker": c["ticker"],
        })

for lc in SNOWBALL_TQQQ["lock_conditions"]:
    EXPECTED_LOCKS.append({
        "id": lc["id"],
        "condition": lc["condition"],
        "release_condition": lc.get("release_condition", ""),
        "action": lc["action"],
    })


# ─────────────────────────────────────────────────────────────────
# 추출 결과 → 대응 찾기
# ─────────────────────────────────────────────────────────────────

def find_best_match(extracted_list, key_field, expected_val):
    """condition 문자열 유사도로 가장 가까운 항목 찾기"""
    best = None
    best_score = -1
    enorm = normalize(expected_val)
    for item in extracted_list:
        val = normalize(item.get(key_field, ""))
        # 공통 문자 비율
        common = sum(1 for c in enorm if c in val)
        score = common / max(len(enorm), 1)
        if score > best_score:
            best_score = score
            best = item
    return best, best_score


def compare(extracted):
    total = 0
    earned = 0

    print("\n" + "=" * 60)
    print("▶ 매수 조건 비교")
    print("=" * 60)
    for exp in EXPECTED_BUYS:
        print(f"\n[{exp['id']}] {exp['label']}")
        got, sim = find_best_match(extracted.get("buy_conditions", []), "condition", exp["condition"])
        if got is None:
            print(f"  ❌ 항목 없음 (기대: {exp['condition']!r})")
            total += 3; continue
        earned += score_field("condition",   exp["condition"],  got.get("condition", ""))
        earned += score_field("weight_pct",  exp["weight_pct"], got.get("weight_pct"))
        earned += score_field("weight_mode", exp["weight_mode"],got.get("weight_mode"))
        total += 3

    print("\n" + "=" * 60)
    print("▶ 매도 조건 비교")
    print("=" * 60)
    for exp in EXPECTED_SELLS:
        print(f"\n[{exp['id']}] {exp['label']}")
        got, sim = find_best_match(extracted.get("sell_conditions", []), "condition", exp["condition"])
        if got is None:
            print(f"  ❌ 항목 없음 (기대: {exp['condition']!r})")
            total += 3; continue
        earned += score_field("condition", exp["condition"], got.get("condition", ""))
        earned += score_field("sell_pct",  exp["sell_pct"],  got.get("sell_pct"))
        earned += score_field("sell_mode", exp["sell_mode"], got.get("sell_mode"))
        total += 3

    print("\n" + "=" * 60)
    print("▶ 락(Lock) 조건 비교")
    print("=" * 60)
    for exp in EXPECTED_LOCKS:
        print(f"\n[{exp['id']}]")
        got, sim = find_best_match(extracted.get("lock_conditions", []), "condition", exp["condition"])
        if got is None:
            print(f"  ❌ 항목 없음 (기대: {exp['condition']!r})")
            total += 3; continue
        earned += score_field("condition",         exp["condition"],         got.get("condition", ""))
        earned += score_field("release_condition", exp["release_condition"], got.get("release_condition", ""))
        earned += score_field("action",            exp["action"],            got.get("action", ""))
        total += 3

    pct = round(earned / total * 100) if total else 0
    print("\n" + "=" * 60)
    print(f"📊 최종 점수: {earned}/{total}점 = {pct}%")
    print("=" * 60)

    if pct < 100:
        print("\n[불일치 항목 요약]")
        # 위에서 이미 출력됨
    return pct, earned, total


# ─────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python test_vision_match.py <이미지경로>")
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"이미지 없음: {image_path}")
        sys.exit(1)

    print(f"이미지 분석 중: {image_path}")
    print("(Gemini Vision API 호출 중...)\n")

    # Vision AI 분석
    with open(image_path, "rb") as f:
        image_bytes = f.read()

    # analyze_image_conditions는 bytes를 받는 버전 사용
    result = analyze_image_conditions(image_bytes)

    if result.get("error"):
        print(f"❌ Vision AI 오류: {result['error']}")
        sys.exit(1)

    print("=== Vision AI 추출 결과 (raw JSON) ===")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 비교
    pct, earned, total = compare(result)

    if pct == 100:
        print("\n🎉 완전 일치! Vision AI가 눈덩이 TQQQ 조건을 100% 정확히 추출합니다.")
    else:
        print(f"\n⚠️  {100 - pct}점 차이. 위 ❌ 항목을 프롬프트에서 수정하세요.")
