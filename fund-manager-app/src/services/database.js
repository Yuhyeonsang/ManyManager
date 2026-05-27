// SQLite 로컬 캐시 (expo-sqlite v16)
import * as SQLite from 'expo-sqlite';

// Promise 싱글톤 — 동시 다중 호출 시 DB가 두 번 초기화되는 race condition 방지
let dbPromise = null;

function getDB() {
  if (!dbPromise) {
    dbPromise = SQLite.openDatabaseAsync('fundmanager.db').then(async (db) => {
      await db.execAsync(
        'PRAGMA journal_mode = WAL;' +
        'CREATE TABLE IF NOT EXISTS hot_stocks (' +
          'ticker TEXT PRIMARY KEY,' +
          'payload TEXT NOT NULL,' +
          'cached_at INTEGER NOT NULL' +
        ');' +
        'CREATE TABLE IF NOT EXISTS reports (' +
          'ticker TEXT PRIMARY KEY,' +
          'payload TEXT NOT NULL,' +
          'cached_at INTEGER NOT NULL' +
        ');' +
        'CREATE TABLE IF NOT EXISTS settings (' +
          'key TEXT PRIMARY KEY,' +
          'value TEXT NOT NULL' +
        ');' +
        'CREATE TABLE IF NOT EXISTS favorites (' +
          'ticker TEXT PRIMARY KEY,' +
          'name TEXT NOT NULL,' +
          'added_at INTEGER NOT NULL' +
        ');'
      );
      return db;
    }).catch((e) => {
      // 초기화 실패 시 다음 호출에서 재시도할 수 있도록 초기화
      dbPromise = null;
      throw e;
    });
  }
  return dbPromise;
}

// ─── 핫 종목 캐시 ───────────────────────────────────

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

export async function getCachedHotStocks() {
  const db = await getDB();
  const rows = await db.getAllAsync(
    'SELECT payload FROM hot_stocks ORDER BY cached_at DESC;'
  );
  return rows.map((r) => JSON.parse(r.payload));
}

// ─── 리포트 캐시 ────────────────────────────────────

export async function cacheReport(report) {
  const db = await getDB();
  await db.runAsync(
    'INSERT OR REPLACE INTO reports (ticker, payload, cached_at) VALUES (?, ?, ?);',
    [report.ticker, JSON.stringify(report), Date.now()]
  );
}

export async function getCachedReport(ticker) {
  const db = await getDB();
  const row = await db.getFirstAsync(
    'SELECT payload, cached_at FROM reports WHERE ticker = ?;',
    [ticker]
  );
  if (!row) return null;
  return { ...JSON.parse(row.payload), _cachedAt: row.cached_at };
}

export async function clearCache() {
  const db = await getDB();
  await db.execAsync('DELETE FROM hot_stocks; DELETE FROM reports;');
}

// ─── 설정값 ─────────────────────────────────────────

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

export async function setSetting(key, value) {
  const db = await getDB();
  await db.runAsync(
    'INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?);',
    [key, String(value)]
  );
}

export async function deleteSetting(key) {
  const db = await getDB();
  await db.runAsync('DELETE FROM settings WHERE key = ?;', [key]);
}

// ─── 관심종목 ────────────────────────────────────────

export async function addFavorite(ticker, name) {
  const db = await getDB();
  await db.runAsync(
    'INSERT OR REPLACE INTO favorites (ticker, name, added_at) VALUES (?, ?, ?);',
    [ticker, name, Date.now()]
  );
}

export async function removeFavorite(ticker) {
  const db = await getDB();
  await db.runAsync('DELETE FROM favorites WHERE ticker = ?;', [ticker]);
}

export async function getFavorites() {
  const db = await getDB();
  const rows = await db.getAllAsync(
    'SELECT ticker, name, added_at FROM favorites ORDER BY added_at DESC;'
  );
  return rows;
}

export async function isFavorite(ticker) {
  const db = await getDB();
  const row = await db.getFirstAsync(
    'SELECT ticker FROM favorites WHERE ticker = ?;',
    [ticker]
  );
  return !!row;
}
