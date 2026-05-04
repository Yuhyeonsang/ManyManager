// FastAPI ?�버 ?�신 ?�이??// ?�️ 중요: 갤럭??S24?�서 ?�행???�는 PC??IP 주소�??�어????
// (localhost ???�드???�기 ?�신??가리키므�??�동 ????
//
// 1) PC?�서 cmd ?�고 `ipconfig` ?�행 ??IPv4 주소 ?�인 (?? 192.168.0.10)
// 2) ?�래 BASE_URL ??192.168.0.10 부분을 본인 IP�?교체
// 3) PC?� ?�드?�이 같�? ?�?�파?�에 ?�결?�어 ?�어????// 4) ?�이???�버??`uvicorn main:app --host 0.0.0.0 --port 8000` ?�로 ?�행

import axios from 'axios';
import { getSetting, setSetting, deleteSetting } from './database';

// 기본 BASE_URL — SQLite 의 settings.base_url 이 비어있을 때 사용되는 fallback.
// 이 값은 .apk 안에 박혀있지만, 사용자가 앱의 "설정" 화면에서 다른 URL 로
// 덮어쓰면 그 값이 우선 적용됩니다 (재빌드 없이 변경 가능).
export const BASE_URL = 'http://192.168.0.4:8000';

const SETTINGS_KEY_BASE_URL = 'base_url';

let _client = null;
let _clientUrl = null;

/**
 * 현재 사용 중인 BASE_URL 가져오기
 *   1) SQLite 의 저장된 값
 *   2) 위 BASE_URL 상수 (fallback)
 */
export async function getEffectiveBaseURL() {
  const saved = await getSetting(SETTINGS_KEY_BASE_URL, null);
  return (saved && saved.trim()) ? saved.trim() : BASE_URL;
}

/**
 * 사용자가 설정 화면에서 BASE_URL 을 바꿀 때 호출
 */
export async function updateBaseURL(newUrl) {
  const trimmed = (newUrl || '').trim().replace(/\/+$/, '');
  if (!trimmed) {
    await deleteSetting(SETTINGS_KEY_BASE_URL);   // 빈 값 → 기본값으로 복원
  } else {
    await setSetting(SETTINGS_KEY_BASE_URL, trimmed);
  }
  _client = null;
  _clientUrl = null;
}

/**
 * URL 캐시 무효화 (다음 호출 시 새 client 생성)
 */
export function resetClientCache() {
  _client = null;
  _clientUrl = null;
}

/**
 * 매 요청마다 호출 — URL 이 바뀌지 않았으면 캐시된 axios 재사용
 */
async function getClient() {
  const url = await getEffectiveBaseURL();
  if (_client && _clientUrl === url) return _client;
  _clientUrl = url;
  _client = axios.create({
    baseURL: url,
    // 60초 — hot-stocks 첫 호출은 yfinance + 네이버 + Gemini 동시 호출이라 30~60초 걸림
    timeout: 60000,
    headers: {
      'Content-Type': 'application/json',
      // Cloudflare/ngrok 무료 터널의 경고 페이지 우회용
      'ngrok-skip-browser-warning': 'true',
    },
  });
  return _client;
}

/**
 * 현재 BASE_URL 로 헬스체크 (설정 화면의 "연결 테스트" 버튼용)
 */
export async function pingServer() {
  const c = await getClient();
  const t0 = Date.now();
  await c.get('/', { timeout: 8000 });
  return Date.now() - t0;   // 응답 ms
}

/**
 * ?�늘????종목 리스??가?�오�? * ?�버 ?�드?�인???�시: GET /api/hot-stocks
 * ?�답 ?�태:
 * [
 *   {
 *     "ticker": "005930",
 *     "name": "?�성?�자",
 *     "price": 78500,
 *     "change_pct": 2.34,
 *     "grade": "BUY",          // "BUY" | "HOLD" | "SELL"
 *     "score": 87,
 *     "summary": "??�??�약..."
 *   }
 * ]
 */
export async function fetchHotStocks() {
  const c = await getClient();
  const { data } = await c.get('/api/hot-stocks');
  return data;
}

/**
 * ?�정 종목 ?�세 리포??가?�오�? * ?�버 ?�드?�인???�시: GET /api/stocks/{ticker}/report
 * ?�답 ?�태:
 * {
 *   "ticker": "005930",
 *   "name": "?�성?�자",
 *   "grade": "BUY",
 *   "score": 87,
 *   "news_summary": "AI가 ?�리???�스 ?�약...",
 *   "financials": {
 *     "per": 12.3,
 *     "pbr": 1.4,
 *     "roe": 11.2,
 *     "revenue_growth": 8.7,
 *     "operating_margin": 15.3,
 *     "debt_ratio": 28.5
 *   },
 *   "updated_at": "2026-05-03T09:30:00"
 * }
 */
export async function fetchStockReport(ticker) {
  const c = await getClient();
  const { data } = await c.get(`/api/stocks/${ticker}/report`);
  return data;
}

/**
 * ?�립보드 복사???�스??리포??가?�오�? * ?�버 ?�드?�인???�시: GET /api/stocks/{ticker}/clipboard
 * ?�답 ?�태:
 * { "text": "?�리???�스??리포??.." }
 *
 * ?�버?????�드?�인?��? ?�으�??�라?�언??측에???�백 ?�맷??formatReportForClipboard)???�용??
 */
export async function fetchClipboardText(ticker) {
  const c = await getClient();
  const { data } = await c.get(`/api/stocks/${ticker}/clipboard`);
  return data.text;
}

/**
 * 종목 검색 (회사명 또는 종목코드/티커)
 * GET /api/search?q={query}&limit=20
 * 응답:
 * [
 *   { "code": "005930", "name": "삼성전자", "market": "KOSPI",
 *     "ticker": "005930.KS", "region": "KR" },
 *   { "code": "AAPL",   "name": "Apple",   "market": "NASDAQ",
 *     "ticker": "AAPL",      "region": "US" }
 * ]
 */
export async function searchStocks(query, limit = 20) {
  if (!query || !query.trim()) return [];
  const c = await getClient();
  const { data } = await c.get('/api/search', {
    params: { q: query.trim(), limit },
  });
  return data;
}
