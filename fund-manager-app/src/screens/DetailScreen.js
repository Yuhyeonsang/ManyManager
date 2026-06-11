import React, { useCallback, useEffect, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  Pressable,
  ActivityIndicator,
  Alert,
  Linking,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';

import GradeBadge from '../components/GradeBadge';
import { fetchStockReport, fetchClipboardText } from '../services/api';
import {
  cacheReport,
  getCachedReport,
  addFavorite,
  removeFavorite,
  isFavorite,
} from '../services/database';
import {
  copyToClipboard,
  formatReportForClipboard,
} from '../utils/clipboard';

export default function DetailScreen({ route, navigation }) {
  const { ticker } = route.params;
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [copying, setCopying] = useState(false);
  const [favorited, setFavorited] = useState(false);
  const insets = useSafeAreaInsets();

  // 관심종목 상태 초기 로드
  useEffect(() => {
    isFavorite(ticker).then(setFavorited).catch(() => {});
  }, [ticker]);

  const handleToggleFavorite = useCallback(async () => {
    try {
      if (favorited) {
        await removeFavorite(ticker);
        setFavorited(false);
      } else {
        const name = report?.name ?? ticker;
        await addFavorite(ticker, name);
        setFavorited(true);
      }
    } catch (e) {
      console.warn('관심종목 토글 실패', e?.message);
    }
  }, [favorited, ticker, report]);

  // 헤더 우측 별 버튼 등록 (favorited 상태 변할 때마다 갱신)
  useEffect(() => {
    navigation.setOptions({
      headerRight: () => (
        <Pressable
          onPress={handleToggleFavorite}
          hitSlop={12}
          style={({ pressed }) => [{ opacity: pressed ? 0.6 : 1, marginRight: 4 }]}
        >
          <Text style={{ fontSize: 22 }}>{favorited ? '⭐' : '☆'}</Text>
        </Pressable>
      ),
    });
  }, [navigation, handleToggleFavorite, favorited]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchStockReport(ticker);
      setReport(data);
      setOffline(false);
      cacheReport(data).catch((e) => console.warn('cache 실패', e));
    } catch (e) {
      console.warn('상세 리포트 서버 통신 실패, 캐시 사용', e?.message);
      const cached = await getCachedReport(ticker);
      if (cached) {
        setReport(cached);
        setOffline(true);
      } else {
        Alert.alert(
          '오프라인',
          '서버에 연결할 수 없고, 이 종목의 저장된 리포트도 없습니다.'
        );
      }
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  useEffect(() => {
    load();
  }, [load]);

  const handleCopy = async () => {
    if (!report) return;
    setCopying(true);
    try {
      let text;
      try {
        text = await fetchClipboardText(ticker);
      } catch {
        text = formatReportForClipboard(report);
      }
      await copyToClipboard(text);
      Alert.alert(
        '복사 완료',
        'Claude 웹버전에 붙여넣고 추가 분석을 받아보세요!'
      );
    } catch (e) {
      Alert.alert('복사 실패', e?.message ?? '알 수 없는 오류');
    } finally {
      setCopying(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator size="large" color="#0F172A" />
        <Text style={styles.loadingText}>리포트를 불러오는 중…</Text>
      </SafeAreaView>
    );
  }

  if (!report) {
    return (
      <SafeAreaView style={styles.center}>
        <Text style={styles.empty}>리포트를 불러올 수 없습니다.</Text>
      </SafeAreaView>
    );
  }

  const f = report.financials || {};

  return (
    <SafeAreaView style={styles.container} edges={['left', 'right']}>
      <ScrollView
        contentContainerStyle={{
          padding: 16,
          paddingBottom: 120 + (insets.bottom || 0),
        }}
      >
        {offline && (
          <View style={styles.offlineBanner}>
            <Text style={styles.offlineText}>
              오프라인 — 마지막에 저장된 리포트입니다
            </Text>
          </View>
        )}

        <View style={styles.header}>
          <View style={{ flex: 1 }}>
            <Text style={styles.name}>{report.name}</Text>
            <Text style={styles.ticker}>{report.ticker}</Text>
          </View>
          <GradeBadge grade={report.grade} />
        </View>

        <View style={styles.scoreBox}>
          <Text style={styles.scoreLabel}>AI 종합 점수</Text>
          <Text style={styles.scoreValue}>
            {report.score ?? '-'}
            <Text style={styles.scoreMax}> / 100</Text>
          </Text>
        </View>

        <Section title="AI 뉴스 요약">
          {Array.isArray(report.news_items) && report.news_items.length > 0 ? (
            report.news_items.map((item, idx) => (
              <NewsRow
                key={`${idx}-${item.title}`}
                item={item}
                isLast={idx === report.news_items.length - 1}
              />
            ))
          ) : (
            <Text style={styles.body}>
              {report.news_summary?.trim() || '(요약된 뉴스가 없습니다)'}
            </Text>
          )}
        </Section>

        <EtfSection etf={report.etf_info} />

        {!report.etf_info && (
          <Section title="주요 재무 수치">
            <FinancialRow label="PER" value={f.per} suffix="배" basis={f.per_basis} />
            <FinancialRow label="PBR" value={f.pbr} suffix="배" basis={f.pbr_basis} />
            <FinancialRow label="ROE" value={f.roe} suffix="%" basis={f.roe_basis} />
            <FinancialRow label="매출 성장률" value={f.revenue_growth} suffix="%" basis={f.revenue_growth_basis} />
            <FinancialRow label="영업이익률" value={f.operating_margin} suffix="%" basis={f.operating_margin_basis} />
            <FinancialRow label="부채비율" value={f.debt_ratio} suffix="%" basis={f.debt_ratio_basis} />
          </Section>
        )}

        {report.updated_at && (
          <Text style={styles.updatedAt}>
            데이터 기준: {report.updated_at}
          </Text>
        )}
      </ScrollView>

      <View
        style={[
          styles.footer,
          { paddingBottom: 16 + (insets.bottom || 0) },
        ]}
      >
        <Pressable
          onPress={handleCopy}
          disabled={copying}
          style={({ pressed }) => [
            styles.copyBtn,
            pressed && { opacity: 0.85 },
            copying && { opacity: 0.6 },
          ]}
        >
          {copying ? (
            <ActivityIndicator color="#FFFFFF" />
          ) : (
            <Text style={styles.copyBtnText}>
              텍스트 리포트 복사 (Claude 웹에 붙여넣기)
            </Text>
          )}
        </Pressable>
      </View>
    </SafeAreaView>
  );
}

function Section({ title, children }) {
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      <View style={styles.sectionBody}>{children}</View>
    </View>
  );
}


function EtfRow({ label, value, suffix = '', color }) {
  const display = value === null || value === undefined ? '-' : `${value}${suffix}`;
  const valStyle = [styles.finValue];
  if (color === 'auto' && value !== null && value !== undefined) {
    if (value > 0) valStyle.push({ color: '#16A34A' });
    else if (value < 0) valStyle.push({ color: '#DC2626' });
  }
  return (
    <View style={styles.finRow}>
      <Text style={styles.finLabel}>{label}</Text>
      <Text style={valStyle}>{display}</Text>
    </View>
  );
}

function EtfSection({ etf }) {
  if (!etf) return null;
  const isKR = etf.market === 'KR';
  return (
    <Section title="ETF 정보">
      {etf.fund_name ? (
        <Text style={[styles.body, { marginBottom: 8, fontWeight: '600' }]}>{etf.fund_name}</Text>
      ) : null}
      {etf.fund_family ? <EtfRow label="운용사" value={etf.fund_family} /> : null}
      {etf.category    ? <EtfRow label="카테고리" value={etf.category} /> : null}

      {isKR && etf.nav != null ? (
        <EtfRow label="NAV" value={etf.nav?.toLocaleString()} suffix="원" />
      ) : null}
      {isKR && etf.nav_diff_pct != null ? (
        <EtfRow label="괴리율" value={etf.nav_diff_pct} suffix="%" color="auto" />
      ) : null}

      {etf.total_assets_billion != null ? (
        <EtfRow
          label="총운용자산"
          value={isKR
            ? `${etf.total_assets_billion?.toLocaleString()}억원`
            : `$${etf.total_assets_billion}B`}
        />
      ) : null}
      {etf.expense_ratio_pct != null ? (
        <EtfRow label="운용보수(TER)" value={etf.expense_ratio_pct} suffix="%" />
      ) : null}
      {etf.dividend_yield_pct != null ? (
        <EtfRow label="배당수익률" value={etf.dividend_yield_pct} suffix="%" />
      ) : null}

      <View style={styles.etfReturnRow}>
        {[
          { label: '1개월', val: etf.return_1m },
          { label: '3개월', val: etf.return_3m },
          { label: isKR ? '1년' : 'YTD', val: isKR ? etf.return_1y : etf.return_ytd },
        ].map(({ label, val }) => (
          <View key={label} style={styles.etfReturnCell}>
            <Text style={styles.etfReturnLabel}>{label}</Text>
            <Text style={[
              styles.etfReturnValue,
              val != null && val > 0 ? { color: '#16A34A' } :
              val != null && val < 0 ? { color: '#DC2626' } : {},
            ]}>
              {val != null ? `${val > 0 ? '+' : ''}${val}%` : '-'}
            </Text>
          </View>
        ))}
      </View>

      {!isKR && etf.return_3y_ann != null ? (
        <EtfRow label="3년 연평균" value={etf.return_3y_ann} suffix="%" color="auto" />
      ) : null}
      {!isKR && etf.return_5y_ann != null ? (
        <EtfRow label="5년 연평균" value={etf.return_5y_ann} suffix="%" color="auto" />
      ) : null}
      {!isKR && etf.beta != null ? (
        <EtfRow label="베타 (3년)" value={etf.beta} />
      ) : null}
    </Section>
  );
}

function FinancialRow({ label, value, suffix, basis }) {
  const display =
    value === null || value === undefined ? '-' : `${value}${suffix ?? ''}`;
  return (
    <View style={styles.finRow}>
      <View style={styles.finLabelWrap}>
        <Text style={styles.finLabel}>{label}</Text>
        {basis ? (
          <View style={styles.basisBadge}>
            <Text style={styles.basisText}>{basis}</Text>
          </View>
        ) : null}
      </View>
      <Text style={styles.finValue}>{display}</Text>
    </View>
  );
}

function NewsRow({ item, isLast }) {
  const url = (item.link || '').trim();
  const hasLink = !!url;
  const rel = formatRelativeTime(item.pub_date);
  const impactStyle = impactBadgeStyle(item.impact);

  const handlePress = async () => {
    if (!hasLink) return;
    try {
      const can = await Linking.canOpenURL(url);
      if (can) {
        await Linking.openURL(url);
      } else {
        Alert.alert('링크 열기 실패', '브라우저에서 이 URL을 열 수 없습니다.');
      }
    } catch (e) {
      Alert.alert('링크 열기 실패', String(e?.message || e));
    }
  };

  return (
    <Pressable
      onPress={handlePress}
      disabled={!hasLink}
      style={({ pressed }) => [
        styles.newsRow,
        isLast && { borderBottomWidth: 0 },
        pressed && hasLink && { backgroundColor: '#F8FAFC' },
      ]}
    >
      <View style={styles.newsHeaderRow}>
        <View style={[styles.impactBadge, { backgroundColor: impactStyle.bg }]}>
          <Text style={[styles.impactBadgeText, { color: impactStyle.fg }]}>
            {item.impact || '중립'}
          </Text>
        </View>
        {rel ? <Text style={styles.newsTime}>{rel}</Text> : null}
      </View>
      <Text style={styles.newsTitle} numberOfLines={3}>
        {item.title}
      </Text>
      {hasLink ? (
        <Text style={styles.newsLink}>탭하여 원문 보기</Text>
      ) : null}
    </Pressable>
  );
}

function impactBadgeStyle(impact) {
  switch (impact) {
    case '긍정':
      return { bg: '#DCFCE7', fg: '#166534' };
    case '부정':
      return { bg: '#FEE2E2', fg: '#991B1B' };
    default:
      return { bg: '#E2E8F0', fg: '#475569' };
  }
}

function formatRelativeTime(pubDate) {
  if (!pubDate) return '';
  const t = Date.parse(pubDate);
  if (isNaN(t)) return '';
  const diffMs = Date.now() - t;
  if (diffMs < 0) return '방금';
  const sec = Math.floor(diffMs / 1000);
  if (sec < 60) return '방금';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}분 전`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}시간 전`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}일 전`;
  const wk = Math.floor(day / 7);
  if (wk < 5) return `${wk}주 전`;
  const mo = Math.floor(day / 30);
  if (mo < 12) return `${mo}개월 전`;
  return `${Math.floor(day / 365)}년 전`;
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
  empty: { color: '#64748B' },
  offlineBanner: {
    backgroundColor: '#FEF3C7',
    padding: 10,
    borderRadius: 8,
    marginBottom: 12,
  },
  offlineText: { color: '#92400E', fontSize: 12, fontWeight: '600' },
  header: { flexDirection: 'row', alignItems: 'center', marginBottom: 16 },
  name: { fontSize: 22, fontWeight: '800', color: '#0F172A' },
  ticker: { color: '#64748B', marginTop: 2 },
  scoreBox: {
    backgroundColor: '#0F172A',
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
  },
  scoreLabel: { color: '#94A3B8', fontSize: 12 },
  scoreValue: {
    color: '#F8FAFC',
    fontSize: 36,
    fontWeight: '800',
    marginTop: 4,
  },
  scoreMax: { fontSize: 16, fontWeight: '500', color: '#94A3B8' },
  section: { marginBottom: 16 },
  sectionTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#334155',
    marginBottom: 8,
  },
  sectionBody: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 14,
  },
  body: { color: '#1E293B', fontSize: 14, lineHeight: 21 },
  finRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 8,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#E2E8F0',
  },
  finLabelWrap: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  finLabel: { color: '#64748B' },
  basisBadge: {
    backgroundColor: '#EFF6FF',
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  basisText: { color: '#3B82F6', fontSize: 10, fontWeight: '600' },
  finValue: { color: '#0F172A', fontWeight: '600' },
  updatedAt: {
    color: '#94A3B8',
    fontSize: 11,
    textAlign: 'right',
    marginTop: 8,
  },
  footer: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    padding: 16,
    backgroundColor: 'rgba(241,245,249,0.95)',
  },
  copyBtn: {
    backgroundColor: '#0F172A',
    borderRadius: 12,
    paddingVertical: 16,
    alignItems: 'center',
  },
  copyBtnText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '700',
  },
  newsRow: {
    paddingVertical: 10,
    paddingHorizontal: 2,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#E2E8F0',
  },
  newsHeaderRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 6,
  },
  impactBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: 6,
  },
  impactBadgeText: {
    fontSize: 11,
    fontWeight: '700',
  },
  newsTime: {
    fontSize: 11,
    color: '#94A3B8',
    fontWeight: '500',
  },
  newsTitle: {
    color: '#0F172A',
    fontSize: 14,
    lineHeight: 20,
    fontWeight: '500',
  },
  newsLink: {
    color: '#2563EB',
    fontSize: 11,
    marginTop: 4,
    fontWeight: '600',
  },

  etfReturnRow: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    paddingVertical: 12,
    marginVertical: 4,
    backgroundColor: '#F8FAFC',
    borderRadius: 8,
  },
  etfReturnCell: { alignItems: 'center', flex: 1 },
  etfReturnLabel: { color: '#64748B', fontSize: 11, marginBottom: 4 },
  etfReturnValue: { color: '#0F172A', fontSize: 15, fontWeight: '700' },
});