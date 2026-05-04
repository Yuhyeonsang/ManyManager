import os
import json
import re
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dotenv import load_dotenv
load_dotenv()


class GeminiClient:
    """Gemini 1.5 Flash 호출 공통 베이스. 토큰 절약 + JSON 강제."""

    ENDPOINT = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-1.5-flash:generateContent"
    )

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")

    def call_json(self, prompt: str, temperature: float = 0.2) -> Dict:
        if not self.api_key:
            return {"error": "missing GEMINI_API_KEY"}
        try:
            r = requests.post(
                self.ENDPOINT,
                params={"key": self.api_key},
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": temperature,
                        "responseMimeType": "application/json",
                    },
                },
                timeout=30,
            )
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
            return json.loads(text)
        except Exception as e:
            return {"error": f"gemini call failed: {e}"}


class GeminiNewsFilter(GeminiClient):
    """핵심 뉴스 N개 선별 + 감성 점수화."""

    def filter_news(self, news_items: List[Dict], top_k: int = 3) -> List[Dict]:
        if not news_items:
            return []
        compact = [
            {
                "i": idx,
                "title": it.get("title", "")[:120],
                "desc": it.get("description", "")[:200],
            }
            for idx, it in enumerate(news_items)
        ]
        prompt = (
            f"너는 한국 주식 애널리스트다. 아래 뉴스 중 주가에 실질적 영향을 줄 만한 "
            f"핵심 뉴스 정확히 {top_k}개만 골라라.\n"
            f"기준: 실적/가이던스, 수주·계약, 규제·소송, 신제품, M&A, 거시 충격.\n"
            f"단순 시황·루머·중복은 제외.\n"
            f'반드시 JSON: {{"picks":[{{"i":번호,"reason":"한 줄 사유","impact":"긍정|부정|중립"}}]}}\n\n'
            f"뉴스 목록:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        data = self.call_json(prompt)
        if data.get("error"):
            return [{"error": data["error"]}]

        picks = data.get("picks", [])
        out: List[Dict] = []
        for p in picks[:top_k]:
            i = p.get("i")
            if isinstance(i, int) and 0 <= i < len(news_items):
                src = news_items[i]
                out.append(
                    {
                        "title": src.get("title"),
                        "link": src.get("link"),
                        "pub_date": src.get("pub_date"),
                        "reason": p.get("reason"),
                        "impact": p.get("impact"),
                    }
                )
        return out

    @staticmethod
    def sentiment_score(picks: List[Dict]) -> Tuple[float, Dict[str, int]]:
        """긍정 +1, 부정 -1, 중립 0 → 평균 점수."""
        counts = {"긍정": 0, "부정": 0, "중립": 0}
        if not picks or (picks and picks[0].get("error")):
            return 0.0, counts
        score = 0
        n = 0
        for p in picks:
            imp = p.get("impact")
            if imp in counts:
                counts[imp] += 1
                score += {"긍정": 1, "부정": -1, "중립": 0}[imp]
                n += 1
        return (round(score / n, 2) if n else 0.0), counts


class RelatedStockInferer(GeminiClient):
    """뉴스 본문에 종목명이 없어도 밸류체인/산업 키워드로 관련 상장사 후보군을 뽑는다."""

    def infer_related_stocks(
        self,
        news_items: List[Dict],
        max_candidates: int = 8,
        market_hint: str = "KOSPI/KOSDAQ",
    ) -> List[Dict]:
        if not news_items:
            return []
        compact = [
            {
                "title": it.get("title", "")[:120],
                "desc": it.get("description", "")[:240],
            }
            for it in news_items[:10]
        ]
        prompt = (
            f"너는 {market_hint} 상장사 데이터에 정통한 한국 주식 애널리스트다.\n"
            f"아래 뉴스 묶음에서 직간접 수혜/피해가 예상되는 상장사 후보를 최대 "
            f"{max_candidates}개 뽑아라.\n"
            f"규칙:\n"
            f"- 뉴스에 직접 언급된 종목뿐 아니라 밸류체인(소재→부품→완성품→유통), "
            f"정책 수혜, 기술 트렌드 파급 효과까지 추론.\n"
            f"- 이미 너무 자주 언급되는 빅테크 위주 추천은 자제, 중·소형주도 포함.\n"
            f"- 종목코드를 모르면 빈 문자열로.\n"
            f'반드시 JSON: {{"candidates":[{{'
            f'"name":"종목명","ticker":"6자리코드","reason":"왜 관련있나",'
            f'"value_chain":"upstream|midstream|downstream|policy|peer",'
            f'"expected_impact":"긍정|부정|중립",'
            f'"confidence":"상|중|하"}}]}}\n\n'
            f"뉴스 묶음:\n{json.dumps(compact, ensure_ascii=False)}"
        )
        data = self.call_json(prompt, temperature=0.3)
        if data.get("error"):
            return [{"error": data["error"]}]
        cands = data.get("candidates", [])
        cleaned: List[Dict] = []
        seen = set()
        for c in cands[:max_candidates]:
            name = (c.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            cleaned.append(
                {
                    "name": name,
                    "ticker": (c.get("ticker") or "").strip(),
                    "reason": (c.get("reason") or "").strip(),
                    "value_chain": c.get("value_chain", ""),
                    "expected_impact": c.get("expected_impact", "중립"),
                    "confidence": c.get("confidence", "중"),
                }
            )
        return cleaned


class SemanticLayer:
    """숫자 계산은 전부 파이썬이 직접. AI한테 안 맡긴다."""

    @staticmethod
    def analyze_price(price: Dict) -> Dict:
        if not price or price.get("error"):
            return {"error": (price or {}).get("error", "no price")}

        cp = price["current_price"]
        ma = price.get("moving_averages", {}) or {}
        ma5, ma20, ma60, ma120 = (
            ma.get("MA5"), ma.get("MA20"), ma.get("MA60"), ma.get("MA120")
        )

        signals: List[str] = []
        if ma5 and ma20:
            signals.append(
                "단기 정배열(MA5>MA20)" if ma5 > ma20 else "단기 역배열(MA5<MA20)"
            )
        if ma20 and ma60:
            signals.append(
                "중기 정배열(MA20>MA60)" if ma20 > ma60 else "중기 역배열(MA20<MA60)"
            )
        if ma20 and cp:
            diff = (cp - ma20) / ma20 * 100
            signals.append(
                f"20일선 상향 돌파(+{diff:.1f}%)" if diff > 0
                else f"20일선 하향 이탈({diff:.1f}%)"
            )

        hi, lo = price.get("high_52w"), price.get("low_52w")
        position_52w = round((cp - lo) / (hi - lo) * 100, 1) if hi and lo and hi > lo else None

        recent = price.get("recent_10d") or []
        momentum_10d = None
        if len(recent) >= 2:
            first, last = recent[0]["close"], recent[-1]["close"]
            momentum_10d = round((last - first) / first * 100, 2)

        return {
            "current_price": cp,
            "change_pct": price.get("change_pct"),
            "ma_summary": {"MA5": ma5, "MA20": ma20, "MA60": ma60, "MA120": ma120},
            "signals": signals,
            "position_52w_pct": position_52w,
            "momentum_10d_pct": momentum_10d,
        }

    @staticmethod
    def analyze_financials(fin: Dict) -> Dict:
        if not fin or fin.get("error"):
            return {"error": (fin or {}).get("error", "no financials")}

        ind = fin.get("indicators", {}) or {}

        def yoy(key: str) -> Optional[float]:
            cur = ind.get(key, {}).get("current")
            prev = ind.get(key, {}).get("previous")
            if cur and prev:
                return round((cur - prev) / abs(prev) * 100, 2)
            return None

        rev_yoy = yoy("revenue")
        op_yoy = yoy("operating_income")
        ni_yoy = yoy("net_income")

        equity = ind.get("total_equity", {}).get("current")
        liab = ind.get("total_liabilities", {}).get("current")
        debt_ratio = round(liab / equity * 100, 2) if equity and liab else None

        return {
            "year": fin.get("year"),
            "margins": fin.get("margins", {}),
            "yoy_growth_pct": {
                "revenue": rev_yoy,
                "operating_income": op_yoy,
                "net_income": ni_yoy,
            },
            "debt_to_equity_pct": debt_ratio,
            "raw_current": {k: v.get("current") for k, v in ind.items()},
        }


class InvestmentGrader:
    """[주가 추세 + 재무 건전성 + 뉴스 감성] → 투자 등급 1차 판정.

    각 축을 -2 ~ +2로 점수화 → 합계 → 등급.
    환각 방지를 위해 모든 분기 조건은 파이썬에서만 평가.
    """

    GRADE_TABLE = [
        (4, "적극 매수"),
        (2, "매수"),
        (0, "보유"),
        (-2, "관망"),
        (-99, "비중 축소"),
    ]

    @staticmethod
    def _score_price(price_an: Dict) -> Tuple[int, List[str]]:
        if price_an.get("error"):
            return 0, [f"가격 데이터 없음: {price_an['error']}"]
        s, reasons = 0, []
        ma = price_an.get("ma_summary") or {}
        ma5, ma20, ma60 = ma.get("MA5"), ma.get("MA20"), ma.get("MA60")
        if ma5 and ma20:
            if ma5 > ma20:
                s += 1; reasons.append("단기 정배열(+1)")
            else:
                s -= 1; reasons.append("단기 역배열(-1)")
        if ma20 and ma60:
            if ma20 > ma60:
                s += 1; reasons.append("중기 정배열(+1)")
            else:
                s -= 1; reasons.append("중기 역배열(-1)")
        mom = price_an.get("momentum_10d_pct")
        if mom is not None:
            if mom >= 5:
                s += 1; reasons.append(f"10일 모멘텀 강세({mom:+.1f}%, +1)")
            elif mom <= -5:
                s -= 1; reasons.append(f"10일 모멘텀 약세({mom:+.1f}%, -1)")
        pos = price_an.get("position_52w_pct")
        if pos is not None:
            if pos >= 80:
                s += 1; reasons.append(f"52주 고점권({pos}%, +1)")
            elif pos <= 20:
                s -= 1; reasons.append(f"52주 저점권({pos}%, -1)")
        return max(-2, min(2, s)), reasons

    @staticmethod
    def _score_financials(fin_an: Dict) -> Tuple[int, List[str]]:
        if fin_an.get("error"):
            return 0, [f"재무 데이터 없음: {fin_an['error']}"]
        s, reasons = 0, []
        g = fin_an.get("yoy_growth_pct") or {}
        op_yoy = g.get("operating_income")
        rev_yoy = g.get("revenue")
        if op_yoy is not None:
            if op_yoy >= 20:
                s += 1; reasons.append(f"영업익 YoY +{op_yoy}%(+1)")
            elif op_yoy <= -20:
                s -= 1; reasons.append(f"영업익 YoY {op_yoy}%(-1)")
        if rev_yoy is not None:
            if rev_yoy >= 10:
                s += 1; reasons.append(f"매출 YoY +{rev_yoy}%(+1)")
            elif rev_yoy <= -10:
                s -= 1; reasons.append(f"매출 YoY {rev_yoy}%(-1)")
        m = fin_an.get("margins") or {}
        op_m = m.get("operating_margin_pct")
        if op_m is not None:
            if op_m >= 10:
                s += 1; reasons.append(f"영업이익률 {op_m}%(+1)")
            elif op_m <= 0:
                s -= 1; reasons.append(f"영업이익률 {op_m}%(-1)")
        debt = fin_an.get("debt_to_equity_pct")
        if debt is not None:
            if debt >= 200:
                s -= 1; reasons.append(f"부채비율 과다 {debt}%(-1)")
            elif debt <= 80:
                s += 1; reasons.append(f"부채비율 양호 {debt}%(+1)")
        return max(-2, min(2, s)), reasons

    @staticmethod
    def _score_sentiment(sent_score: float, counts: Dict[str, int]) -> Tuple[int, List[str]]:
        s = 0
        reasons = []
        if sent_score >= 0.5:
            s = 2; reasons.append(f"뉴스 감성 강한 긍정({sent_score})")
        elif sent_score >= 0.2:
            s = 1; reasons.append(f"뉴스 감성 우호({sent_score})")
        elif sent_score <= -0.5:
            s = -2; reasons.append(f"뉴스 감성 강한 부정({sent_score})")
        elif sent_score <= -0.2:
            s = -1; reasons.append(f"뉴스 감성 부정({sent_score})")
        else:
            reasons.append(f"뉴스 감성 중립({sent_score})")
        reasons.append(
            f"뉴스 분포 → 긍정 {counts.get('긍정',0)} / "
            f"부정 {counts.get('부정',0)} / 중립 {counts.get('중립',0)}"
        )
        return s, reasons

    def grade(
        self,
        price_an: Dict,
        fin_an: Dict,
        sent_score: float,
        sent_counts: Dict[str, int],
    ) -> Dict:
        ps, pr = self._score_price(price_an)
        fs, fr = self._score_financials(fin_an)
        ss, sr = self._score_sentiment(sent_score, sent_counts)
        total = ps + fs + ss
        grade = next(g for thr, g in self.GRADE_TABLE if total >= thr)
        return {
            "total_score": total,
            "grade": grade,
            "axis_scores": {"price": ps, "financials": fs, "sentiment": ss},
            "rationale": {"price": pr, "financials": fr, "sentiment": sr},
        }


class ReportBuilder:
    """선별 뉴스 + 계산 수치 + 추론 관련주 + 투자 등급 → 클로드 웹 붙여넣기 텍스트."""

    def __init__(
        self,
        gemini_filter: Optional[GeminiNewsFilter] = None,
        related_inferer: Optional[RelatedStockInferer] = None,
        grader: Optional[InvestmentGrader] = None,
    ):
        self.gemini = gemini_filter or GeminiNewsFilter()
        self.related = related_inferer or RelatedStockInferer()
        self.grader = grader or InvestmentGrader()
        self.sem = SemanticLayer()

    def build(
        self,
        ticker: str,
        bundle: Dict,
        company_name: Optional[str] = None,
        top_k_news: int = 3,
        max_related: int = 8,
    ) -> str:
        price = bundle.get("price") or {}
        news = bundle.get("news") or {}
        fin = bundle.get("financials") or {}
        items = news.get("items", []) if news else []

        price_an = self.sem.analyze_price(price)
        fin_an = self.sem.analyze_financials(fin)
        picks = self.gemini.filter_news(items, top_k=top_k_news) if items else []
        sent_score, sent_counts = self.gemini.sentiment_score(picks)
        related = self.related.infer_related_stocks(items, max_candidates=max_related) if items else []
        verdict = self.grader.grade(price_an, fin_an, sent_score, sent_counts)

        L: List[str] = []
        L.append("=" * 60)
        L.append(f"📊 일일 종목 리포트  |  {company_name or ticker} ({ticker})")
        L.append(f"생성: {datetime.now():%Y-%m-%d %H:%M}")
        L.append("=" * 60)

        # [1] 시세
        L.append("\n[1] 시세 & 기술적 시그널")
        if price_an.get("error"):
            L.append(f"  - 오류: {price_an['error']}")
        else:
            L.append(f"  - 현재가: {price_an['current_price']:,} ({price_an['change_pct']:+.2f}%)")
            ma = price_an["ma_summary"]
            L.append("  - 이동평균: " + ", ".join(f"{k} {v:,}" for k, v in ma.items() if v))
            if price_an.get("position_52w_pct") is not None:
                L.append(f"  - 52주 위치: {price_an['position_52w_pct']}%")
            if price_an.get("momentum_10d_pct") is not None:
                L.append(f"  - 10일 모멘텀: {price_an['momentum_10d_pct']:+.2f}%")
            for s in price_an["signals"]:
                L.append(f"  - 시그널: {s}")

        # [2] 재무
        L.append("\n[2] 재무 핵심 지표 (파이썬 계산)")
        if fin_an.get("error"):
            L.append(f"  - 오류: {fin_an['error']}")
        else:
            m = fin_an.get("margins") or {}
            g = fin_an.get("yoy_growth_pct") or {}
            L.append(f"  - 기준 연도: {fin_an.get('year')}")
            L.append(
                f"  - 영업이익률: {m.get('operating_margin_pct')}% / "
                f"순이익률: {m.get('net_margin_pct')}%"
            )
            L.append(
                f"  - YoY 성장률 → 매출 {g.get('revenue')}% / "
                f"영업익 {g.get('operating_income')}% / 순익 {g.get('net_income')}%"
            )
            if fin_an.get("debt_to_equity_pct") is not None:
                L.append(f"  - 부채비율: {fin_an['debt_to_equity_pct']}%")

        # [3] 뉴스
        L.append("\n[3] 핵심 뉴스 (Gemini 선별)")
        if not picks:
            L.append("  - 수집된 뉴스 없음")
        elif picks[0].get("error"):
            L.append(f"  - 오류: {picks[0]['error']}")
        else:
            for i, p in enumerate(picks, 1):
                L.append(f"  {i}. [{p.get('impact','-')}] {p.get('title','')}")
                L.append(f"     사유: {p.get('reason','')}")
                if p.get("link"):
                    L.append(f"     링크: {p['link']}")
            L.append(
                f"  - 감성 스코어: {sent_score} "
                f"(긍 {sent_counts['긍정']} / 부 {sent_counts['부정']} / 중 {sent_counts['중립']})"
            )

        # [4] 관련주 추론
        L.append("\n[4] 추론된 관련주 후보 (Gemini 밸류체인 분석)")
        if not related:
            L.append("  - 추론 없음 (뉴스 부족)")
        elif related[0].get("error"):
            L.append(f"  - 오류: {related[0]['error']}")
        else:
            for i, c in enumerate(related, 1):
                tk = f" ({c['ticker']})" if c.get("ticker") else ""
                L.append(
                    f"  {i}. {c['name']}{tk}  "
                    f"[{c['value_chain']}/{c['expected_impact']}/신뢰도 {c['confidence']}]"
                )
                L.append(f"     근거: {c['reason']}")

        # [5] 투자 등급 자동 산출
        L.append("\n[5] 투자 등급 (파이썬 1차 판정)")
        ax = verdict["axis_scores"]
        L.append(
            f"  ▶ 종합 등급: 【{verdict['grade']}】  "
            f"(총점 {verdict['total_score']} = "
            f"가격 {ax['price']:+d} + 재무 {ax['financials']:+d} + 감성 {ax['sentiment']:+d})"
        )
        L.append("  - 근거(가격):")
        for r in verdict["rationale"]["price"]:
            L.append(f"     · {r}")
        L.append("  - 근거(재무):")
        for r in verdict["rationale"]["financials"]:
            L.append(f"     · {r}")
        L.append("  - 근거(감성):")
        for r in verdict["rationale"]["sentiment"]:
            L.append(f"     · {r}")

        # [6] 클로드 웹 정밀 분석 요청
        L.append("\n[6] 클로드 웹 정밀 분석 요청 템플릿")
        L.append(
            "  위 [1~5] 데이터를 바탕으로 다음을 정리해줘:\n"
            "  (1) 파이썬 1차 등급에 동의하는지, 반대라면 어떤 축의 가중치를 다르게 보는지.\n"
            "  (2) [4]번 관련주 후보 중 실제 수혜 강도가 가장 큰 Top3와 그 이유.\n"
            "  (3) 단기(1~2주) / 중기(1~3개월) 매매 시나리오 및 손절·익절 가이드.\n"
            "  (4) 본 종목이 흔들릴 수 있는 거시·산업 리스크 3가지."
        )
        L.append("=" * 60)
        return "\n".join(L)

    def save(self, report: str, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        return path


if __name__ == "__main__":
    from data_collector import StockDataCollector

    collector = StockDataCollector()
    bundle = collector.collect_all(
        ticker="005930.KS",
        news_query="삼성전자",
        stock_code="005930",
    )
    builder = ReportBuilder()
    report = builder.build(
        ticker="005930.KS",
        bundle=bundle,
        company_name="삼성전자",
        top_k_news=3,
        max_related=8,
    )
    print(report)
    out_path = os.path.join(os.path.dirname(__file__), "report_sample.txt")
    builder.save(report, out_path)
    print(f"\n저장됨: {out_path}")
