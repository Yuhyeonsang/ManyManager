import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  TextInput,
  FlatList,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  Keyboard,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { searchStocks } from '../services/api';

export default function SearchScreen({ navigation }) {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const debounceRef = useRef(null);

  // debounced search
  const triggerSearch = useCallback((q) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!q || q.trim().length < 1) {
      setResults([]);
      setError(null);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await searchStocks(q, 30);
        setResults(data);
      } catch (e) {
        console.warn('search failed', e?.message);
        setError('검색에 실패했어요. 서버 연결을 확인해 주세요.');
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 300);
  }, []);

  useEffect(() => {
    triggerSearch(query);
    return () => debounceRef.current && clearTimeout(debounceRef.current);
  }, [query, triggerSearch]);

  const goDetail = (item) => {
    Keyboard.dismiss();
    // 상세 화면은 코드/티커 둘 다 받을 수 있음
    const idForApi = item.region === 'KR' ? item.code : item.ticker;
    navigation.navigate('Detail', { ticker: idForApi, name: item.name });
  };

  return (
    <SafeAreaView style={styles.container} edges={['left', 'right']}>
      <View style={styles.searchBox}>
        <TextInput
          autoFocus
          value={query}
          onChangeText={setQuery}
          placeholder="회사명 또는 종목코드 (예: 삼성, 005930, AAPL)"
          placeholderTextColor="#94A3B8"
          style={styles.input}
          autoCorrect={false}
          autoCapitalize="characters"
          returnKeyType="search"
        />
        {query.length > 0 && (
          <Pressable onPress={() => setQuery('')} hitSlop={10}>
            <Text style={styles.clear}>✕</Text>
          </Pressable>
        )}
      </View>

      {loading && (
        <View style={styles.center}>
          <ActivityIndicator size="small" color="#0F172A" />
        </View>
      )}

      {error && !loading && (
        <View style={styles.errorBox}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      {!loading && !error && query.length === 0 && (
        <View style={styles.hintBox}>
          <Text style={styles.hintTitle}>🔍 검색 팁</Text>
          <Text style={styles.hintBody}>
            • 한국 종목: 회사명 일부 또는 6자리 코드 (삼성전자, 005930){'\n'}
            • 미국 종목: 영문명 또는 티커 (Apple, AAPL){'\n'}
            • ETF / 코스닥 중소형주도 검색 가능
          </Text>
        </View>
      )}

      <FlatList
        data={results}
        keyExtractor={(item) => `${item.region}-${item.code}`}
        keyboardShouldPersistTaps="handled"
        renderItem={({ item }) => (
          <Pressable
            onPress={() => goDetail(item)}
            style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
          >
            <View style={{ flex: 1 }}>
              <View style={styles.rowTopLine}>
                <Text style={styles.name}>{item.name}</Text>
                <RegionBadge region={item.region} market={item.market} />
              </View>
              <Text style={styles.ticker}>
                {item.code}
                {item.market ? `  ·  ${item.market}` : ''}
              </Text>
            </View>
            <Text style={styles.chevron}>›</Text>
          </Pressable>
        )}
        ListEmptyComponent={
          !loading && query.length > 0 && !error ? (
            <View style={styles.center}>
              <Text style={styles.emptyText}>
                "{query}" 검색 결과가 없어요
              </Text>
            </View>
          ) : null
        }
      />
    </SafeAreaView>
  );
}

function RegionBadge({ region, market }) {
  const isKR = region === 'KR';
  return (
    <View
      style={[
        styles.badge,
        { backgroundColor: isKR ? '#DBEAFE' : '#FEF3C7' },
      ]}
    >
      <Text
        style={[
          styles.badgeText,
          { color: isKR ? '#1E40AF' : '#92400E' },
        ]}
      >
        {market || (isKR ? 'KR' : 'US')}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F1F5F9' },
  searchBox: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#FFFFFF',
    margin: 12,
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 4,
    shadowColor: '#000',
    shadowOpacity: 0.05,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 1 },
    elevation: 1,
  },
  input: {
    flex: 1,
    fontSize: 16,
    color: '#0F172A',
    paddingVertical: 12,
  },
  clear: { color: '#94A3B8', fontSize: 18, paddingHorizontal: 6 },
  center: { padding: 24, alignItems: 'center' },
  emptyText: { color: '#64748B' },
  errorBox: {
    margin: 16,
    padding: 12,
    backgroundColor: '#FEE2E2',
    borderRadius: 8,
  },
  errorText: { color: '#991B1B' },
  hintBox: {
    margin: 16,
    padding: 16,
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
  },
  hintTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#0F172A',
    marginBottom: 6,
  },
  hintBody: { color: '#475569', fontSize: 13, lineHeight: 20 },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    paddingHorizontal: 16,
    backgroundColor: '#FFFFFF',
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#E2E8F0',
  },
  rowTopLine: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  name: { fontSize: 15, fontWeight: '600', color: '#0F172A' },
  ticker: { color: '#64748B', fontSize: 12, marginTop: 2 },
  badge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 6,
    marginLeft: 8,
  },
  badgeText: { fontSize: 11, fontWeight: '700' },
  chevron: { color: '#94A3B8', fontSize: 22, paddingLeft: 8 },
});
