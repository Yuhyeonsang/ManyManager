import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  Pressable,
  ActivityIndicator,
  RefreshControl,
  Linking,
  Alert,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';

import GradeBadge from '../components/GradeBadge';
import { fetchStockReport } from '../services/api';
import { getFavorites, removeFavorite, getCachedReport } from '../services/database';

// ─────────────────────────────────────────────
// 메인 화면
// ─────────────────────────────────────────────
export default function FavoritesScreen({ navigation }) {
  const [items, setItems] = useState([]); // { ticker, name, report, loading, error }
  const [refreshing, setRefreshing] = useState(false);
  const [initialLoad, setInitialLoad] = useState(true);
  const insets = useSafeAreaInsets();
  const isMounted = useRef(true);

  useEffect(() => {
    isMounted.current = true;
    return () => { isMounted.current = false; };
  }, []);

  // 관심종목 목록 로드 + 각 리포트 병렬 fetch
  const load = useCallback(async ({ isRefresh = false } = {}) => {
    if (isRefresh) setRefreshing(true);

    const favs = await getFavorites();

    if (!isMounted.current) return;

    if (favs.length === 0) {
      setItems([]);
      setInitialLoad(false);
      setRefreshing(false);
      return;
    }

    // 초기 스켈레톤 세팅
    setItems(favs.map((f) => ({ ticker: f.ticker, name: f.name, report: null, loading: true, error: null })));
    setInitialLoad(false);

    // 각 종목 리포트 비동기 병렬 fetch
    favs.forEach(async (f) => {
      let report = null;
      let error = null;
      try {
        report = await fetchStockReport(f.ticker);
      } catch (e) {
        // 서버 실패 → 캐시 시도
        try {
          report = await getCachedReport(f.ticker);
          if (!report) error = '데이터를 불러올 수 없습니다';
        } catch {
          error = '데이터를 불러올 수 없습니다';
        }
      }
      if (!isMounted.current) return;
      setItems((prev) =>
        prev.map((item) =>
          item.ticker === f.ticker
            ? { ...item, report, loading: false, error }
            : item
        )
      );
    });

    setRefreshing(false);
  }, []);

  // 화면 포커스 시 재로드 (관심 해제 후 돌아오면 반영)
  useEffect(() => {
    const unsub = navigation.addListener('focus', () => load());
    return unsub;
  }, [navigation, load]);

  const handleRemove = async (ticker, name) => {
    Alert.alert(
      '관심종목 해제',
      `${name}을(를) 관심종목에서 제거할까요?`,
      [
        { text: '취소', style: 'cancel' },
        {
          text: '제거',
          style: 'destructive',
          onPress: async () => {
            await removeFavorite(ticker);
            setItems((prev) => prev.filter((i) => i.ticker !== ticker));
          },
        },
      ]
    );
  };

  if (initialLoad) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator size="large" color="#0F172A" />
      </SafeAreaView>
    );
  }

  if (items.length === 0) {
    return (
      <SafeAreaView style={styles.center}>
        <Text style={styles.emptyIcon}>⭐</Text>
        <Text style={styles.emptyTitle}>관심종목이 없습니다</Text>
        <Text style={styles.emptyDesc}>
          종목 상세 화면에서 ⭐ 버튼을 누르면{'\n'}이곳에 추가됩니다
        </Text>
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['left', 'right']}>
      <ScrollView
        contentContainerStyle={{ padding: 16, paddingBottom: 32 + (insets.bottom || 0) }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => load({ isRefresh: true })}
            tintColor="#0F172A"
          />
        }
      >
        {items.map((item) => (
          <FavoriteCard
            key={item.ticker}
            item={item}
            onRemove={() => handleRemove(item.ticker, item.name)}
            onPress={() =>
              navigation.navigate('Detail', {
                ticker: item.ticker,
                name: item.name,
              })
            }
          />
        ))}
      </ScrollView>
    </SafeAreaView>
  );
}

