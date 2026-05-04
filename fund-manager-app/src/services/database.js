// SQLite 로컬 캐시 — 인터넷이 끊겨도 마지막에 받아온 리포트를 볼 수 있게 함
// expo-sqlite v14 의 신규 비동기 API 사용

import * as SQLite from 'expo-sqlite';

let dbInstance = null;

async function getDB() {
  if (!dbInstance) {
    dbInstance = await SQLite.openDatabaseAsync('fundmanager.db');
    await dbInstance.execAsync(`
      PRAGMA journal_mode = WAL;
      CREATE TABLE IF NOT EXISTS hot_stocks (
        ticker TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        cached_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS reports (
        ticker TEXT PRIMARY KEY,
        payload TEXT NOT NULL,
        cached_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
      );
    `);
  }
  return dbInstance;
}

/**
 * 핫 종목 리스트 캐시 저장
 */
export async function cacheHotStocks(stocks) {
  const db = await getDB();
  const now = Date.now();
  await db.withTransactionAsync(async () => {
    await db.runAsync('DELETE FROM hot_stocks;');
    for (const s of stocks) {
      await db.runAsync(
        'INSERT OR REPLACE INTO hot_stocks (ticker, payload, cached_at) VALUES (?, ?, ?);',
        [s.ticker, JSON.stringify(s), now]
      );
    }
  });
}

/**
 * 캐시된 핫 종목 리스트 가져오기 (오프라인 폴백용)
 */
export async function getCachedHotStocks() {
  const db = await getDB();
  const rows = await db.getAllAsync(
    'SELECT payload FROM hot_stocks ORDER BY cached_at DESC;'
  );
  return rows.map((r) => JSON.parse(r.payload));
}

/**
 * 종목 상세 리포트 캐시 저장
 */
export async function cacheReport(report) {
  const db = await getDB();
  await db.runAsync(
    'INSERT OR REPLACE INTO reports (ticker, payload, cached_at) VALUES (?, ?, ?);',
    [report.ticker, JSON.stringify(report), Date.now()]
  );
}

/**
 * 캐시된 리포트 가져오기
 */
export async function getCachedReport(ticker) {
  const db = await getDB();
  const row = await db.getFirstAsync(
    'SELECT payload, cached_at FROM reports WHERE ticker = ?;',
    [ticker]
  );
  if (!row) return null;
  return { ...JSON.parse(row.payload), _cachedAt: row.cached_at };
}

/**
 * 캐시 전체 비우기 (디버깅용)
 */
export async function clearCache() {
  const db = await getDB();
  await db.execAsync('DELETE FROM hot_stocks; DELETE FROM reports;');
}

// ─────────────────────────────────────────────
// 설정값 (key-value) 영구 저장
//   - base_url 같은 사용자 설정을 .apk 재빌드 없이 바꿀 수 있게 함
// ─────────────────────────────────────────────

/**
 * 설정값 가져오기 (없으면 defaultValue 반환)
 */
export async function getSetting(key, defaultValue = null) {
  try {
    const db = await getDB();
    const row = await db.getFirstAsync(
      'SELECT value FROM settings WHERE key = ?;',
      [key]
    );
    return row ? row.value : defaultValue;
  } catch (e) {
    console.warn('getSetting 실패', e?.message);
    return defaultValue;
  }
}

/**
 * 설정값 저장
 */
export async function setSetting(key, value) {
  const db = await getDB();
  await db.runAsync(
    'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);',
    [key, String(value)]
  );
}

/**
 * 설정값 삭제 (= 기본값으로 복원)
 */
export async function deleteSetting(key) {
  const db = await getDB();
  await db.runAsync('DELETE FROM settings WHERE key = ?;', [key]);
}
