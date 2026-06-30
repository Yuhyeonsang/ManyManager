"""
auto_trader.py — KIS 실전투자 API 기반 자동매매 엔진
======================================================
주요 기능:
  1) Gemini Vision으로 이미지에서 매수/매도 조건 추출
  2) KIS API 토큰 발급 및 갱신
  3) 조건 만족 시 자동 매수/매도 주문
  4) 백그라운드 루프 (5분 간격)

환경변수 (.env):
  KIS_APP_KEY        한국투자증권 앱키
  KIS_APP_SECRET     한국투자증권 앱시크릿
  KIS_ACCOUNT_NO     계좌번호 (예: 50100000-01)
  KIS_TRADE_MODE     real (기본값=실전) / paper (모의)
  GEMINI_API_KEY     이미지 분석용
"""

import os
import json
import time
import logging
import threading
import requests
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger("auto-trader")

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
KIS_APP_KEY    = os.getenv("KIS_APP_KEY", "")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET", "")
KIS_ACCOUNT_NO = os.getenv("KIS_ACCOUNT_NO", "")   # "50100000-01" 형식
KIS_TRADE_MODE = os.getenv("KIS_TRADE_MODE", "real")  # real / paper

KIS_BASE_REAL  = "https://openapi.koreainvestment.com:9443"
KIS_BASE_PAPER = "https://openapivts.koreainvestment.com:9443"

def _get_base_url(mode: str) -> str:
    return KIS_BASE_PAPER if mode == "paper" else KIS_BASE_REAL

# 계좌번호 파싱: "50100000-01" → cano="50100000", acnt_prdt_cd="01"
def _parse_account(account_no: str):
    parts = account_no.replace("-", "").strip()
    if len(parts) >= 10:
        return parts[:8], parts[8:10]
    return parts, "01"

# ─────────────────────────────────────────────
# 전역 상태
# ─────────────────────────────────────────────
_state = {
    "running": False,
    "conditions": None,        # 구 포맷 (AI 추출) — 하위 호환
    "strategy": None,          # Phase 템플릿 포맷
    "phase_state": None,       # Phase 엔진 상태
    "conditions_image": None,
    "token": None,
    "token_expires": None,
    "trade_log": [],
    "last_check": None,
    "error": None,
    "thread": None,
    "trade_mode": KIS_TRADE_MODE,
}
_lock = threading.Lock()

# ─────────────────────────────────────────────
# KIS 토큰
# ─────────────────────────────────────────────
def _get_kis_token() -> str:
    """유효한 KIS 액세스 토큰 반환 (만료 시 재발급)."""
    with _lock:
        now = datetime.now()
        if _state["token"] and _state["token_expires"] and now < _state["token_expires"]:
            return _state["token"]

        if not KIS_APP_KEY or not KIS_APP_SECRET:
            raise ValueError("KIS_APP_KEY / KIS_APP_SECRET 환경변수를 설정하세요.")

        url = f"{_get_base_url(_state['trade_mode'])}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": KIS_APP_KEY,
            "appsecret": KIS_APP_SECRET,
        }
        resp = requests.post(url, json=body, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"KIS 토큰 발급 실패: {data}")

        _state["token"] = token
        _state["token_expires"] = now + timedelta(hours=23)
        log.info("KIS 토큰 발급 완료")
        return token


def _kis_headers(tr_id: str, mode: str = None) -> dict:
    token = _get_kis_token()
    cano, acnt_prdt_cd = _parse_account(KIS_ACCOUNT_NO)
    return {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",
        "Content-Type": "application/json; charset=utf-8",
    }

# ─────────────────────────────────────────────
# 현재가 조회
# ─────────────────────────────────────────────
def get_account_balance() -> dict:
    """KIS 계좌 잔고 조회 (미구현 스텁). 빈 dict 반환 → 예산(AUTO_TRADE_BUDGET) 또는 매수 보류."""
    return {}


