"""
ETF 데이터 파싱 진단 스크립트 — Pi에서 실행
python3 test_etf_debug.py
"""
import requests
from bs4 import BeautifulSoup
import re

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

KRX_CODE    = "476010"   # KODEX AI반도체핵심장비 KRX 코드
NAVER_CODE  = "0167A0"   # wisereport / 네이버 코드 (등록된 것)

print("=" * 60)
print(f"ETF 진단: KRX={KRX_CODE}, NAVER={NAVER_CODE}")
print("=" * 60)

# ─── 1. sise_day 테스트 ───────────────────────────────────────
print("\n[1] 네이버 일별시세 (sise_day) 테스트")
for code in [NAVER_CODE, KRX_CODE]:
    url = f"https://finance.naver.com/item/sise_day.naver?code={code}&page=1"
    try:
        r = requests.get(url, headers=headers, timeout=8)
        soup = BeautifulSoup(r.content, "html.parser", from_encoding="euc-kr")
        table = soup.find("table", class_="type2")
        rows = []
        if table:
            for tr in table.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) < 7:
                    continue
                texts = [td.get_text(strip=True) for td in tds]
                date_str = texts[0]
                if not re.match(r"\d{4}\.\d{2}\.\d{2}", date_str):
                    continue
                close_raw = re.sub(r"[^0-9]", "", texts[1])
                rows.append((date_str, int(close_raw) if close_raw else None))
        print(f"  code={code}: HTTP {r.status_code}, 테이블={'있음' if table else '없음'}, 행={len(rows)}개")
        if rows:
            print(f"    최신: {rows[0][0]} 종가={rows[0][1]:,}원")
            closes = [r[1] for r in rows if r[1]]
            if len(closes) >= 20:
                ma20 = round(sum(closes[:20]) / 20)
                print(f"    MA20(20일): {ma20:,}원  ← 정상이면 이 값이 앱에 표시됨")
            else:
                print(f"    ⚠ 데이터 {len(closes)}개뿐 — MA20 계산 불가 (20개 필요)")
    except Exception as e:
        print(f"  code={code}: ❌ 실패 — {e}")

# ─── 2. wisereport 구성종목 테스트 ───────────────────────────
print("\n[2] wisereport 구성종목 테스트")
url = f"https://navercomp.wisereport.co.kr/v2/ETF/index.aspx?cmp_cd={NAVER_CODE}"
try:
    r = requests.get(url, headers={"User-Agent": headers["User-Agent"], "Referer": "https://finance.naver.com/"}, timeout=15)
    content = r.content.decode("utf-8", errors="replace")
    names_raw = re.findall(r'"STK_NM_KOR"\s*:\s*"([^"]+)"', content)
    weights_raw = re.findall(r'"ETF_WEIGHT"\s*:\s*([\d.]+)', content)
    print(f"  HTTP {r.status_code}, HTML 길이: {len(content)}bytes")
    print(f"  STK_NM_KOR 항목: {len(names_raw)}개 → {names_raw[:5]}")
    print(f"  ETF_WEIGHT 항목: {len(weights_raw)}개 → {weights_raw[:5]}")
    if names_raw:
        print("  ✅ wisereport 구성종목 파싱 성공!")
    else:
        print("  ❌ wisereport 구성종목 없음 — HTML 확인 필요")
        # 힌트: JSON 패턴 확인
        json_keys = re.findall(r'"([A-Z_]{3,20})"\s*:', content[:3000])
        print(f"  HTML 앞부분 키 목록: {list(set(json_keys))[:20]}")
except Exception as e:
    print(f"  ❌ 실패 — {e}")

# ─── 3. naver coinfo 테스트 (KRX 코드로) ────────────────────
print("\n[3] 네이버 coinfo 구성종목 테스트 (KRX코드)")
url = f"https://finance.naver.com/item/coinfo.naver?code={KRX_CODE}"
try:
    r = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(r.content, "html.parser", from_encoding="euc-kr")
    print(f"  HTTP {r.status_code}, 응답 길이: {len(r.content)}bytes")
    tables = soup.find_all("table")
    print(f"  테이블 개수: {len(tables)}")
    found = False
    for i, table in enumerate(tables):
        first_row = table.find("tr")
        if first_row:
            cells = [c.get_text(strip=True) for c in first_row.find_all(["th", "td"])]
            if "구성종목명" in str(cells):
                print(f"  ✅ table[{i}]에서 구성종목 테이블 발견! 헤더: {cells}")
                found = True
    if not found:
        print(f"  ❌ 구성종목 테이블 없음 (JS 렌더링 필요 — 정상)")
        # 구성종목 언급 텍스트 확인
        mentions = [t.strip()[:50] for t in soup.find_all(string=re.compile("구성종목")) if t.strip()]
        print(f"  '구성종목' 언급: {mentions[:3]}")
except Exception as e:
    print(f"  ❌ 실패 — {e}")

print("\n" + "=" * 60)
print("진단 완료")
