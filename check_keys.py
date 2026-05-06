"""
3개 API 키 (Gemini / 네이버 / DART) 가 .env에 잘 박혔는지,
실제 호출이 되는지 한 번에 확인.

실행:  python check_keys.py
"""
import os
import sys
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[WARN] python-dotenv 미설치 → pip install python-dotenv")

OK = "[OK]"
FAIL = "[FAIL]"
WARN = "[WARN]"


def mask(s: str, keep: int = 4) -> str:
    if not s:
        return "(없음)"
    if len(s) <= keep * 2:
        return s[:keep] + "***"
    return s[:keep] + "***" + s[-keep:]


def check_gemini() -> bool:
    key = os.getenv("GEMINI_API_KEY")
    print(f"\n=== Gemini ===  키: {mask(key)}")
    if not key or "여기에" in key:
        print(f"{FAIL} GEMINI_API_KEY가 .env에 비었거나 템플릿 그대로임")
        return False
    try:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-1.5-flash:generateContent?key={key}"
        )
        body = {"contents": [{"parts": [{"text": "ping"}]}]}
        r = requests.post(url, json=body, timeout=10)
        if r.status_code == 200:
            print(f"{OK} Gemini API 정상 응답")
            return True
        print(f"{FAIL} HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"{FAIL} 호출 예외: {e}")
        return False


def check_naver() -> bool:
    cid = os.getenv("NAVER_CLIENT_ID")
    csec = os.getenv("NAVER_CLIENT_SECRET")
    print(f"\n=== 네이버 검색 ===  ID: {mask(cid)}  SECRET: {mask(csec)}")
    if not cid or not csec or "여기에" in (cid + csec):
        print(f"{FAIL} NAVER_CLIENT_ID / SECRET 누락 또는 템플릿 그대로")
        return False
    try:
        r = requests.get(
            "https://openapi.naver.com/v1/search/news.json",
            headers={
                "X-Naver-Client-Id": cid,
                "X-Naver-Client-Secret": csec,
            },
            params={"query": "삼성전자", "display": 1},
            timeout=10,
        )
        if r.status_code == 200:
            cnt = len(r.json().get("items", []))
            print(f"{OK} 네이버 응답 정상 (샘플 {cnt}건)")
            return True
        print(f"{FAIL} HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"{FAIL} 호출 예외: {e}")
        return False


def check_dart() -> bool:
    key = os.getenv("DART_API_KEY")
    print(f"\n=== Open DART ===  키: {mask(key)}")
    if not key or "여기에" in key:
        print(f"{FAIL} DART_API_KEY가 .env에 비었거나 템플릿 그대로임")
        return False
    try:
        r = requests.get(
            "https://opendart.fss.or.kr/api/list.json",
            params={
                "crtfc_key": key,
                "corp_code": "00126380",  # 삼성전자
                "bgn_de": "20250101",
                "end_de": "20250131",
                "page_count": "1",
            },
            timeout=10,
        )
        data = r.json()
        st = data.get("status")
        if st in ("000", "013"):  # 000=정상, 013=조회결과없음 → 키는 유효
            print(f"{OK} DART 응답 정상 (status={st})")
            return True
        print(f"{FAIL} status={st}, message={data.get('message')}")
        return False
    except Exception as e:
        print(f"{FAIL} 호출 예외: {e}")
        return False


if __name__ == "__main__":
    print("=" * 50)
    print("API 키 검증 — .env 로드 결과")
    print("=" * 50)
    results = {
        "Gemini": check_gemini(),
        "Naver":  check_naver(),
        "DART":   check_dart(),
    }
    print("\n" + "=" * 50)
    print("요약")
    print("=" * 50)
    for k, ok in results.items():
        print(f"  {k:8s} {'OK' if ok else 'FAIL'}")
    sys.exit(0 if all(results.values()) else 1)