def get_holdings() -> list:
    """KIS 보유종목 조회 (국내+미국). 반환 [{ticker,qty,eval_amount}].
    ※ 응답 필드명은 모의(paper)로 1회 검증 권장 — 미검증 시 매도수량 오류 가능."""
    out = []
    mode = _state.get("trade_mode", KIS_TRADE_MODE)
    base = _get_base_url(mode)
    real = (mode != "paper")
    cano, prdt = _parse_account(KIS_ACCOUNT_NO)
    # ── 미국(해외) 잔고 ──
    try:
        tr = "TTTS3012R" if real else "VTTS3012R"
        url = f"{base}/uapi/overseas-stock/v1/trading/inquire-balance"
        params = {"CANO": cano, "ACNT_PRDT_CD": prdt,
                  "OVRS_EXCG_CD": os.getenv("KIS_US_EXCHANGE", "NASD"),
                  "TR_CRCY_CD": "USD", "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""}
        r = requests.get(url, headers=_kis_headers(tr), params=params, timeout=10)
        r.raise_for_status()
        for h in (r.json().get("output1") or []):
            qty = int(float(h.get("ovrs_cblc_qty", 0) or 0))
            if qty > 0:
                out.append({"ticker": h.get("ovrs_pdno", ""), "qty": qty,
                            "eval_amount": float(h.get("ovrs_stck_evlu_amt", 0) or 0)})
    except Exception as e:
        log.warning(f"해외 잔고조회 실패: {e}")
    # ── 국내 잔고 ──
    try:
        tr = "TTTC8434R" if real else "VTTC8434R"
        url = f"{base}/uapi/domestic-stock/v1/trading/inquire-balance"
        params = {"CANO": cano, "ACNT_PRDT_CD": prdt, "AFHR_FLPR_YN": "N",
                  "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
                  "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
                  "PRCS_DVSN": "01", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
        r = requests.get(url, headers=_kis_headers(tr), params=params, timeout=10)
        r.raise_for_status()
        for h in (r.json().get("output1") or []):
            qty = int(float(h.get("hldg_qty", 0) or 0))
            if qty > 0:
                out.append({"ticker": h.get("pdno", ""), "qty": qty,
                            "eval_amount": float(h.get("evlu_amt", 0) or 0)})
    except Exception as e:
        log.warning(f"국내 잔고조회 실패: {e}")
    return out


def _is_us_ticker(ticker: str) -> bool:
    """국내=6자리 숫자코드, 그 외(TQQQ 등 알파벳)=미국. 라우팅용."""
    code = str(ticker).split(".")[0].strip().upper()
    return not code.isdigit()


# 미국 거래소: 현재가(EXCD)와 주문(OVRS_EXCG_CD) 코드가 다름
_US_PRICE_EXCD = {"NASD": "NAS", "NAS": "NAS", "NYSE": "NYS", "NYS": "NYS", "AMEX": "AMS", "AMS": "AMS"}

def _us_exchange(symbol: str) -> str:
    """주문용 거래소코드. 기본 NASDAQ (TQQQ/QQQ 등). 필요시 .env KIS_US_EXCHANGE로 변경."""
    return os.getenv("KIS_US_EXCHANGE", "NASD")


def get_overseas_price(symbol: str) -> Optional[float]:
    """KIS 해외주식 현재가(HHDFS00000300). 실패 시 None."""
    try:
        sym = symbol.split(".")[0].strip().upper()
        _mode = _state.get("trade_mode", KIS_TRADE_MODE)
        excd = _US_PRICE_EXCD.get(_us_exchange(sym), "NAS")
        url = f"{_get_base_url(_mode)}/uapi/overseas-price/v1/quotations/price"
        headers = _kis_headers("HHDFS00000300")
        params = {"AUTH": "", "EXCD": excd, "SYMB": sym}
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        out = resp.json().get("output", {}) or {}
        raw = str(out.get("last") or out.get("ovrs_prpr") or "0").replace(",", "").strip()
        return float(raw) if raw and float(raw) > 0 else None
    except Exception as e:
        log.warning(f"해외 현재가 조회 실패 ({symbol}): {e}")
        return None


def place_overseas_order(symbol: str, order_type: str, qty: int, price: float = 0) -> dict:
    """KIS 해외주식(미국) 주문. 미국은 지정가만 지원 → price<=0이면 현재가로 지정가 제출."""
    sym = symbol.split(".")[0].strip().upper()
    cano, acnt_prdt_cd = _parse_account(KIS_ACCOUNT_NO)
    _mode = _state.get("trade_mode", KIS_TRADE_MODE)
    real = (_mode != "paper")
    if order_type == "buy":
        tr_id = "TTTT1002U" if real else "VTTT1002U"   # 미국 매수
    else:
        tr_id = "TTTT1006U" if real else "VTTT1001U"   # 미국 매도
    unpr = float(price) if price and float(price) > 0 else (get_overseas_price(sym) or 0)
    url = f"{_get_base_url(_mode)}/uapi/overseas-stock/v1/trading/order"
    headers = _kis_headers(tr_id)
    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "OVRS_EXCG_CD": _us_exchange(sym),     # NASD/NYSE/AMEX
        "PDNO": sym,
        "ORD_QTY": str(int(qty)),
        "OVRS_ORD_UNPR": f"{unpr:.2f}",
        "ORD_SVR_DVSN_CD": "0",
        "ORD_DVSN": "00",                      # 지정가
    }
    resp = requests.post(url, headers=headers, json=body, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if str(result.get("rt_cd", "0")) != "0":
        raise RuntimeError(f"해외주문 거부: [{result.get('msg_cd','')}] {result.get('msg1','') or result}")
    return result


def get_current_price(ticker: str) -> Optional[float]:
    """현재가 조회. 미국=해외(overseas), 국내=domestic. 실패 시 None."""
    if _is_us_ticker(ticker):
        return get_overseas_price(ticker)
    try:
        # 종목 코드 정리 (005930.KS → 005930)
        code = ticker.split(".")[0] if "." in ticker else ticker
        _mode = _state.get("trade_mode", KIS_TRADE_MODE)
        url = f"{_get_base_url(_mode)}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = _kis_headers("FHKST01010100")
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code,
        }
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        output = data.get("output", {})
        price_str = output.get("stck_prpr", "0")
        return float(price_str) if price_str else None
    except Exception as e:
        log.warning(f"현재가 조회 실패 ({ticker}): {e}")
        return None

# ─────────────────────────────────────────────
# 주문 실행
# ─────────────────────────────────────────────
def place_order(ticker: str, order_type: str, qty: int, price: int = 0) -> dict:
    """
    order_type: "buy" | "sell"
    price: 0이면 국내 시장가 (미국은 지정가, 0이면 현재가로 제출)
    """
    if _is_us_ticker(ticker):
        return place_overseas_order(ticker, order_type, qty, price)
    code = ticker.split(".")[0] if "." in ticker else ticker
    cano, acnt_prdt_cd = _parse_account(KIS_ACCOUNT_NO)

    # 실전/모의 TR_ID
    _mode = _state.get("trade_mode", KIS_TRADE_MODE)
    if order_type == "buy":
        tr_id = "TTTC0802U" if _mode == "real" else "VTTC0802U"
    else:
        tr_id = "TTTC0801U" if _mode == "real" else "VTTC0801U"

    _mode = _state.get("trade_mode", KIS_TRADE_MODE)
    url = f"{_get_base_url(_mode)}/uapi/domestic-stock/v1/trading/order-cash"
    headers = _kis_headers(tr_id)

    # 시장가: ord_dvsn=01, 지정가: ord_dvsn=00
    ord_dvsn = "01" if price == 0 else "00"
    ord_unpr = "0" if price == 0 else str(price)

    body = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "PDNO": code,
        "ORD_DVSN": ord_dvsn,
        "ORD_QTY": str(qty),
        "ORD_UNPR": ord_unpr,
    }

    resp = requests.post(url, headers=headers, json=body, timeout=10)
    resp.raise_for_status()
    result = resp.json()
    if str(result.get("rt_cd", "0")) != "0":
        raise RuntimeError(f"주문 거부: [{result.get('msg_cd','')}] {result.get('msg1','') or result}")
    return result

# ─────────────────────────────────────────────
# Vision 이미지 분석 — Groq 우선, 실패 시 Gemini fallback
# ─────────────────────────────────────────────
def _parse_vision_text(text: str) -> dict:
    """LLM 응답 텍스트에서 JSON 추출 (Groq/Gemini 공통)."""
    import re
    text = text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
        if m:
            text = m.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end+1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "summary": text[:200] if text else "조건 추출 실패",
            "buy_conditions": [],
            "sell_conditions": [],
            "check_interval_minutes": 5,
        }


def _analyze_with_groq(image_base64: str, mime_type: str, prompt: str) -> dict:
    """Groq vision 모델로 이미지 분석. 실패 시 예외 발생."""
    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key:
        raise ValueError("GROQ_API_KEY 없음")
    model = os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    url = "https://api.groq.com/openai/v1/chat/completions"
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}}
            ]
        }],
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    text = resp.json()["choices"][0]["message"]["content"]
    return _parse_vision_text(text)


