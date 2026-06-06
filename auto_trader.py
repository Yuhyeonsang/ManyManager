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
# Gemini Vision으로 이미지 분석
# ─────────────────────────────────────────────
def analyze_image_conditions(image_base64: str, mime_type: str = "image/jpeg") -> dict:
    """
    이미지에서 매수/매도 조건 추출.
    반환 형태:
    {
      "summary": "조건 요약 텍스트",
      "buy_conditions": [
        {"ticker": "005930", "name": "삼성전자", "condition": "RSI < 30", "qty": 10}
      ],
      "sell_conditions": [
        {"ticker": "005930", "name": "삼성전자", "condition": "수익률 > 5%", "qty": "all"}
      ],
      "check_interval_minutes": 5
    }
    """
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_key:
        raise ValueError("GEMINI_API_KEY 환경변수가 없습니다.")

    prompt = """이 이미지는 주식 자동매매 조건표입니다.
이미지에 적힌 매수/매도 조건을 정확히 읽고 아래 JSON 형식으로 추출하세요.
반드시 JSON만 출력하고 다른 텍스트는 쓰지 마세요.

{
  "summary": "조건 전체 요약 (한국어 1~2문장)",
  "buy_conditions": [
    {
      "ticker": "종목코드(예:005930 또는 AAPL)",
      "name": "종목명",
      "condition": "매수 조건 설명",
      "qty": 매수수량(숫자),
      "price_type": "market(시장가) 또는 limit(지정가)"
    }
  ],
  "sell_conditions": [
    {
      "ticker": "종목코드",
      "name": "종목명",
      "condition": "매도 조건 설명",
      "qty": 매도수량(숫자 또는 all),
      "price_type": "market 또는 limit"
    }
  ],
  "check_interval_minutes": 조건체크주기(기본5)
}

종목코드가 없으면 null로 두세요. 수량이 명시되지 않으면 1로 설정하세요."""

    model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
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
    data = resp.json()

    text = data["candidates"][0]["content"]["parts"][0]["text"]
    # JSON 블록 추출
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1])

    return json.loads(text)


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
            # 토큰 캐시 초기화 (모드 바뀌면 새 토큰 필요)
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
