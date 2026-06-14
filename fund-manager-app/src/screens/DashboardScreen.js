import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  FlatList,
  RefreshControl,
  StyleSheet,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import StockCard from '../components/StockCard';
import { fetchHotStocks, syncFavoritesToServer } from '../services/api';
import {
  cacheHotStocks,
  getCachedHotStocks,
  getFavorites,
  addFavorite,
  removeFavorite,
} from '../services/database';

export default function DashboardScreen({ navigation }) {
  const [stocks, setStocks] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState(null);
  const [favSet, setFavSet] = useState(new Set());

  // 관심종목 Set 로드 + 서버 동기화
  const loadFavs = useCallback(async () => {
    try {
      const favs = await getFavorites();
      setFavSet(new Set(favs.map((f) => f.ticker)));
      // 앱 시작 시 서버에 관심종목 동기화 (워머가 사전 캐싱하도록)
      syncFavoritesToServer(favs);
    } catch {}
  }, []);

  const load = useCallback(async ({ pullToRefresh = false } = {}) => {
    if (pullToRefresh) setRefreshing(true);
    else setLoading(true);
    setError(null);

    try {
      const data = await fetchHotStocks();
      setStocks(data);
      setOffline(false);
      cacheHotStocks(data).catch((e) => console.warn('cache 실패', e));
    } catch (e) {
      console.warn('서버 통신 실패, 캐시 사용', e?.message);
      try {
        const cached = await getCachedHotStocks();
        if (cached.length > 0) {
          setStocks(cached);
          setOffline(true);
        } else {
          setError('서버에 연결할 수 없고, 저장된 데이터도 없습니다.');
        }
      } catch {
        setError('데이터를 불러오지 못했습니다.');
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load();
    loadFavs();
  }, [load, loadFavs]);

  // 관심 탭에서 돌아올 때 favSet 갱신
  useEffect(() => {
    const unsub = navigation.addListener('focus', loadFavs);
    return unsub;
  }, [navigation, loadFavs]);

  const handleFavoriteToggle = useCallback(async (stock) => {
    const ticker = stock.ticker;
    if (favSet.has(ticker)) {
      await removeFavorite(ticker);
      setFavSet((prev) => { const s = new Set(prev); s.delete(ticker); return s; });
    } else {
      await addFavorite(ticker, stock.name);
      setFavSet((prev) => new Set(prev).add(ticker));
    }
    // 서버에 관심종목 동기화 (워머 사전 캐싱용)
    const updatedFavs = await getFavorites();
    syncFavoritesToServer(updatedFavs);
  }, [favSet]);

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator size="large" color="#0F172A" />
        <Text style={styles.loadingText}>오늘의 핫 종목을 불러오는 중…</Text>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['left', 'right']}>
      {offline && (
        <View style={styles.offlineBanner}>
          <Text style={styles.offlineText}>
            오프라인 모드 — 마지막에 저장된 데이터를 보고 있습니다
          </Text>
        </View>
      )}
      {error && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      <FlatList
        data={stocks}
        keyExtractor={(item) => item.ticker}
        contentContainerStyle={{ paddingVertical: 8 }}
        renderItem={({ item }) => (
          <StockCard
            stock={item}
            isFavorited={favSet.has(item.ticker)}
            onFavoriteToggle={handleFavoriteToggle}
            onPress={() =>
              navigation.navigate('Detail', {
                ticker: item.ticker,
                name: item.name,
              })
            }
          />
        )}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => load({ pullToRefresh: true })}
            tintColor="#0F172A"
          />
        }
        ListEmptyComponent={
          !error ? (
            <View style={styles.center}>
              <Text style={styles.emptyText}>표시할 종목이 없습니다.</Text>
            </View>
          ) : null
        }
      />
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F1F5F9' },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 24,
  },
  loadingText: { marginTop: 12, color: '#475569' },
  emptyText: { color: '#64748B' },
  offlineBanner: {
    backgroundColor: '#FEF3C7',
    paddingHorizontal: 16,
    paddingVertical: 8,
  },
  offlineText: { color: '#92400E', fontSize: 12, fontWeight: '600' },
  errorBox: {
    margin: 16,
    padding: 12,
    backgroundColor: '#FEE2E2',
    borderRadius: 8,
  },
  errorText: { color: '#991B1B' },
});