def _analyze_with_gemini(image_base64: str, mime_type: str, prompt: str) -> dict:
    """Gemini Vision으로 이미지 분석. 실패 시 예외 발생."""
    gemini_key = os.getenv("GEMINI_VISION_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY 없음")
    model = os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": image_base64}}
            ]
        }],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}
    }
    resp = requests.post(url, params={"key": gemini_key}, json=body, timeout=30)
    resp.raise_for_status()
    text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    return _parse_vision_text(text)


def analyze_image_conditions(image_base64: str, mime_type: str = "image/jpeg") -> dict:
    """
    이미지에서 매수/매도 조건 추출.
    Groq vision 우선 → 실패 시 Gemini fallback.

    반환 형태 (포트폴리오 비중 기반 전략 지원):
    {
      "summary": "조건 전체 요약 텍스트",
      "strategy_type": "weight_based" | "qty_based",
      "buy_conditions": [
        {
          "ticker": "TQQQ",
          "name": "ProShares UltraPro QQQ",
          "condition": "QQQ 고점 대비 -10% 이하",
          "ref_ticker": "QQQ",           # 조건 판단에 쓰는 참조 종목 (없으면 null)
          "weight_pct": 30,              # 목표 포트폴리오 비중 %
          "weight_mode": "target",       # "target"=목표비중, "add"=현재에 추가
          "condition_logic": "AND",      # 복합조건일 때 AND/OR
          "sub_conditions": [            # 복합조건이면 분리해서 나열
            "QQQ 고점 대비 -10% 이하"
          ],
          "label": "Dip1"               # 조건 이름/라벨 (있으면)
        }
      ],
      "sell_conditions": [
        {
          "ticker": "TQQQ",
          "name": "ProShares UltraPro QQQ",
          "condition": "수익률 +15% 달성",
          "sell_pct": 50,                # 보유 수량 중 매도 비율 % (100=전량)
          "sell_mode": "initial_qty",    # "initial_qty"=최초매수 기준, "current"=현재보유 기준
          "label": "TP1"
        }
      ],
      "lock_conditions": [              # 신규매수 금지 조건 (락)
        {
          "condition": "QQQ 고점 대비 -40% 이하",
          "action": "lock_buy"           # "lock_buy"=매수잠금, "liquidate"=전량청산+잠금
        }
      ],
      "check_interval_minutes": 5
    }
    """
    prompt = """이 이미지는 주식/ETF 자동매매 전략 조건표입니다.
이미지에 적힌 모든 조건을 빠짐없이 정확하게 읽고 아래 JSON 형식으로 추출하세요.
반드시 JSON만 출력하고 다른 텍스트는 절대 쓰지 마세요.

=== 핵심 규칙 ===
1. 하나의 카드/박스에 여러 조건이 섞여 있으면 각각 별도 항목으로 분리하세요.
   예) "TP3 +350% 전량매도 & 데드크로스 전량매도" → sell_conditions 2개로 분리
2. 조건이 수량(주)이 아니라 포트폴리오 비중(%)이면 weight_pct 필드 사용.
   예) "TQQQ 30% 비중으로 매수" → weight_pct: 30
3. 참조 지수(예: QQQ 고점 대비 -X%)로 다른 종목(예: TQQQ) 매수 조건을 판단하면
   ref_ticker에 QQQ 기입, ticker에 실제 매수할 종목(TQQQ) 기입.
4. 매수/매도/락 조건을 절대 섞지 마세요. 매수는 buy_conditions, 매도는 sell_conditions,
   신규매수 금지/강제청산은 lock_conditions에만 넣으세요.
5. 복합 AND 조건(예: "고점 대비 -22% AND 200일선 대비 -7% 이하")은
   sub_conditions 배열에 각 조건을 따로 나열하고 condition_logic: "AND" 설정.
6. 매수 조건의 weight_mode 결정:
   - "목표비중 X%", "비중 X%로 매수", "X% 비중 유지" → weight_mode: "target" (목표 비중까지 채움)
   - "X% 추가 매수", "비중 +X% 추가", "RSI 과매도 시 추가" → weight_mode: "add" (기존에 추가로 더 삼)
   - 골든크로스(GC) "100% 전액 투입"도 반드시 weight_mode: "target" (전액=목표비중 100%)
   Dip1/Dip2/GC 같은 '목표비중' 조건은 반드시 "target"으로 설정하세요.
7. 이미지에 있는 수치(%, 일수, 배수)를 절대 바꾸지 마세요.
8. 이동평균선은 반드시 몇 일선인지 명시하세요.
   예) "5일선이 220일선 상향 돌파" → condition: "5일선 220일선 골든크로스"
   예) "5일선이 220일선 하향 돌파" → condition: "5일선 220일선 데드크로스"

=== TP(익절) 조건 파싱 규칙 (매우 중요) ===
TP 조건에는 두 가지 숫자가 있습니다. 절대 혼동하지 마세요:
  A) 수익률 기준 (condition에 기입): "수익률 +X% 도달 시" 의 X
  B) 매도 비율 (sell_pct에 기입): "보유수량의 Y% 매도" 의 Y

예시:
  "TP1: 수익률 +15% 달성 시, 최초수량의 50% 매도"
  → condition: "수익률 +15% 도달", sell_pct: 50, sell_mode: "initial_qty"

  "TP2: 수익률 +100% 달성 시, 현재보유의 35% 매도" (TP2 최소수량 기준)
  → condition: "수익률 +100% 도달", sell_pct: 35, sell_mode: "current_qty"
  ※ sell_mode: "current_qty" = TP1 이후 남은 현재 보유량 기준
  ※ 수익률 100%와 매도비율 35%를 절대 혼동하지 마세요!

  "TP3: 수익률 +350% 달성 시, 남은 전량 매도"
  → condition: "수익률 +350% 도달", sell_pct: 100, sell_mode: "current_qty"

  "DC 비상탈출: 5일선 220일선 데드크로스 시, 전량 매도"
  → condition: "5일선 220일선 데드크로스", sell_pct: 100, sell_mode: "current_qty"

=== 골든크로스(GC) 풀매수 규칙 ===
"TQQQ 5일선이 220일선 상향 돌파 시 잔여 현금 100% 전액 투입"은 buy_conditions에 추가:
  → condition: "5일선 220일선 골든크로스", weight_pct: 100, weight_mode: "target", label: "GC 풀매수"
  ※ weight_mode: "target" (100% 목표비중까지 채움)

=== RSI 과매도 비중 추가 규칙 ===
"RSI 35 이하 시 +10% 비중 추가", "RSI 25 이하 시 +15% 비중 추가"는 각각 별도 항목:
  → condition: "RSI 35 이하", weight_pct: 10, weight_mode: "add"
  → condition: "RSI 25 이하", weight_pct: 15, weight_mode: "add"

=== 락(Lock) 조건 파싱 규칙 ===
락 조건에는 발동 조건과 해제 조건이 모두 있습니다. 반드시 둘 다 기입하세요.
  예) "QQQ 고점 대비 -40% 이하 시 모든 매수 잠금, -39% 이상 회복 시 즉시 해제"
  → condition: "126일 고점 대비 -40% 이하", release_condition: "126일 고점 대비 -39% 이상", action: "lock_buy"

  예) "TP3 전량매도 후 재진입 잠금, Dip1(-10%) 발생 후 GC 발생 시 해제"
  → condition: "phase == 6", release_condition: "Dip1(-10%) 발생 후 GC 발생 (순서 조건)", action: "lock_buy"

=== JSON 스키마 ===
{
  "summary": "전략 전체 요약 (한국어 2~3문장)",
  "strategy_type": "weight_based",
  "buy_conditions": [
    {
      "ticker": "종목코드(예: TQQQ, AAPL, 005930)",
      "name": "종목명",
      "condition": "조건 전체 설명 (이미지 그대로)",
      "ref_ticker": "참조종목코드 또는 null",
      "weight_pct": 목표비중숫자(없으면 null),
      "weight_mode": "target 또는 add",
      "condition_logic": "AND 또는 OR 또는 null",
      "sub_conditions": ["조건1", "조건2"],
      "label": "조건 라벨명 또는 null"
    }
  ],
  "sell_conditions": [
    {
      "ticker": "종목코드",
      "name": "종목명",
      "condition": "조건 전체 설명 (이미지 그대로)",
      "sell_pct": 매도비율숫자(0~100),
      "sell_mode": "current_qty 또는 initial_qty",
      "label": "TP1 등 라벨 또는 null"
    }
  ],
  "lock_conditions": [
    {
      "condition": "락 발동 조건 설명",
      "release_condition": "락 해제 조건 설명 (반드시 기입)",
      "action": "lock_buy 또는 liquidate"
    }
  ],
  "check_interval_minutes": 조건체크주기(기본5)
}

종목코드가 이미지에 없으면 종목명으로 유추하세요. 비중/수량 정보가 전혀 없으면 null."""

    # 1순위: Gemini (이미지 분석 정확도 우선)
    try:
        result = _analyze_with_gemini(image_base64, mime_type, prompt)
        result["_provider"] = "gemini"
        return result
    except Exception as gemini_err:
        import logging
        logging.getLogger("auto_trader").warning(f"Gemini vision 실패 → Groq fallback: {gemini_err}")

    # 2순위: Groq fallback
    result = _analyze_with_groq(image_base64, mime_type, prompt)
    result["_provider"] = "groq"
    return result



