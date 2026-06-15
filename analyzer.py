import os
import json
import re
import requests
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DEFAULT_GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
DEFAULT_GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")


class GeminiClient:
    """LLM 호출 공통 베이스.
    GROQ_API_KEY 가 있으면 Groq (Llama) 사용 — 매우 빠르고 한도가 넉넉.
    없으면 Gemini fallback.
    클래스명은 호환성을 위해 유지 (safety.py 의 RateLimiter 등이 동작하도록)."""

    GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        # provider 자동 선택
        self.groq_key = os.getenv("GROQ_API_KEY")
        self.groq_model = os.getenv("GROQ_MODEL", DEFAULT_GROQ_MODEL)
        self.gemini_key = api_key or os.getenv("GEMINI_API_KEY")
        self.gemini_model = model or DEFAULT_GEMINI_MODEL
        self.provider = "groq" if self.groq_key else "gemini"
        # 하위 호환 — 기존 코드가 self.api_key, self.model 참조해도 동작
        self.api_key = self.groq_key if self.provider == "groq" else self.gemini_key
        self.model = self.groq_model if self.provider == "groq" else self.gemini_model

    @property
    def ENDPOINT(self) -> str:
        if self.provider == "groq":
            return self.GROQ_ENDPOINT
        return (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent"
        )

    def call_json(self, prompt: str, temperature: float = 0.2) -> Dict:
        if self.provider == "groq":
            return self._call_groq(prompt, temperature)
        return self._call_gemini(prompt, temperature)

    def _call_groq(self, prompt: str, temperature: float = 0.2) -> Dict:
        if not self.groq_key:
            return {"error": "missing GROQ_API_KEY"}
        import time as _t
        last_err = None
        for attempt in range(3):
            try:
                r = requests.post(
                    self.GROQ_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {self.groq_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.groq_model,
                        "messages": [
                            {"role": "user", "content": prompt + "\n\n반드시 JSON 객체만 출력."}
                        ],
                        "temperature": temperature,
                        "response_format": {"type": "json_object"},
                    },
                    timeout=30,
                )
                if r.status_code == 429 and attempt < 2:
                    _t.sleep(20)
                    continue
                r.raise_for_status()
                text = r.json()["choices"][0]["message"]["content"].strip()
                text = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text, flags=re.S)
                return json.loads(text)
            except Exception as e:
                last_err = e
                if attempt < 2:
                    _t.sleep(10)
                    continue
                break
        return {"error": f"groq call failed: {last_err}"}

    def _call_gemini(self, prompt: str, temperature: float = 0.2) -> Dict:
        if not self.gemini_key:
            return {"error": "missing GEMINI_API_KEY"}
        import time as _t
        last_err = None
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.gemini_model}:generateContent"
        )
        for attempt in range(3):
            try:
                r = requests.post(
                    endpoint,
                    params={"key": self.gemini_key},
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
                if r.status_code == 429 and attempt < 2:
                    _t.sleep(30)
                    continue
                r.raise_for_status()
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                text = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text, flags=re.S)
                return json.loads(text)
            except Exception as e:
                last_err = e
                if attempt < 2:
                    _t.sleep(15)
                    continue
                break
        return {"error": f"gemini call failed: {last_err}"}
 
 
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
            f"너는 한국 주식 투자 전문 애널리스트다. "
            f"아래 뉴스 중 투자자 관점에서 주가에 실질적 영향을 줄 핵심 뉴스 최대 {top_k}개를 골라라.\n\n"
            f"[선정 기준] 실적/가이던스 발표, 대규모 수주·계약, 규제·소송 결과, 신제품·기술 돌파구, M&A, 거시 충격 (금리·환율·정책).\n"
            f"단순 시황 요약·루머·중복·인사·행사 보도는 제외.\n\n"
            f"[impact 판단 기준 — 제목 단어가 아닌 투자자에게 미치는 실질 영향 기준]\n"
            f"  긍정: 실적 호조/상향, 대형 수주·계약 체결, 신시장 진출, 규제 완화, 경쟁사 대비 우위 확보\n"
            f"  부정: 실적 하회/하향, 주요 수주 취소·지연, 소송 패소, 시장 수요 하락, 핵심 기술 유출\n"
            f"  중립: 단순 사실 보도, 가능성·검토 단계, 영향 불분명, 긍정+부정 혼재\n"
            f"  ※ 제목에 '우려','경고','적신호' 등 부정적 단어가 있어도 "
            f"실질 내용이 대규모 투자·수주라면 긍정으로 판단할 것.\n\n"
            f'반드시 JSON만 출력: {{"picks":[{{"i":번호,"reason":"투자자 관점 한 줄 사유","impact":"긍정|부정|중립"}}]}}\n\n'
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
        # 신규 data_collector는 ratios/growth 를 미리 계산해서 넣어줌. 폴백도 유지.
        ratios = fin.get("ratios") or fin.get("margins") or {}
        growth = fin.get("growth") or {}

        def yoy(key: str) -> Optional[float]:
            cur = ind.get(key, {}).get("current")
            prev = ind.get(key, {}).get("previous")
            if cur and prev:
                return round((cur - prev) / abs(prev) * 100, 2)
            return None

        rev_yoy = growth.get("revenue_yoy_pct") if growth.get("revenue_yoy_pct") is not None else yoy("revenue")
        ni_yoy = growth.get("net_income_yoy_pct") if growth.get("net_income_yoy_pct") is not None else yoy("net_income")
        op_yoy = yoy("operating_income")

        debt_ratio = ratios.get("debt_to_equity_pct")
        if debt_ratio is None:
            equity = ind.get("total_equity", {}).get("current")
            liab = ind.get("total_liabilities", {}).get("current")
            debt_ratio = round(liab / equity * 100, 2) if equity and liab else None

        return {
            "year": fin.get("year"),
            "margins": {
                "operating_margin_pct": ratios.get("operating_margin_pct"),
                "net_margin_pct": ratios.get("net_margin_pct"),
            },
            "ratios": ratios,
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

    가격 ±4  + 재무 ±5 (PER/PBR/ROE 포함)  + 뉴스 ±3 = -12 ~ +12
    환각 방지를 위해 모든 분기 조건은 파이썬에서만 평가.
    """

    # 총점 범위: 가격±4 + 재무±5 + 뉴스±3 = -12 ~ +12
    GRADE_TABLE = [
        (7,  "적극 매수"),
        (3,  "매수"),
        (0,  "보유"),
        (-3, "관망"),
        (-99,"비중 축소"),
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
        return max(-4, min(4, s)), reasons

    @staticmethod
    def _score_financials(fin_an: Dict, market_metrics: Optional[Dict] = None) -> Tuple[int, List[str]]:
        """재무 점수 ±5.
        PER/PBR/ROE(valuation) + 영업이익률 + 매출YoY + 부채비율
        """
        if fin_an.get("error"):
            return 0, [f"재무 데이터 없음: {fin_an['error']}"]
        s, reasons = 0, []
        mm = market_metrics or {}

        # ── Valuation (PER / PBR / ROE) ──────────────────────────
        per = mm.get("per")
        if per is not None:
            try:
                p = float(per)
                if 0 < p <= 12:
                    s += 1; reasons.append(f"PER {p:.1f}배 저평가(+1)")
                elif p < 0:
                    s -= 1; reasons.append(f"PER {p:.1f}배 적자(-1)")
                elif p > 40:
                    s -= 1; reasons.append(f"PER {p:.1f}배 고평가(-1)")
            except (TypeError, ValueError):
                pass

        pbr = mm.get("pbr")
        if pbr is not None:
            try:
                b = float(pbr)
                if 0 < b < 1.0:
                    s += 1; reasons.append(f"PBR {b:.2f}배 청산가치 이하(+1)")
                elif b > 6.0:
                    s -= 1; reasons.append(f"PBR {b:.2f}배 과도 프리미엄(-1)")
            except (TypeError, ValueError):
                pass

        roe = mm.get("roe_pct")
        if roe is not None:
            try:
                r = float(roe)
                if r >= 15:
                    s += 1; reasons.append(f"ROE {r:.1f}% 우수(+1)")
                elif r < 0:
                    s -= 1; reasons.append(f"ROE {r:.1f}% 적자(-1)")
            except (TypeError, ValueError):
                pass

        # ── Profitability (영업이익률) ──────────────────────────
        m = fin_an.get("margins") or {}
        op_m = m.get("operating_margin_pct")
        if op_m is not None:
            if op_m >= 10:
                s += 1; reasons.append(f"영업이익률 {op_m}%(+1)")
            elif op_m <= 0:
                s -= 1; reasons.append(f"영업이익률 {op_m}%(-1)")

        # ── Growth (매출 YoY) ──────────────────────────
        g = fin_an.get("yoy_growth_pct") or {}
        rev_yoy = g.get("revenue")
        if rev_yoy is not None:
            if rev_yoy >= 15:
                s += 1; reasons.append(f"매출 YoY +{rev_yoy:.1f}%(+1)")
            elif rev_yoy <= -15:
                s -= 1; reasons.append(f"매출 YoY {rev_yoy:.1f}%(-1)")

        # ── Stability (부채비율) ──────────────────────────
        debt = fin_an.get("debt_to_equity_pct")
        if debt is not None:
            if debt >= 200:
                s -= 1; reasons.append(f"부채비율 {debt:.0f}% 과다(-1)")
            elif debt <= 80:
                s += 1; reasons.append(f"부채비율 {debt:.0f}% 양호(+1)")

        if not reasons:
            reasons.append("재무 데이터 부족 (0점)")
        return max(-5, min(5, s)), reasons

    @staticmethod
    def _score_sentiment(sent_score: float, counts: Dict[str, int]) -> Tuple[int, List[str]]:
        """뉴스 감성 점수 ±3 (재무 점수 강화로 가중치 조정)."""
        pos = counts.get("긍정", 0)
        neg = counts.get("부정", 0)
        neu = counts.get("중립", 0)
        total = pos + neg + neu
        s = 0
        reasons = []
        if total == 0:
            return 0, ["뉴스 없음 (0점)"]
        if sent_score >= 0.5:
            s = 3; reasons.append(f"뉴스 감성 강한 긍정({sent_score:+.2f}, +3)")
        elif sent_score >= 0.2:
            s = 2; reasons.append(f"뉴스 감성 긍정({sent_score:+.2f}, +2)")
        elif sent_score > -0.1:
            s = 1; reasons.append(f"뉴스 감성 중립/우호({sent_score:+.2f}, +1)")
        elif sent_score >= -0.3:
            s = 0; reasons.append(f"뉴스 감성 약한 부정({sent_score:+.2f}, 0)")
        elif sent_score >= -0.6:
            s = -2; reasons.append(f"뉴스 감성 부정({sent_score:+.2f}, -2)")
        else:
            s = -3; reasons.append(f"뉴스 감성 강한 부정({sent_score:+.2f}, -3)")
        reasons.append(f"뉴스 분포 → 긍정 {pos} / 부정 {neg} / 중립 {neu}")
        return s, reasons

    def grade(
        self,
        price_an: Dict,
        fin_an: Dict,
        sent_score: float,
        sent_counts: Dict[str, int],
        is_etf: bool = False,
        market_metrics: Optional[Dict] = None,
    ) -> Dict:
        ps, pr = self._score_price(price_an)
        if is_etf:
            fs, fr = 0, ["ETF — 재무지표 미적용 (가격·뉴스만 채점)"]
        else:
            fs, fr = self._score_financials(fin_an, market_metrics=market_metrics)
        ss, sr = self._score_sentiment(sent_score, sent_counts)
        total = ps + fs + ss
        grade = next(g for thr, g in self.GRADE_TABLE if total >= thr)
        return {
            "total_score": total,
            "grade": grade,
            "axis_scores": {"price": ps, "financials": fs, "sentiment": ss},
            "rationale": {"price": pr, "financials": fr, "sentiment": sr},
        }
 
 
# ─────────────────────────────────────────────
# 룰 기반 재무 해석문 — PER/PBR/ROE/부채비율 보고 자연어 한 줄씩
# ─────────────────────────────────────────────
def _interpret_financials(market_metrics, margins, growth, debt_pct):
    """재무 숫자 → 사용자 친화적 해석 문장 리스트.
    데이터 없으면 빈 리스트."""
    out = []
    mm = market_metrics or {}
    per = mm.get("per")
    pbr = mm.get("pbr")
    roe = mm.get("roe_pct")

    # PER
    if per is not None:
        try:
            p = float(per)
            if p < 0:
                out.append(f"PER {p:.1f}배 — 적자 상태로 PER 의미 제한적")
            elif p < 10:
                out.append(f"PER {p:.1f}배 — 시장 평균보다 저평가 (가치주 영역)")
            elif p < 20:
                out.append(f"PER {p:.1f}배 — 시장 평균 수준의 밸류에이션")
            elif p < 40:
                out.append(f"PER {p:.1f}배 — 성장주 영역, 실적 뒷받침 필요")
            else:
                out.append(f"PER {p:.1f}배 — 매우 고평가 구간, 신중한 접근 필요")
        except Exception:
            pass

    # PBR
    if pbr is not None:
        try:
            b = float(pbr)
            if b < 1.0:
                out.append(f"PBR {b:.2f}배 — 청산가치 이하 (저PBR, 가치 매력)")
            elif b < 2.0:
                out.append(f"PBR {b:.2f}배 — 무난한 자산 대비 가격")
            elif b < 5.0:
                out.append(f"PBR {b:.2f}배 — 자산 대비 다소 비싼 편")
            else:
                out.append(f"PBR {b:.2f}배 — 성장주/브랜드 프리미엄 반영")
        except Exception:
            pass

    # ROE
    if roe is not None:
        try:
            r = float(roe)
            if r < 0:
                out.append(f"ROE {r:.1f}% — 자기자본 손실 (적자 또는 자본 잠식 주의)")
            elif r < 5:
                out.append(f"ROE {r:.1f}% — 자본 수익성 낮음")
            elif r < 10:
                out.append(f"ROE {r:.1f}% — 평균 수준의 자본 효율")
            elif r < 20:
                out.append(f"ROE {r:.1f}% — 우수한 자본 효율")
            else:
                out.append(f"ROE {r:.1f}% — 매우 우수 (지속 가능성 확인 필요)")
        except Exception:
            pass

    # 영업이익률
    op = (margins or {}).get("operating_margin_pct")
    if op is not None:
        try:
            o = float(op)
            if o < 0:
                out.append(f"영업이익률 {o:.1f}% — 영업적자 상태")
            elif o < 5:
                out.append(f"영업이익률 {o:.1f}% — 마진 박한 수준")
            elif o < 15:
                out.append(f"영업이익률 {o:.1f}% — 적정 수익성")
            else:
                out.append(f"영업이익률 {o:.1f}% — 높은 수익성 (사업 경쟁력 강함)")
        except Exception:
            pass

    # 매출 성장률
    rg = (growth or {}).get("revenue")
    if rg is not None:
        try:
            v = float(rg)
            if v < -10:
                out.append(f"매출 {v:+.1f}% — 큰 폭 감소 (산업/제품 사이클 확인 필요)")
            elif v < 0:
                out.append(f"매출 {v:+.1f}% — 정체/약한 감소")
            elif v < 10:
                out.append(f"매출 {v:+.1f}% — 안정적 성장")
            elif v < 30:
                out.append(f"매출 {v:+.1f}% — 견조한 성장세")
            else:
                out.append(f"매출 {v:+.1f}% — 폭발적 성장 (지속성 확인)")
        except Exception:
            pass

    # 부채비율
    if debt_pct is not None:
        try:
            d = float(debt_pct)
            if d < 50:
                out.append(f"부채비율 {d:.1f}% — 매우 안정 (자기자본 충분)")
            elif d < 100:
                out.append(f"부채비율 {d:.1f}% — 양호한 재무 구조")
            elif d < 200:
                out.append(f"부채비율 {d:.1f}% — 다소 높음 (이자비용 부담 체크)")
            elif d < 400:
                out.append(f"부채비율 {d:.1f}% — 부채 많음 (위험도 ↑)")
            else:
                out.append(f"부채비율 {d:.1f}% — 매우 높음 (금융업 등 특수 업종은 정상)")
        except Exception:
            pass

    return out


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
 
    def _build_etf(self, ticker: str, bundle: Dict, company_name: Optional[str], top_k_news: int) -> str:
        """ETF 전용 텍스트 리포트. 재무제표 대신 수익률/AUM/구성종목 뉴스 중심."""
        price = bundle.get("price") or {}
        etf = bundle.get("etf_info") or {}
        news = bundle.get("news") or {}
        const_news = bundle.get("etf_constituent_news") or {}
        etf_items = news.get("items", []) if news else []
        const_items = const_news.get("items", []) if const_news else []

        price_an = self.sem.analyze_price(price)
        # ★ yfinance 실패 시 naver_price로 대체
        if price_an.get("error") and etf.get("naver_price"):
            _np = etf["naver_price"]
            _np_an = self.sem.analyze_price(_np)
            if not _np_an.get("error"):
                price_an = _np_an
                price = _np
        # Gemini 필터 → 실패 시 raw fallback
        etf_picks = self.gemini.filter_news(etf_items, top_k=top_k_news) if etf_items else []
        if not etf_picks or etf_picks[0].get("error"):
            etf_picks = [
                {"title": it.get("title", ""), "link": it.get("link"),
                 "pub_date": it.get("pub_date"), "impact": "중립", "reason": ""}
                for it in etf_items[:top_k_news] if it.get("title")
            ]
        const_picks = self.gemini.filter_news(const_items, top_k=top_k_news) if const_items else []
        if not const_picks or (const_picks and const_picks[0].get("error")):
            const_picks = [
                {"title": it.get("title", ""), "link": it.get("link"),
                 "pub_date": it.get("pub_date"), "impact": "중립", "reason": ""}
                for it in const_items[:top_k_news] if it.get("title")
            ]
        sent_score, sent_counts = self.gemini.sentiment_score(etf_picks + const_picks)
        verdict = self.grader.grade(price_an, {}, sent_score, sent_counts, is_etf=True)

        is_kr = etf.get("market") == "KR"
        L: List[str] = []
        L.append("=" * 60)
        L.append(f"📊 ETF 분석 리포트  |  {company_name or ticker} ({ticker})")
        L.append(f"생성: {datetime.now():%Y-%m-%d %H:%M}")
        L.append("=" * 60)

        # [1] ETF 기본 정보
        L.append("\n[1] ETF 기본 정보")
        L.append(f"  - ETF명: {company_name or ticker}")
        if etf.get("fund_family"):
            L.append(f"  - 운용사: {etf['fund_family']}")
        if etf.get("category"):
            L.append(f"  - 카테고리: {etf['category']}")
        if etf.get("total_assets_billion") is not None:
            aum = etf["total_assets_billion"]
            L.append(f"  - 순자산총액(AUM): {'%.1f억원' % aum if is_kr else '$%.2fB' % aum}")
        if etf.get("expense_ratio_pct") is not None:
            L.append(f"  - 운용보수(TER): {etf['expense_ratio_pct']}%")
        if etf.get("dividend_yield_pct") is not None:
            L.append(f"  - 배당수익률: {etf['dividend_yield_pct']}%")
        if etf.get("benchmark_index"):
            L.append(f"  - 기초지수: {etf['benchmark_index']}")
        if is_kr and etf.get("nav") is not None:
            L.append(f"  - NAV: {etf['nav']:,}원")
        if is_kr and etf.get("nav_diff_pct") is not None:
            L.append(f"  - 괴리율: {etf['nav_diff_pct']:+.2f}%")
        # 구성종목 (collect_all에서 파싱한 실제 데이터)
        constituents = etf.get("constituents") or []
        if constituents:
            L.append(f"  - 주요 구성종목: {', '.join(constituents)}")

        # [2] 수익률 성과
        L.append("\n[2] 수익률 성과")
        r1m = etf.get("return_1m")
        r3m = etf.get("return_3m")
        r6m = etf.get("return_6m")
        r1y = etf.get("return_1y") if is_kr else etf.get("return_ytd")
        r3y = etf.get("return_3y_ann")
        r5y = etf.get("return_5y_ann")
        def _pct(v): return f"{v:+.2f}%" if v is not None else "N/A (데이터 없음)"
        L.append(f"  - 1개월: {_pct(r1m)}")
        L.append(f"  - 3개월: {_pct(r3m)}")
        L.append(f"  - 6개월: {_pct(r6m)}")
        L.append(f"  - {'1년' if is_kr else 'YTD'}: {_pct(r1y)}")
        if r3y is not None:
            L.append(f"  - 3년 연평균: {_pct(r3y)}")
        if r5y is not None:
            L.append(f"  - 5년 연평균: {_pct(r5y)}")

        # [3] 가격 & 기술적 시그널
        L.append("\n[3] 가격 & 기술적 시그널")
        if price_an.get("error"):
            L.append(f"  - 오류: {price_an['error']}")
        else:
            L.append(f"  - 현재가: {price_an['current_price']:,} ({price_an.get('change_pct', 0):+.2f}%)")
            # 52주 고저 (ETF 자체 데이터 우선, 없으면 price_an)
            hi52 = etf.get("price_52w_high")
            lo52 = etf.get("price_52w_low")
            if hi52 and lo52:
                cur = price_an.get("current_price", 0)
                from_high = round((cur - hi52) / hi52 * 100, 1) if hi52 else None
                from_low  = round((cur - lo52)  / lo52  * 100, 1) if lo52 else None
                L.append(f"  - 52주 최고: {hi52:,}원  최저: {lo52:,}원")
                if from_high is not None:
                    L.append(f"  - 고점 대비: {from_high:+.1f}%  /  저점 대비: {from_low:+.1f}%")
            elif price_an.get("position_52w_pct") is not None:
                L.append(f"  - 52주 위치: {price_an['position_52w_pct']}%")
            if etf.get("avg_volume_20d"):
                L.append(f"  - 20일 평균거래량: {int(etf['avg_volume_20d']):,}주")
            if price_an.get("momentum_10d_pct") is not None:
                L.append(f"  - 10일 모멘텀: {price_an['momentum_10d_pct']:+.2f}%")
            for s in price_an.get("signals", []):
                L.append(f"  - 시그널: {s}")

        # [3-1] 물타기 / 불타기 점수
        ws = etf.get("water_score")
        fs = etf.get("fire_score")
        if ws is not None or fs is not None:
            L.append("\n[3-1] 매수 타이밍 점수 (0~100)")
            if ws is not None:
                L.append(f"  - 💧 물타기(저점 매수) 점수: {ws}/100")
                for r in (etf.get("water_reasons") or []):
                    L.append(f"     · {r}")
            if fs is not None:
                L.append(f"  - 🔥 불타기(모멘텀 추가) 점수: {fs}/100")
                for r in (etf.get("fire_reasons") or []):
                    L.append(f"     · {r}")

        # [4] ETF 자체 뉴스
        L.append("\n[4] ETF 관련 뉴스 (Gemini 선별)")
        if not etf_picks or etf_picks[0].get("error"):
            L.append("  - 수집된 뉴스 없음")
        else:
            for i, p in enumerate(etf_picks, 1):
                L.append(f"  {i}. [{p.get('impact','-')}] {p.get('title','')}")
                if p.get("reason"):
                    L.append(f"     사유: {p['reason']}")

        # [5] 구성종목 뉴스
        L.append("\n[5] 구성종목 핵심 뉴스 (Gemini 선별)")
        if not const_picks or const_picks[0].get("error"):
            L.append("  - 구성종목 뉴스 없음")
        else:
            for i, p in enumerate(const_picks, 1):
                L.append(f"  {i}. [{p.get('impact','-')}] {p.get('title','')}")
                if p.get("reason"):
                    L.append(f"     사유: {p['reason']}")

        # [6] 투자 의견
        L.append("\n[6] 투자 의견 (가격 모멘텀 + 뉴스 감성)")
        ax = verdict["axis_scores"]
        L.append(
            f"  ▶ 종합 등급: 【{verdict['grade']}】  "
            f"(총점 {verdict['total_score']} = 가격 {ax['price']:+d} + 감성 {ax['sentiment']:+d})"
        )
        for r in verdict["rationale"]["price"]:
            L.append(f"     · {r}")
        for r in verdict["rationale"]["sentiment"]:
            L.append(f"     · {r}")

        # [7] Claude 웹 분석 요청
        L.append("\n[7] Claude 웹 정밀 분석 요청 템플릿")
        L.append(
            "  위 [1~6] 데이터를 바탕으로 다음을 정리해줘:\n"
            "  (1) 이 ETF의 테마/섹터 현재 모멘텀 판단 (긍정/부정/중립 + 이유).\n"
            "  (2) [4][5]번 뉴스들이 ETF 수익률에 미칠 단기/중기 영향.\n"
            "  (3) 유사 ETF 대비 AUM·수익률 경쟁력 (알고 있는 범위에서).\n"
            "  (4) 구성종목 집중도 리스크 — 상위 종목 비중 쏠림 위험.\n"
            "  (5) 단기(1~2주) / 중기(1~3개월) 매매 시나리오.\n"
            "  (6) 이 ETF 테마가 흔들릴 수 있는 거시·정책 리스크 3가지."
        )
        L.append("=" * 60)
        return "\n".join(L)

    def build(
        self,
        ticker: str,
        bundle: Dict,
        company_name: Optional[str] = None,
        top_k_news: int = 3,
        max_related: int = 8,
    ) -> str:
        # ETF면 전용 리포트 사용
        if bundle.get("etf_info"):
            return self._build_etf(ticker, bundle, company_name, top_k_news)

        price = bundle.get("price") or {}
        news = bundle.get("news") or {}
        fin = bundle.get("financials") or {}
        items = news.get("items", []) if news else []
 
        price_an = self.sem.analyze_price(price)
        fin_an = self.sem.analyze_financials(fin)
        picks = self.gemini.filter_news(items, top_k=top_k_news) if items else []
        sent_score, sent_counts = self.gemini.sentiment_score(picks)
        related = self.related.infer_related_stocks(items, max_candidates=max_related) if items else []
        market_metrics = bundle.get("market_metrics") or {}
        verdict = self.grader.grade(
            price_an, fin_an, sent_score, sent_counts,
            market_metrics=market_metrics,
        )
 
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

            # ★ 룰 기반 재무 해석 한 단락 추가 — 사용자가 숫자만 보고 헷갈리지 않게
            interp = _interpret_financials(
                market_metrics=(bundle or {}).get("market_metrics") or {},
                margins=m,
                growth=g,
                debt_pct=fin_an.get("debt_to_equity_pct"),
            )
            if interp:
                L.append("  - 해석:")
                for line in interp:
                    L.append(f"     · {line}")
 
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
            "  (2) [3]번 뉴스 링크들을 각각 열어 읽고, 종목 주가에 미칠 단기/중기 영향도\n"
            "      (강/중/약 + 호재/악재) 와 핵심 메시지 한 줄을 뉴스마다 정리.\n"
            "  (3) [4]번 관련주 후보 중 실제 수혜 강도가 가장 큰 Top3와 그 이유.\n"
            "  (4) [2]번 재무 지표를 동종 업계 평균과 비교해 강점/약점 2개씩.\n"
            "  (5) 단기(1~2주) / 중기(1~3개월) 매매 시나리오 및 손절·익절 가이드.\n"
            "  (6) 본 종목이 흔들릴 수 있는 거시·산업 리스크 3가지."
        )
        L.append("=" * 60)
        return "\n".join(L)
 
    def save(self, report: str, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            f.write(report)
        return path
 
 
# ─────────────────────────────────────────────
# 모드 1: HybridAnalyzer
#   ReportBuilder 결과 + Gemini 자동 종합 판정
#   importance >= threshold 면 ManualClaudeAnalyzer 프롬프트 자동 첨부
# ─────────────────────────────────────────────
class HybridAnalyzer:
    def __init__(
        self,
        builder: Optional[ReportBuilder] = None,
        gemini: Optional[GeminiClient] = None,
        claude_threshold: float = 7.0,
    ):
        self.builder = builder or ReportBuilder()
        self.gemini = gemini or GeminiClient()
        self.claude_threshold = claude_threshold

    def analyze(
        self,
        ticker: str,
        bundle: Dict,
        company_name: Optional[str] = None,
    ) -> Dict:
        report_text = self.builder.build(
            ticker=ticker, bundle=bundle, company_name=company_name,
        )
        prompt = (
            "아래 종목 리포트를 분석해 한국어 JSON 객체만 출력해. 다른 텍스트/마크다운 금지.\n\n"
            "JSON 스키마:\n"
            "{\n"
            '  "importance": 0~10 정수,\n'
            '  "sentiment": "bullish"|"bearish"|"neutral",\n'
            '  "action": "BUY"|"HOLD"|"WATCH"|"AVOID",\n'
            '  "confidence": 0~10 정수,\n'
            '  "key_drivers": ["3개, 각 30자 이내"],\n'
            '  "risk_factors": ["2~3개, 각 30자 이내"],\n'
            '  "short_term": "1주~1개월 전망 (60자 이내)",\n'
            '  "mid_term": "3~6개월 전망 (60자 이내)",\n'
            '  "comment": "종합 코멘트 (200자 이내)"\n'
            "}\n\n"
            f"[리포트]\n{report_text}"
        )
        verdict = self.gemini.call_json(prompt, temperature=0.3)
        verdict["mode"] = "hybrid_gemini"
        verdict["model"] = self.gemini.model
        verdict["ticker"] = ticker
        verdict["name"] = company_name
        verdict["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        verdict["report_text"] = report_text

        try:
            importance = float(verdict.get("importance", 0))
        except (TypeError, ValueError):
            importance = 0.0

        if importance >= self.claude_threshold:
            verdict["needs_claude_review"] = True
            verdict["claude_prompt"] = ManualClaudeAnalyzer.build_prompt(
                report_text=report_text,
                ticker=ticker,
                company_name=company_name,
                extra_context=(
                    "[Gemini 1차 자동 판정 — 너가 검증·심화할 베이스]\n"
                    + json.dumps(
                        {k: v for k, v in verdict.items()
                         if k not in ("claude_prompt", "report_text")},
                        ensure_ascii=False, indent=2, default=str,
                    )
                ),
            )
        else:
            verdict["needs_claude_review"] = False
        return verdict


# ─────────────────────────────────────────────
# 모드 2: ManualClaudeAnalyzer
#   Gemini 종합 판정 호출 안 함. 클로드용 프롬프트만 만들어줌.
# ─────────────────────────────────────────────
class ManualClaudeAnalyzer:
    def __init__(self, builder: Optional[ReportBuilder] = None):
        self.builder = builder or ReportBuilder()

    def make_paste(
        self,
        ticker: str,
        bundle: Dict,
        company_name: Optional[str] = None,
    ) -> Dict:
        report_text = self.builder.build(
            ticker=ticker, bundle=bundle, company_name=company_name,
        )
        prompt = self.build_prompt(report_text, ticker, company_name)
        return {
            "mode": "manual_claude",
            "ticker": ticker,
            "name": company_name,
            "claude_prompt": prompt,
            "report_text": report_text,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    def build_prompt(
        report_text: str,
        ticker: str = "",
        company_name: Optional[str] = None,
        extra_context: str = "",
    ) -> str:
        head = f"너는 한국/미국 주식 펀드매니저야. 아래 종목 리포트를 정밀 분석해 JSON 객체만 출력해.\n\n"
        head += f"[종목] {company_name or ''} ({ticker})\n\n"
        head += f"[리포트]\n{report_text}\n"
        if extra_context:
            head += f"\n{extra_context}\n"
        head += (
            "\n[출력 — JSON 객체 하나만, 마크다운 코드펜스 금지]\n"
            "{\n"
            '  "importance": 0~10 정수,\n'
            '  "sentiment": "bullish"|"bearish"|"neutral",\n'
            '  "key_drivers": ["핵심 동인 3개, 각 30자 이내"],\n'
            '  "risk_factors": ["리스크 2~3개, 각 30자 이내"],\n'
            '  "valuation_judgment": "현재 밸류에이션 판단 (60자 이내)",\n'
            '  "fundamental_view": "펀더멘털 종합 의견 (100자 이내)",\n'
            '  "short_term": "1주~1개월 전망 (80자 이내)",\n'
            '  "mid_term": "3~6개월 전망 (80자 이내)",\n'
            '  "action": "BUY"|"HOLD"|"WATCH"|"AVOID",\n'
            '  "target_buy_zone": "예: 70000~73000",\n'
            '  "stop_loss": "손절 라인 가격",\n'
            '  "confidence": 0~10 정수,\n'
            '  "reasoning": "결론 도출 근거 (200자 이내)"\n'
            "}\n"
        )
        return head

    @staticmethod
    def parse_response(claude_response: str) -> Dict:
        text = (claude_response or "").strip()
        if "```" in text:
            for p in text.split("```"):
                p = p.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith("{"):
                    text = p
                    break
        s, e = text.find("{"), text.rfind("}")
        if s >= 0 and e > s:
            text = text[s:e + 1]
        try:
            result = json.loads(text)
        except json.JSONDecodeError as ex:
            return {"error": f"json parse failed: {ex}", "raw": text[:500]}
        result["mode"] = "manual_claude"
        result["analyzed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return result


# ─────────────────────────────────────────────
# CLI 데모
#   python analyzer.py hybrid 005930.KS 삼성전자 005930
#   python analyzer.py manual 005930.KS 삼성전자 005930
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    from data_collector import StockDataCollector

    mode = sys.argv[1] if len(sys.argv) > 1 else "manual"
    ticker = sys.argv[2] if len(sys.argv) > 2 else "005930.KS"
    name = sys.argv[3] if len(sys.argv) > 3 else "삼성전자"
    code = sys.argv[4] if len(sys.argv) > 4 else "005930"

    print(f"[데이터 수집] {ticker} / {name} / {code}")
    collector = StockDataCollector()
    bundle = collector.collect_all(ticker=ticker, news_query=name, stock_code=code)

    if mode == "hybrid":
        print("\n[모드 1: Hybrid — Gemini 자동 종합]")
        result = HybridAnalyzer().analyze(ticker=ticker, bundle=bundle, company_name=name)
        compact = {k: v for k, v in result.items() if k not in ("claude_prompt", "report_text")}
        print(json.dumps(compact, ensure_ascii=False, indent=2, default=str))
        if result.get("needs_claude_review"):
            print("\n" + "=" * 70)
            print(f"⚠️  importance={result.get('importance')} → 아래를 클로드창에 붙여넣어 정밀 분석:")
            print("=" * 70)
            print(result["claude_prompt"])
        out = os.path.join(os.path.dirname(__file__), "report_sample.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(result["report_text"])
        print(f"\n[저장] {out}")

    elif mode == "manual":
        print("\n[모드 2: Manual Claude — 프롬프트 생성]")
        out = ManualClaudeAnalyzer().make_paste(ticker=ticker, bundle=bundle, company_name=name)
        print("\n" + "=" * 70)
        print("아래를 통째로 복사해 클로드창에 붙여넣으세요:")
        print("=" * 70)
        print(out["claude_prompt"])
        path = os.path.join(os.path.dirname(__file__), "claude_prompt.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(out["claude_prompt"])
        print(f"\n[저장] {path}  ← 파일로도 저장됨 (메모장에서 바로 열어 복사 가능)")
        print("\n클로드 답변 받으면:")
        print("  >>> from analyzer import ManualClaudeAnalyzer")
        print("  >>> ManualClaudeAnalyzer.parse_response(클로드답변문자열)")

    else:
        print(f"[ERROR] mode는 hybrid 또는 manual 만 가능 (입력: {mode})")