// ─────────────────────────────────────────────
// 관심종목 카드
// ─────────────────────────────────────────────
function FavoriteCard({ item, onRemove, onPress }) {
  const { name, ticker, report, loading, error } = item;

  return (
    <View style={styles.card}>
      {/* 카드 헤더 */}
      <Pressable
        onPress={onPress}
        style={({ pressed }) => [styles.cardHeader, pressed && { opacity: 0.7 }]}
      >
        <View style={{ flex: 1 }}>
          <Text style={styles.cardName}>{name}</Text>
          <Text style={styles.cardTicker}>{ticker}</Text>
        </View>
        {report && <GradeBadge grade={report.grade} />}
        <Text style={styles.chevron}>›</Text>
      </Pressable>

      {/* 로딩 */}
      {loading && (
        <View style={styles.cardBody}>
          <ActivityIndicator size="small" color="#64748B" />
          <Text style={styles.loadingText}>분석 불러오는 중…</Text>
        </View>
      )}

      {/* 에러 */}
      {!loading && error && (
        <View style={styles.cardBody}>
          <Text style={styles.errorText}>{error}</Text>
        </View>
      )}

      {/* 리포트 내용 */}
      {!loading && report && (
        <View style={styles.cardBody}>
          {/* AI 점수 */}
          <View style={styles.scoreRow}>
            <Text style={styles.scoreLabel}>AI 점수</Text>
            <Text style={styles.scoreValue}>{report.score ?? '-'} / 100</Text>
          </View>

          {/* 재무 요약 */}
          <FinancialMini financials={report.financials} />

          {/* 뉴스 최신 1~2건 */}
          <NewsMini newsItems={report.news_items} newsSummary={report.news_summary} />
        </View>
      )}

      {/* 관심 해제 버튼 */}
      <Pressable
        onPress={onRemove}
        style={({ pressed }) => [styles.removeBtn, pressed && { opacity: 0.6 }]}
      >
        <Text style={styles.removeBtnText}>⭐ 관심 해제</Text>
      </Pressable>
    </View>
  );
}

// ─────────────────────────────────────────────
// 재무 미니 요약
// ─────────────────────────────────────────────
function FinancialMini({ financials }) {
  const f = financials || {};
  const items = [
    { label: 'PER', value: f.per, suffix: '배' },
    { label: 'PBR', value: f.pbr, suffix: '배' },
    { label: 'ROE', value: f.roe, suffix: '%' },
    { label: '영업이익률', value: f.operating_margin, suffix: '%' },
  ];
  const hasAny = items.some((i) => i.value !== null && i.value !== undefined);
  if (!hasAny) return null;

  return (
    <View style={styles.finSection}>
      <Text style={styles.subTitle}>📊 재무</Text>
      <View style={styles.finGrid}>
        {items.map((i) => (
          <View key={i.label} style={styles.finCell}>
            <Text style={styles.finLabel}>{i.label}</Text>
            <Text style={styles.finValue}>
              {i.value !== null && i.value !== undefined ? `${i.value}${i.suffix}` : '-'}
            </Text>
          </View>
        ))}
      </View>
    </View>
  );
}

// ─────────────────────────────────────────────
// 뉴스 미니 (최신 2건 or 요약 텍스트)
// ─────────────────────────────────────────────
function NewsMini({ newsItems, newsSummary }) {
  const topNews = Array.isArray(newsItems) && newsItems.length > 0
    ? newsItems.slice(0, 2)
    : null;

  if (!topNews && !newsSummary) return null;

  return (
    <View style={styles.newsSection}>
      <Text style={styles.subTitle}>📰 최신 뉴스</Text>
      {topNews
        ? topNews.map((item, idx) => (
            <NewsMinRow key={idx} item={item} />
          ))
        : <Text style={styles.newsText} numberOfLines={3}>{newsSummary}</Text>
      }
    </View>
  );
}