def analyze_text_conditions(text: str) -> dict:
    """
    텍스트(복붙)에서 매수/매도 조건 추출.
    이미지 분석과 동일한 JSON 스키마 반환.
    """
    prompt = """아래 텍스트는 주식/ETF 자동매매 전략 조건을 서술한 글입니다.
텍스트에 적힌 모든 조건을 빠짐없이 정확하게 읽고 아래 JSON 형식으로 추출하세요.
반드시 JSON만 출력하고 다른 텍스트는 절대 쓰지 마세요.

=== 핵심 규칙 ===
1. 하나의 항목에 여러 조건이 섞여 있으면 각각 별도 항목으로 분리하세요.
2. 조건이 수량(주)이 아니라 포트폴리오 비중(%)이면 weight_pct 필드 사용.
3. 참조 지수(예: QQQ 고점 대비 -X%)로 다른 종목(예: TQQQ) 매수 조건을 판단하면
   ref_ticker에 QQQ 기입, ticker에 실제 매수할 종목(TQQQ) 기입.
4. 매수/매도/락 조건을 절대 섞지 마세요.
5. 복합 AND 조건은 sub_conditions 배열에 나열하고 condition_logic: "AND" 설정.
6. weight_mode: "target" = 목표비중까지 채움, "add" = 현재에 추가로 더 삼.

=== TP(익절) 조건 파싱 규칙 ===
TP 조건: 수익률 기준(condition)과 매도 비율(sell_pct)을 혼동하지 마세요.
예) "TP1: +15% 달성 시, 최초수량의 50% 매도"
  → condition: "TP1 +15% 도달 시", sell_pct: 50, sell_mode: "initial_qty"

=== JSON 스키마 ===
{
  "summary": "전략 전체 요약 (한국어 2~3문장)",
  "strategy_type": "weight_based",
  "buy_conditions": [
    {
      "ticker": "종목코드(예: TQQQ, AAPL, 005930)",
      "name": "종목명",
      "condition": "조건 전체 설명",
      "ref_ticker": "참조종목코드 또는 null",
      "weight_pct": 목표비중숫자(없으면 null),
      "weight_mode": "target 또는 add",
      "condition_logic": "AND 또는 OR 또는 null",
      "sub_conditions": ["조건1", "조건2"],
      "label": "조건 라벨명 또는 null"
    }
  ],
  "sell_conditions": [
    {
      "ticker": "종목코드",
      "name": "종목명",
      "condition": "조건 전체 설명",
      "sell_pct": 매도비율숫자(0~100),
      "sell_mode": "current 또는 initial_qty",
      "label": "TP1 등 라벨 또는 null"
    }
  ],
  "lock_conditions": [
    {
      "condition": "락 발동 조건 설명",
      "action": "lock_buy 또는 liquidate"
    }
  ],
  "check_interval_minutes": 조건체크주기(기본5)
}

종목코드가 없으면 종목명으로 유추하세요. 비중/수량 정보가 없으면 null.

=== 분석할 텍스트 ===
""" + text

    # Gemini 텍스트 모드 우선
    try:
        gemini_key = os.getenv("GEMINI_VISION_API_KEY") or os.getenv("GEMINI_API_KEY", "")
        if not gemini_key:
            raise ValueError("GEMINI_API_KEY 없음")
        model = os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}
        }
        resp = requests.post(url, params={"key": gemini_key}, json=body, timeout=30)
        resp.raise_for_status()
        text_out = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        result = _parse_vision_text(text_out)
        result["_provider"] = "gemini-text"
        return result
    except Exception as e:
        import logging
        logging.getLogger("auto_trader").warning(f"Gemini 텍스트 분석 실패 → Groq fallback: {e}")

    # Groq fallback (텍스트 전용)
    groq_key = os.getenv("GROQ_API_KEY", "")
    if not groq_key:
        raise ValueError("GROQ_API_KEY 없음 — 텍스트 분석 불가")
    model = os.getenv("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
    url = "https://api.groq.com/openai/v1/chat/completions"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 1024,
    }
    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )
    resp.raise_for_status()
    text_out = resp.json()["choices"][0]["message"]["content"]
    result = _parse_vision_text(text_out)
    result["_provider"] = "groq-text"
    return result


