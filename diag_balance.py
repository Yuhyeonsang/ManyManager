#!/usr/bin/env python3
"""
diag_balance.py — KIS 해외/국내 잔고·매수가능금액 응답 진단 (읽기전용, 주문 안 함)
서버에서:  cd ~/ManyManager && ./venv/bin/python diag_balance.py
출력을 그대로 복사해서 붙여주면 get_account_balance/get_holdings 필드를 정확히 맞춤.
"""
import os, json, requests
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

KEY  = os.getenv("KIS_APP_KEY", "")
SEC  = os.getenv("KIS_APP_SECRET", "")
ACCT = os.getenv("KIS_ACCOUNT_NO", "")
MODE = os.getenv("KIS_TRADE_MODE", "real")
EXCD = os.getenv("KIS_US_EXCHANGE", "NASD")
BASE = "https://openapivts.koreainvestment.com:9443" if MODE == "paper" else "https://openapi.koreainvestment.com:9443"

cano = ACCT.replace("-", "").strip()[:8]
prdt = (ACCT.replace("-", "").strip()[8:10] or "01")
print(f"[설정] MODE={MODE} ACCT={cano}-{prdt} EXCD={EXCD} KEY={'있음' if KEY else '없음'}")

tok = requests.post(f"{BASE}/oauth2/tokenP",
                    json={"grant_type": "client_credentials", "appkey": KEY, "appsecret": SEC},
                    timeout=10).json().get("access_token")
print("[토큰]", "발급OK" if tok else "발급실패")


def H(tr):
    return {"authorization": f"Bearer {tok}", "appkey": KEY, "appsecret": SEC,
            "tr_id": tr, "custtype": "P", "Content-Type": "application/json; charset=utf-8"}


def show(title, method, url, tr, params):
    print("\n" + "=" * 60)
    print(f"== {title}  (tr_id={tr}) ==")
    try:
        r = requests.get(url, headers=H(tr), params=params, timeout=12)
        print("HTTP", r.status_code)
        try:
            j = r.json()
            print("rt_cd:", j.get("rt_cd"), "| msg:", j.get("msg1"))
            # output / output1 / output2 키 구조만 요약
            for k in ("output", "output1", "output2"):
                if k in j:
                    v = j[k]
                    if isinstance(v, list):
                        print(f"{k}: list({len(v)})", json.dumps(v[:1], ensure_ascii=False)[:900])
                    else:
                        print(f"{k}:", json.dumps(v, ensure_ascii=False)[:900])
        except Exception:
            print("BODY:", r.text[:600])
    except Exception as e:
        print("요청실패:", e)


# 1) 해외 매수가능금액
show("해외 매수가능금액", "GET",
     f"{BASE}/uapi/overseas-stock/v1/trading/inquire-psamount",
     "TTTS3007R" if MODE != "paper" else "VTTS3007R",
     {"CANO": cano, "ACNT_PRDT_CD": prdt, "OVRS_EXCG_CD": EXCD,
      "OVRS_ORD_UNPR": "80", "ITEM_CD": "TQQQ"})

# 2) 해외 잔고
show("해외 잔고", "GET",
     f"{BASE}/uapi/overseas-stock/v1/trading/inquire-balance",
     "TTTS3012R" if MODE != "paper" else "VTTS3012R",
     {"CANO": cano, "ACNT_PRDT_CD": prdt, "OVRS_EXCG_CD": EXCD,
      "TR_CRCY_CD": "USD", "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""})

# 3) (참고) 원화 예수금 — 국내 잔고
show("국내 잔고(예수금 참고)", "GET",
     f"{BASE}/uapi/domestic-stock/v1/trading/inquire-balance",
     "TTTC8434R" if MODE != "paper" else "VTTC8434R",
     {"CANO": cano, "ACNT_PRDT_CD": prdt, "AFHR_FLPR_YN": "N", "OFL_YN": "",
      "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
      "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "01",
      "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})

print("\n[완료] 위 출력 전체를 복사해서 붙여줘 (앱키/시크릿/잔고 숫자는 노출 안 됨)")