function NewsMinRow({ item }) {
  const url = (item.link || '').trim();
  const impactStyle = impactBadgeStyle(item.impact);
  const rel = formatRelativeTime(item.pub_date);

  const handlePress = async () => {
    if (!url) return;
    try {
      const can = await Linking.canOpenURL(url);
      if (can) await Linking.openURL(url);
    } catch {}
  };

  return (
    <Pressable
      onPress={handlePress}
      disabled={!url}
      style={({ pressed }) => [styles.newsMinRow, pressed && url && { opacity: 0.7 }]}
    >
      <View style={[styles.impactBadge, { backgroundColor: impactStyle.bg }]}>
        <Text style={[styles.impactText, { color: impactStyle.fg }]}>
          {item.impact || '중립'}
        </Text>
      </View>
      <Text style={styles.newsMinTitle} numberOfLines={2}>{item.title}</Text>
      {rel ? <Text style={styles.newsMinTime}>{rel}</Text> : null}
    </Pressable>
  );
}

// ─────────────────────────────────────────────
// 헬퍼
// ─────────────────────────────────────────────
function impactBadgeStyle(impact) {
  switch (impact) {
    case '긍정': return { bg: '#DCFCE7', fg: '#166534' };
    case '부정': return { bg: '#FEE2E2', fg: '#991B1B' };
    default:     return { bg: '#E2E8F0', fg: '#475569' };
  }
}

function formatRelativeTime(pubDate) {
  if (!pubDate) return '';
  const t = Date.parse(pubDate);
  if (isNaN(t)) return '';
  const diffMs = Date.now() - t;
  if (diffMs < 0) return '방금';
  const min = Math.floor(diffMs / 60000);
  if (min < 1) return '방금';
  if (min < 60) return `${min}분 전`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}시간 전`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}일 전`;
  return `${Math.floor(day / 7)}주 전`;
}

// ─────────────────────────────────────────────
// 스타일
// ─────────────────────────────────────────────
const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F1F5F9' },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', padding: 32 },
  emptyIcon: { fontSize: 48, marginBottom: 12 },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: '#0F172A', marginBottom: 8 },
  emptyDesc: { color: '#64748B', textAlign: 'center', lineHeight: 22 },

  // 카드
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    marginBottom: 16,
    overflow: 'hidden',
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  cardHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#E2E8F0',
    gap: 8,
  },
  cardName: { fontSize: 16, fontWeight: '700', color: '#0F172A' },
  cardTicker: { color: '#64748B', fontSize: 12, marginTop: 2 },
  chevron: { color: '#94A3B8', fontSize: 20, fontWeight: '300', marginLeft: 4 },
  cardBody: { padding: 16 },
  loadingText: { color: '#64748B', marginTop: 8, textAlign: 'center' },
  errorText: { color: '#EF4444', fontSize: 13 },

  // 점수
  scoreRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    backgroundColor: '#F8FAFC',
    borderRadius: 10,
    paddingHorizontal: 14,
    paddingVertical: 10,
    marginBottom: 12,
  },
  scoreLabel: { color: '#64748B', fontSize: 13 },
  scoreValue: { color: '#0F172A', fontSize: 18, fontWeight: '800' },

  // 재무
  finSection: { marginBottom: 12 },
  subTitle: { fontSize: 12, fontWeight: '700', color: '#475569', marginBottom: 8 },
  finGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  finCell: {
    flex: 1,
    minWidth: '45%',
    backgroundColor: '#F8FAFC',
    borderRadius: 8,
    padding: 10,
  },
  finLabel: { color: '#64748B', fontSize: 11, marginBottom: 2 },
  finValue: { color: '#0F172A', fontSize: 14, fontWeight: '700' },

  // 뉴스
  newsSection: { marginTop: 4 },
  newsText: { color: '#334155', fontSize: 13, lineHeight: 20 },
  newsMinRow: {
    paddingVertical: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#E2E8F0',
    gap: 4,
  },
  impactBadge: {
    alignSelf: 'flex-start',
    paddingHorizontal: 7,
    paddingVertical: 2,
    borderRadius: 5,
    marginBottom: 4,
  },
  impactText: { fontSize: 10, fontWeight: '700' },
  newsMinTitle: { color: '#1E293B', fontSize: 13, lineHeight: 19, fontWeight: '500' },
  newsMinTime: { color: '#94A3B8', fontSize: 11, marginTop: 2 },

  // 해제 버튼
  removeBtn: {
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#E2E8F0',
    paddingVertical: 12,
    alignItems: 'center',
  },
  removeBtnText: { color: '#94A3B8', fontSize: 13, fontWeight: '600' },
});