# ─────────────────────────────────────────────
# 조건 평가 엔진 (condition_evaluator 사용)
# ─────────────────────────────────────────────
try:
    import condition_evaluator as _ce
    _CE_AVAILABLE = True
except ImportError:
    _CE_AVAILABLE = False
    log.warning("condition_evaluator 모듈 없음 — 가격 조건만 지원")


def _evaluate_condition_item(cond: dict, price: float, buy_price: Optional[float] = None) -> bool:
    """
    조건 딕셔너리 하나를 평가.
    sub_conditions(AND/OR 복합) 지원.
    """
    ticker = cond.get("ticker", "")
    ref_ticker = cond.get("ref_ticker") or None
    condition_str = cond.get("condition", "")
    sub_conditions = cond.get("sub_conditions", [])
    logic = cond.get("condition_logic", "AND") or "AND"

    kwargs = dict(
        ticker=ticker,
        ref_ticker=ref_ticker,
        buy_price=buy_price,
        current_price=price,
    )

    if not _CE_AVAILABLE:
        # 폴백: 가격 비교만
        cond_lower = condition_str.strip().lower()
        if cond_lower in ("항상", "always", "즉시"):
            return True
        log.warning(f"[폴백] 미지원 조건: '{condition_str}'")
        return False

    # 복합 조건 (sub_conditions)
    if sub_conditions:
        return _ce.evaluate_compound(sub_conditions, logic, **kwargs)

    # 단일 조건
    return _ce.evaluate(condition_str, **kwargs)


# ─────────────────────────────────────────────
# 포지션 추적 (수익률 조건 / sell_mode 지원용)
# ─────────────────────────────────────────────
# { ticker: {"buy_price": float, "initial_qty": int, "current_qty": int} }
_positions: dict = {}


def _record_buy(ticker: str, price: float, qty: int):
    if ticker not in _positions:
        _positions[ticker] = {"buy_price": price, "initial_qty": qty, "current_qty": qty}
    else:
        pos = _positions[ticker]
        total_cost = pos["buy_price"] * pos["current_qty"] + price * qty
        pos["current_qty"] += qty
        pos["buy_price"] = total_cost / pos["current_qty"]
        pos["initial_qty"] = pos["current_qty"]  # 리셋: 분할매수 반영


def _calc_sell_qty(cond: dict, ticker: str) -> int:
    """sell_pct + sell_mode 기반 매도 수량 계산."""
    sell_pct = cond.get("sell_pct", 100)
    sell_mode = cond.get("sell_mode", "current")
    pos = _positions.get(ticker, {})

    if sell_mode == "initial_qty":
        base_qty = pos.get("initial_qty", 1)
    else:
        base_qty = pos.get("current_qty", 1)

    qty = max(1, int(base_qty * sell_pct / 100))
    return qty


def _record_sell(ticker: str, qty: int):
    if ticker in _positions:
        _positions[ticker]["current_qty"] = max(0, _positions[ticker]["current_qty"] - qty)
        if _positions[ticker]["current_qty"] == 0:
            del _positions[ticker]


def _trading_loop():
    """백그라운드 스레드에서 실행되는 자동매매 루프."""
    log.info("자동매매 루프 시작")

    while True:
        with _lock:
            if not _state["running"]:
                break
            strategy = _state.get("strategy")
            conditions = _state.get("conditions")

        has_work = strategy or conditions
        if not has_work:
            time.sleep(30)
            continue

        interval_sec = 5 * 60  # 기본 5분
        if conditions:
            interval_sec = conditions.get("check_interval_minutes", 5) * 60

        try:
            if strategy:
                _check_and_trade_phase(strategy)
            else:
                _check_and_trade_legacy(conditions)
        except Exception as e:
            log.error(f"매매 루프 오류: {e}")
            with _lock:
                _state["error"] = str(e)

        end_time = time.time() + interval_sec
        while time.time() < end_time:
            with _lock:
                if not _state["running"]:
                    break
            time.sleep(5)

    log.info("자동매매 루프 종료")


