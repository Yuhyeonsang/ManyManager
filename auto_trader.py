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
    "conditions": None,
    "conditions_image": None,
    "token": None,
    "token_expires": None,
    "trade_log": [],
    "last_check": None,
    "error": None,
    "thread": None,
    "trade_mode": KIS_TRADE_MODE,  # 런타임 전환 가능
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
def get_current_price(ticker: str) -> Optional[float]:
    """KIS API로 현재가 조회. 실패 시 None."""
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
    price: 0이면 시장가
    """
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
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1024}
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
6. RSI 과매도 시 '비중 추가'는 weight_mode: "add"로 설정.
7. 이미지에 있는 수치(%, 일수, 배수)를 절대 바꾸지 마세요.

=== TP(익절) 조건 파싱 규칙 (매우 중요) ===
TP 조건에는 두 가지 숫자가 있습니다. 절대 혼동하지 마세요:
  A) 수익률 기준 (condition에 기입): "수익률 +X% 도달 시" 의 X
  B) 매도 비율 (sell_pct에 기입): "보유수량의 Y% 매도" 의 Y

예시:
  "TP1: 수익률 +15% 달성 시, 최초수량의 50% 매도"
  → condition: "TP1 +15% 도달 시", sell_pct: 50, sell_mode: "initial_qty"

  "TP2: 수익률 +100% 달성 시, 최초수량의 35% 매도"
  → condition: "TP2 +100% 도달 시", sell_pct: 35, sell_mode: "initial_qty"
  ※ 수익률 100%와 매도비율 35%를 절대 혼동하지 마세요!

  "TP3: 수익률 +350% 달성 시, 남은 전량 매도"
  → condition: "TP3 +350% 도달 시", sell_pct: 100, sell_mode: "current"

=== 골든크로스(GC) 풀매수 규칙 ===
"골든크로스 발생 시 잔여 현금 100% 전액 투입"은 buy_conditions에 추가:
  → condition: "골든크로스 발생", weight_pct: 100, weight_mode: "add", label: "GC 풀매수"

=== RSI 과매도 비중 추가 규칙 ===
"RSI 35 이하 시 +10% 비중 추가", "RSI 25 이하 시 +15% 비중 추가"는 각각 별도 항목:
  → condition: "RSI < 35", weight_pct: 10, weight_mode: "add"
  → condition: "RSI < 25", weight_pct: 15, weight_mode: "add"

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



# ─────────────────────────────────────────────
# 조건 체크 및 자동매매 루프
# ─────────────────────────────────────────────
def _evaluate_condition(condition_str: str, current_price: float, ticker: str) -> bool:
    """
    조건 문자열을 해석해 True/False 반환.
    지원 형식 예시:
      "현재가 < 70000"
      "price < 70000"
      "RSI < 30"  (RSI는 현재 미지원 → False)
      "수익률 > 5%"  (미지원 → False)
      "항상"  → True
    """
    cond = condition_str.strip().lower()

    # 항상 실행
    if cond in ("항상", "always", "즉시"):
        return True

    # price / 현재가 비교
    for keyword in ["현재가", "price", "가격"]:
        if keyword in cond:
            try:
                if "<=" in cond:
                    threshold = float(cond.split("<=")[1].strip().replace(",", ""))
                    return current_price <= threshold
                elif ">=" in cond:
                    threshold = float(cond.split(">=")[1].strip().replace(",", ""))
                    return current_price >= threshold
                elif "<" in cond:
                    threshold = float(cond.split("<")[1].strip().replace(",", ""))
                    return current_price < threshold
                elif ">" in cond:
                    threshold = float(cond.split(">")[1].strip().replace(",", ""))
                    return current_price > threshold
            except Exception:
                pass

    # 지원하지 않는 조건 → False (RSI, MACD 등 지표는 추후 구현)
    log.warning(f"미지원 조건: '{condition_str}' → 건너뜀")
    return False


def _trading_loop():
    """백그라운드 스레드에서 실행되는 자동매매 루프."""
    log.info("자동매매 루프 시작")

    while True:
        with _lock:
            if not _state["running"]:
                break
            conditions = _state["conditions"]

        if not conditions:
            time.sleep(30)
            continue

        interval_sec = (conditions.get("check_interval_minutes", 5)) * 60

        try:
            _check_and_trade(conditions)
        except Exception as e:
            log.error(f"매매 루프 오류: {e}")
            with _lock:
                _state["error"] = str(e)

        # 다음 체크까지 대기 (running 상태 실시간 확인)
        end_time = time.time() + interval_sec
        while time.time() < end_time:
            with _lock:
                if not _state["running"]:
                    break
            time.sleep(5)

    log.info("자동매매 루프 종료")


def _check_and_trade(conditions: dict):
    """한 사이클: 조건 체크 → 주문."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        _state["last_check"] = now_str

    # 매수 조건 체크
    for cond in conditions.get("buy_conditions", []):
        ticker = cond.get("ticker")
        if not ticker:
            continue
        price = get_current_price(ticker)
        if price is None:
            continue
        if _evaluate_condition(cond.get("condition", ""), price, ticker):
            qty = cond.get("qty", 1)
            try:
                qty = int(qty)
            except Exception:
                qty = 1
            price_type = cond.get("price_type", "market")
            order_price = 0 if price_type == "market" else int(price)
            try:
                result = place_order(ticker, "buy", qty, order_price)
                log_entry = {
                    "time": now_str,
                    "action": "매수",
                    "ticker": ticker,
                    "name": cond.get("name", ticker),
                    "price": price,
                    "qty": qty,
                    "condition": cond.get("condition", ""),
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

    # 매도 조건 체크
    for cond in conditions.get("sell_conditions", []):
        ticker = cond.get("ticker")
        if not ticker:
            continue
        price = get_current_price(ticker)
        if price is None:
            continue
        if _evaluate_condition(cond.get("condition", ""), price, ticker):
            qty_raw = cond.get("qty", 1)
            qty = 1 if qty_raw == "all" else int(qty_raw)
            price_type = cond.get("price_type", "market")
            order_price = 0 if price_type == "market" else int(price)
            try:
                result = place_order(ticker, "sell", qty, order_price)
                log_entry = {
                    "time": now_str,
                    "action": "매도",
                    "ticker": ticker,
                    "name": cond.get("name", ticker),
                    "price": price,
                    "qty": qty,
                    "condition": cond.get("condition", ""),
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
    """자동매매 시작. 이미 실행 중이면 False."""
    with _lock:
        if _state["running"]:
            return False
        _state["conditions"] = conditions
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
        return {
            "running": _state["running"],
            "conditions": _state["conditions"],
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
