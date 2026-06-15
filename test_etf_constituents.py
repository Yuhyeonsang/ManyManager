"""ETF 구성종목 파싱 테스트 — Pi에서 실행: python3 test_etf_constituents.py"""
import requests
from bs4 import BeautifulSoup
import re

def is_valid_kr_name(name):
    return bool(re.search(r'[가-힣a-zA-Z]', name))

def get_constituents_naver_coinfo(naver_code, top_n=7):
    url = f"https://finance.naver.com/item/coinfo.naver?code={naver_code}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get(url, headers=headers, timeout=10)
    soup = BeautifulSoup(r.content, "html.parser", from_encoding="euc-kr")

    pairs = []
    for table in soup.find_all("table"):
        headers_row = table.find("tr")
        if not headers_row:
            continue
        ths = [th.get_text(strip=True) for th in headers_row.find_all(["th", "td"])]
        if "구성종목명" not in str(ths):
            continue
        print(f"  [테이블 헤더] {ths}")
        try:
            name_idx = next(i for i, h in enumerate(ths) if "구성종목명" in h)
            weight_idx = next((i for i, h in enumerate(ths) if "구성비중" in h), None)
        except StopIteration:
            continue
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) <= name_idx:
                continue
            name = cells[name_idx].get_text(strip=True)
            if not name or not is_valid_kr_name(name):
                continue
            weight = 0.0
            if weight_idx is not None and len(cells) > weight_idx:
                try:
                    weight = float(cells[weight_idx].get_text(strip=True).replace(",", "").replace("%", ""))
                except ValueError:
                    pass
            pairs.append((name, weight))
        if pairs:
            break

    pairs.sort(key=lambda x: -x[1])
    return pairs[:top_n]

# 테스트: SOL 반도체AI TOP2 플러스 (476010 → naver 0167A0)
print("=== SOL 반도체AI TOP2 플러스 (0167A0) ===")
result = get_constituents_naver_coinfo("0167A0", top_n=7)
if result:
    for name, weight in result:
        print(f"  {name}: {weight}%")
else:
    print("  파싱 실패 — 테이블 구조 확인 필요")

# 다른 ETF도 테스트하려면 아래에 추가
# print("\n=== KODEX 반도체 (091160 → naver 코드) ===")
# result2 = get_constituents_naver_coinfo("091160", top_n=5)