def _check_and_trade_phase(strategy: dict):
    """Phase 엔진 기반 한 사이클: 상태 체크 → 주문 → Phase 전환."""
    from phase_engine import (
        evaluate_strategy, apply_action_to_state,
        record_buy as pe_record_buy, get_phase_name,
    )

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        _state["last_check"] = now_str
        phase_state = dict(_state.get("phase_state") or {})
        if not phase_state:
            phase_state = {"phase": strategy.get("initial_phase", 0),
                           "triggered": {}, "locked": False, "lock_reason": None,
                           "initial_qty": {}, "entry_price": {}}

    # ── 가격 수집 ────────────────────────────
    tickers_needed = set()
    for c in strategy.get("conditions", []):
        tickers_needed.add(c.get("ticker", ""))
        tickers_needed.add(c.get("ref_ticker", ""))
    for lc in strategy.get("lock_conditions", []):
        tickers_needed.add(lc.get("ticker", ""))
        tickers_needed.add(lc.get("ref_ticker", ""))
    tickers_needed.discard("")

    prices = {}
    for tk in tickers_needed:
        p = get_current_price(tk)
        if p:
            prices[tk] = p

    if not prices:
        log.warning("가격 조회 실패 — 이번 사이클 스킵")
        return

    # ── 포트폴리오 가치 (실제 잔고 → 사용자 예산 → 매수 보류) ──
    try:
        bal = get_account_balance()
        portfolio_value = float(bal.get("total_eval_amount", 0) or 0)
    except Exception:
        portfolio_value = 0.0
    if portfolio_value <= 0:
        # 잔고 자동조회 미구현/실패 시: .env AUTO_TRADE_BUDGET(매매통화 기준) 사용. 없으면 0 → 매수 보류
        portfolio_value = float(os.getenv("AUTO_TRADE_BUDGET", "0") or 0)
    if portfolio_value <= 0:
        log.warning("잔고/예산 미설정 → 이번 사이클 매수 보류 (AUTO_TRADE_BUDGET 설정 또는 잔고연동 필요)")

    # ── Phase 엔진 평가 ──────────────────────
    actions = evaluate_strategy(strategy, phase_state, prices, portfolio_value)

    for action in actions:
        atype = action.get("type")
        ticker = action.get("ticker", "")
        now_str2 = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        if atype == "lock":
            phase_state = apply_action_to_state(phase_state, action)
            log.info(f"🔒 락 발동: {action.get('reason')}")
            with _lock:
                _state["phase_state"] = phase_state
                _state["trade_log"].insert(0, {
                    "time": now_str2, "action": "락 발동", "ticker": ticker,
                    "name": "락", "price": 0, "qty": 0,
                    "condition": action.get("reason", ""), "result": "매수 잠금",
                })
            continue

        if atype == "unlock":
            phase_state = apply_action_to_state(phase_state, action)
            log.info(f"🔓 락 해제")
            with _lock:
                _state["phase_state"] = phase_state
                _state["trade_log"].insert(0, {
                    "time": now_str2, "action": "락 해제", "ticker": ticker,
                    "name": "락 해제", "price": 0, "qty": 0,
                    "condition": "", "result": "매수 잠금 해제",
                })
            continue

        price = prices.get(ticker, 0)
        if price <= 0:
            continue

        if atype == "buy":
            weight_pct = action.get("weight_pct", 0)
            weight_mode = action.get("weight_mode", "add")

            if weight_mode == "target":
                # 목표비중까지 채우기: 목표금액 - 현재보유금액
                try:
                    holdings = get_holdings()
                    current_val = sum(
                        float(h.get("eval_amount", 0))
                        for h in holdings
                        if h.get("ticker") == ticker
                    )
                except Exception:
                    current_val = 0
                target_amount = portfolio_value * weight_pct / 100
                buy_amount = max(0, target_amount - current_val)
            else:
                buy_amount = portfolio_value * weight_pct / 100

            qty = int(buy_amount / price)
            if qty < 1:
                log.info(f"매수금액 부족(예산 {portfolio_value:.2f}, {ticker}) → 매수 스킵")
                continue
            try:
                result = place_order(ticker, "buy", qty, 0)
                # Phase 상태 업데이트
                phase_state = apply_action_to_state(phase_state, action)
                phase_state = pe_record_buy(phase_state, ticker, qty, price)
                with _lock:
                    _state["phase_state"] = phase_state
                    _state["trade_log"].insert(0, {
                        "time": now_str2, "action": "매수",
                        "ticker": ticker, "name": action.get("condition_name", ticker),
                        "price": price, "qty": qty,
                        "condition": action.get("condition_name", ""),
                        "phase": get_phase_name(strategy, phase_state["phase"]),
                        "result": result.get("msg1", "완료"),
                    })
                    _state["trade_log"] = _state["trade_log"][:100]
                log.info(f"✅ 매수: {ticker} {qty}주 @{price} | Phase→{phase_state['phase']}")
            except Exception as e:
                log.error(f"매수 실패 ({ticker}): {e}")
                with _lock:
                    _state["trade_log"].insert(0, {
                        "time": now_str2, "action": "매수실패",
                        "ticker": ticker, "name": action.get("condition_name", ticker),
                        "price": price, "qty": qty,
                        "condition": action.get("condition_name", ""),
                        "result": str(e),
                    })

        elif atype == "sell":
            sell_pct = action.get("sell_pct", 100)
            sell_mode = action.get("sell_mode", "current_qty")
            initial_qty = phase_state.get("initial_qty", {}).get(ticker, 0)

            try:
                holdings = get_holdings()
                current_qty = next(
                    (int(h.get("qty", 0)) for h in holdings if h.get("ticker") == ticker), 0
                )
            except Exception:
                current_qty = _positions.get(ticker, {}).get("current_qty", 0)

            if sell_mode == "initial_qty":
                base_qty = initial_qty if initial_qty > 0 else current_qty
            else:
                base_qty = current_qty

            qty = max(1, int(base_qty * sell_pct / 100))
            qty = min(qty, current_qty)  # 보유 초과 불가

            if qty <= 0:
                log.warning(f"매도 수량 0 ({ticker}) — 스킵")
                phase_state = apply_action_to_state(phase_state, action)
                with _lock:
                    _state["phase_state"] = phase_state
                continue

            try:
                result = place_order(ticker, "sell", qty, 0)
                phase_state = apply_action_to_state(phase_state, action)
                with _lock:
                    _state["phase_state"] = phase_state
                    _state["trade_log"].insert(0, {
                        "time": now_str2, "action": "매도",
                        "ticker": ticker, "name": action.get("condition_name", ticker),
                        "price": price, "qty": qty,
                        "condition": action.get("condition_name", ""),
                        "phase": get_phase_name(strategy, phase_state["phase"]),
                        "result": result.get("msg1", "완료"),
                    })
                    _state["trade_log"] = _state["trade_log"][:100]
                log.info(f"✅ 매도: {ticker} {qty}주 @{price} | Phase→{phase_state['phase']}")
            except Exception as e:
                log.error(f"매도 실패 ({ticker}): {e}")
                with _lock:
                    _state["trade_log"].insert(0, {
                        "time": now_str2, "action": "매도실패",
                        "ticker": ticker, "name": action.get("condition_name", ticker),
                        "price": price, "qty": qty,
                        "condition": action.get("condition_name", ""),
                        "result": str(e),
                    })


def _check_and_trade_legacy(conditions: dict):
    """구 포맷(AI 추출) 조건 처리 — 하위 호환용."""
    _check_and_trade(conditions)


def _check_and_trade(conditions: dict):
    """한 사이클: 조건 체크 → 주문."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        _state["last_check"] = now_str

    _mode = _state.get("trade_mode", KIS_TRADE_MODE)

    # ── 락 조건 체크 (신규 매수 금지) ────────
    buy_locked = False
    force_liquidate = False
    for lock in conditions.get("lock_conditions", []):
        ticker_for_lock = conditions.get("buy_conditions", [{}])[0].get("ref_ticker") or \
                          conditions.get("buy_conditions", [{}])[0].get("ticker", "")
        price_for_lock = get_current_price(ticker_for_lock) if ticker_for_lock else None
        if price_for_lock and _evaluate_condition_item(
            {"condition": lock.get("condition", ""), "ticker": ticker_for_lock},
            price_for_lock,
        ):
            action = lock.get("action", "lock_buy")
            if action == "liquidate":
                force_liquidate = True
            buy_locked = True
            log.info(f"락 조건 발동: {lock.get('condition')} → {action}")

    # ── 강제 청산 ──────────────────────────
    if force_liquidate:
        for ticker, pos in list(_positions.items()):
            qty = pos.get("current_qty", 0)
            if qty > 0:
                try:
                    result = place_order(ticker, "sell", qty, 0)
                    _record_sell(ticker, qty)
                    with _lock:
                        _state["trade_log"].insert(0, {
                            "time": now_str,
                            "action": "강제청산",
                            "ticker": ticker,
                            "price": get_current_price(ticker),
                            "qty": qty,
                            "condition": "락 조건 → liquidate",
                            "result": result.get("msg1", "완료"),
                        })
                except Exception as e:
                    log.error(f"강제청산 실패 ({ticker}): {e}")
        return

    # ── 매수 조건 체크 ────────────────────
    if not buy_locked:
        for cond in conditions.get("buy_conditions", []):
            ticker = cond.get("ticker")
            if not ticker:
                continue
            price = get_current_price(ticker)
            if price is None:
                continue

            buy_price = _positions.get(ticker, {}).get("buy_price")
            if not _evaluate_condition_item(cond, price, buy_price):
                continue

            # 수량 계산: weight_pct 우선, 없으면 qty 필드
            qty = cond.get("qty", None)
            weight_pct = cond.get("weight_pct")
            if weight_pct and price > 0:
                # 계좌 잔고 조회가 필요하나, 현재는 모의 고정값 사용
                # TODO: 계좌 개설 후 get_account_balance() 연동
                portfolio_value = _state.get("portfolio_value", 1_000_000)
                target_amount = portfolio_value * weight_pct / 100
                qty = max(1, int(target_amount / price))
            elif qty is None:
                qty = 1
            try:
                qty = int(qty)
            except Exception:
                qty = 1

            order_price = 0  # 시장가
            try:
                result = place_order(ticker, "buy", qty, order_price)
                _record_buy(ticker, price, qty)
                log_entry = {
                    "time": now_str,
                    "action": "매수",
                    "ticker": ticker,
                    "name": cond.get("name", ticker),
                    "price": price,
                    "qty": qty,
                    "condition": cond.get("condition", ""),
                    "label": cond.get("label", ""),
                    "result": result.get("msg1", "완료"),
                }
                with _lock:
                    _state["trade_log"].insert(0, log_entry)
                    _state["trade_log"] = _state["trade_log"][:100]
                log.info(f"매수 완료: {ticker} {qty}주 @{price}")
            except Exception as e:
                log.error(f"매수 주문 실패 ({ticker}): {e}")
                with _lock:
                    _state["trade_log"].insert(0, {
                        "time": now_str,
                        "action": "매수실패",
                        "ticker": ticker,
                        "name": cond.get("name", ticker),
                        "price": price,
                        "qty": qty,
                        "condition": cond.get("condition", ""),
                        "result": str(e),
                    })

    # ── 매도 조건 체크 ────────────────────
    for cond in conditions.get("sell_conditions", []):
        ticker = cond.get("ticker")
        if not ticker:
            continue
        price = get_current_price(ticker)
        if price is None:
            continue

        buy_price = _positions.get(ticker, {}).get("buy_price")
        if not _evaluate_condition_item(cond, price, buy_price):
            continue

        qty = _calc_sell_qty(cond, ticker)
        order_price = 0  # 시장가
        try:
            result = place_order(ticker, "sell", qty, order_price)
            _record_sell(ticker, qty)
            log_entry = {
                "time": now_str,
                "action": "매도",
                "ticker": ticker,
                "name": cond.get("name", ticker),
                "price": price,
                "qty": qty,
                "condition": cond.get("condition", ""),
                "label": cond.get("label", ""),
                "result": result.get("msg1", "완료"),
            }
            with _lock:
                _state["trade_log"].insert(0, log_entry)
                _state["trade_log"] = _state["trade_log"][:100]
            log.info(f"매도 완료: {ticker} {qty}주 @{price}")
        except Exception as e:
            log.error(f"매도 주문 실패 ({ticker}): {e}")
            with _lock:
                _state["trade_log"].insert(0, {
                    "time": now_str,
                    "action": "매도실패",
                    "ticker": ticker,
                    "name": cond.get("name", ticker),
                    "price": price,
                    "qty": qty,
                    "condition": cond.get("condition", ""),
                    "result": str(e),
                })


# ─────────────────────────────────────────────
# 공개 API (main.py에서 호출)
# ─────────────────────────────────────────────
def start_trading(conditions: dict, trade_mode: str = None) -> bool:
    """자동매매 시작 (구 포맷 조건). 이미 실행 중이면 False."""
    with _lock:
        if _state["running"]:
            return False
        _state["conditions"] = conditions
        _state["strategy"] = None
        _state["running"] = True
        _state["error"] = None
        if trade_mode in ("real", "paper"):
            _state["trade_mode"] = trade_mode
            _state["token"] = None
            _state["token_expires"] = None

    t = threading.Thread(target=_trading_loop, daemon=True)
    t.start()
    with _lock:
        _state["thread"] = t
    return True


def start_trading_phase(strategy: dict, trade_mode: str = None, resume: bool = False) -> bool:
    """Phase 엔진 기반 자동매매 시작."""
    from phase_engine import initial_phase_state
    with _lock:
        if _state["running"]:
            return False
        _state["strategy"] = strategy
        _state["conditions"] = None
        _state["running"] = True
        _state["error"] = None
        # resume=True면 기존 phase_state 유지, False면 초기화
        if not resume or not _state.get("phase_state"):
            _state["phase_state"] = initial_phase_state()
            _state["phase_state"]["phase"] = strategy.get("initial_phase", 0)
        if trade_mode in ("real", "paper"):
            _state["trade_mode"] = trade_mode
            _state["token"] = None
            _state["token_expires"] = None

    t = threading.Thread(target=_trading_loop, daemon=True)
    t.start()
    with _lock:
        _state["thread"] = t
    return True


def stop_trading() -> bool:
    """자동매매 정지."""
    with _lock:
        if not _state["running"]:
            return False
        _state["running"] = False
    return True


def get_status() -> dict:
    """현재 상태 조회."""
    with _lock:
        strategy = _state.get("strategy")
        phase_state = _state.get("phase_state")
        phase_info = None
        if strategy and phase_state is not None:
            from phase_engine import get_phase_name
            phase_num = phase_state.get("phase", 0)
            phase_info = {
                "phase": phase_num,
                "phase_name": get_phase_name(strategy, phase_num),
                "locked": phase_state.get("locked", False),
                "lock_reason": phase_state.get("lock_reason"),
                "triggered": phase_state.get("triggered", {}),
            }
        return {
            "running": _state["running"],
            "conditions": _state["conditions"],
            "strategy": strategy,
            "phase_state": phase_info,
            "conditions_image": _state["conditions_image"],
            "last_check": _state["last_check"],
            "error": _state["error"],
            "trade_log": _state["trade_log"][:20],
            "trade_mode": _state.get("trade_mode", KIS_TRADE_MODE),
            "account_no": KIS_ACCOUNT_NO[:4] + "****" if KIS_ACCOUNT_NO else "",
        }


def set_conditions_image_text(text: str):
    """이미지 분석 결과 텍스트 저장."""
    with _lock:
        _state["conditions_image"] = text


def reset_conditions():
    """조건/전략 전체 초기화 (실행 중이면 중지 후 초기화)."""
    with _lock:
        if _state["running"]:
            _state["running"] = False
        _state["conditions"] = None
        _state["strategy"] = None
        _state["phase_state"] = None
        _state["conditions_image"] = None
        _state["trade_log"] = []
        _state["last_check"] = None
        _state["error"] = None


# ─────────────────────────────────────────────
# 이중 검증 — 추출된 조건 vs 원본 이미지/텍스트
# ─────────────────────────────────────────────

_VERIFY_PROMPT_BASE = """당신은 주식 자동매매 전략 검수 전문가입니다.
아래 "추출된 JSON"이 원본(이미지 또는 텍스트)에 적힌 모든 조건을 얼마나 정확하게 담고 있는지 검증하세요.

=== 검증 기준 ===
1. 원본의 모든 매수/매도/락 조건이 추출됐는가?
2. 수치(%, 일수, 배수)가 정확한가?
3. 분할매도 비율(sell_pct), 비중(weight_pct), 참조종목(ref_ticker)이 올바른가?
4. 빠진 조건이나 잘못 해석된 조건이 있는가?

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트 없이 JSON만 출력하세요.

{
  "match_pct": 85,
  "total_in_source": 12,
  "total_extracted": 10,
  "missing": ["빠진 조건 설명1", "빠진 조건 설명2"],
  "wrong": ["잘못 추출된 조건 설명 (원본: X, 추출: Y)"],
  "notes": "전반적 평가 한 줄 요약"
}

=== 추출된 JSON ===
{extracted_json}
"""


def verify_conditions_image(image_base64: str, mime_type: str, extracted: dict) -> dict:
    """
    원본 이미지 + 추출 JSON → 일치율 검증.
    Gemini Vision으로 이미지를 다시 보면서 누락/오류 체크.
    실패 시 {"match_pct": -1, "error": "..."} 반환.
    """
    try:
        prompt = _VERIFY_PROMPT_BASE.replace(
            "{extracted_json}", json.dumps(extracted, ensure_ascii=False, indent=2)
        )
        gemini_key = os.getenv("GEMINI_VISION_API_KEY") or os.getenv("GEMINI_API_KEY", "")
        if not gemini_key:
            raise ValueError("GEMINI_API_KEY 없음")
        model = os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime_type, "data": image_base64}},
                ]
            }],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 1024},
        }
        resp = requests.post(url, params={"key": gemini_key}, json=body, timeout=30)
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        result = _parse_vision_text(text)
        result["_verifier"] = "gemini-vision"
        return result
    except Exception as e:
        log.warning(f"이미지 검증 실패: {e}")
        return {"match_pct": -1, "error": str(e), "missing": [], "wrong": [], "notes": "검증 실패"}


def verify_conditions_text(original_text: str, extracted: dict) -> dict:
    """
    원본 텍스트 + 추출 JSON → 일치율 검증.
    Groq(텍스트 전용)로 독립 검증 — 추출에 쓴 모델과 다른 경로.
    """
    try:
        prompt = _VERIFY_PROMPT_BASE.replace(
            "{extracted_json}", json.dumps(extracted, ensure_ascii=False, indent=2)
        ) + f"\n\n=== 원본 텍스트 ===\n{original_text}"

        groq_key = os.getenv("GROQ_API_KEY", "")
        if not groq_key:
            raise ValueError("GROQ_API_KEY 없음")
        model = os.getenv("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
        url = "https://api.groq.com/openai/v1/chat/completions"
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 1024,
        }
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
            json=body,
            timeout=30,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"]
        result = _parse_vision_text(text)
        result["_verifier"] = "groq-text"
        return result
    except Exception as e:
        log.warning(f"텍스트 검증 실패: {e}")
        return {"match_pct": -1, "error": str(e), "missing": [], "wrong": [], "notes": "검증 실패"}


# ─────────────────────────────────────────────
# 조건 수정 — 기존 JSON + 수정 텍스트 → 픽스된 JSON
# ─────────────────────────────────────────────

_FIX_PROMPT = """당신은 주식 자동매매 전략 수정 전문가입니다.
아래 "기존 조건 JSON"과 "수정 사항 텍스트"를 보고,
수정 사항 텍스트에 명시된 내용만 정확히 반영하여 수정된 JSON을 반환하세요.

=== 수정 원칙 ===
1. 수정 사항에 없는 조건은 절대 변경하지 마세요.
2. 수치 오류(예: 이상↔이하 반전, % 값 오기입)를 정확히 고치세요.
3. 빠진 조건은 적절한 위치(buy/sell/lock)에 추가하세요.
4. 삭제 명시된 조건만 제거하세요.
5. condition 필드의 방향(이상/이하/초과/미만)에 특히 주의하세요.

반드시 아래와 동일한 JSON 스키마로만 응답하세요. JSON 외 텍스트는 절대 쓰지 마세요.

=== 기존 조건 JSON ===
{existing_json}

=== 수정 사항 텍스트 ===
{fix_text}

=== 수정된 JSON (동일 스키마) ==="""


def fix_conditions(existing: dict, fix_text: str) -> dict:
    """
    기존 조건 JSON + 수정 텍스트 → 수정된 조건 JSON 반환.
    Groq 우선, 실패 시 Gemini 텍스트 모드.
    """
    prompt = _FIX_PROMPT.replace(
        "{existing_json}", json.dumps(existing, ensure_ascii=False, indent=2)
    ).replace("{fix_text}", fix_text)

    # 1) Groq 시도
    groq_key = os.getenv("GROQ_API_KEY", "")
    if groq_key:
        try:
            model = os.getenv("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 4096,
                },
                timeout=40,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            result = _parse_vision_text(text)
            result["_fixer"] = "groq"
            return result
        except Exception as e:
            log.warning(f"Groq fix 실패, Gemini fallback: {e}")

    # 2) Gemini fallback
    gemini_key = os.getenv("GEMINI_VISION_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    if gemini_key:
        try:
            model = os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash")
            resp = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                params={"key": gemini_key},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.0, "maxOutputTokens": 4096},
                },
                timeout=40,
            )
            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            result = _parse_vision_text(text)
            result["_fixer"] = "gemini"
            return result
        except Exception as e:
            log.warning(f"Gemini fix 실패: {e}")

    raise RuntimeError("조건 수정 가능한 AI 모델이 없습니다 (API 키 확인).")
