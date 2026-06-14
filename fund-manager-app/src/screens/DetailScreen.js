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
  TextInput,
} from 'react-native';
import { SafeAreaView, useSafeAreaInsets } from 'react-native-safe-area-context';

import GradeBadge from '../components/GradeBadge';
import { fetchStockReport, fetchClipboardText, syncFavoritesToServer, getEtfNaverCodes, putEtfNaverCode } from '../services/api';
import {
  cacheReport,
  getCachedReport,
  addFavorite,
  removeFavorite,
  isFavorite,
  getFavorites,
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
      // 서버에 관심종목 동기화 (워머 사전 캐싱용)
      const updatedFavs = await getFavorites();
      syncFavoritesToServer(updatedFavs);
    } catch (e) {
      console.warn('관심종목 토글 실패', e?.message);
    }
  }, [favorited, ticker, report]);

  // 헤더 우측 버튼: 새로고침 + 별 (favorited/refreshing 상태 변할 때마다 갱신)
  useEffect(() => {
    navigation.setOptions({
      headerRight: () => (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, marginRight: 4 }}>
          <Pressable
            onPress={() => load({ forceRefresh: true })}
            hitSlop={12}
            disabled={refreshing}
            style={({ pressed }) => [{ opacity: (pressed || refreshing) ? 0.4 : 1, paddingHorizontal: 4 }]}
          >
            <Text style={{ fontSize: 18 }}>{refreshing ? '⏳' : '🔄'}</Text>
          </Pressable>
          <Pressable
            onPress={handleToggleFavorite}
            hitSlop={12}
            style={({ pressed }) => [{ opacity: pressed ? 0.6 : 1 }]}
          >
            <Text style={{ fontSize: 22 }}>{favorited ? '⭐' : '☆'}</Text>
          </Pressable>
        </View>
      ),
    });
  }, [navigation, handleToggleFavorite, favorited, refreshing, load]);

  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async ({ forceRefresh = false } = {}) => {
    if (forceRefresh) setRefreshing(true);
    else setLoading(true);
    try {
      const data = await fetchStockReport(ticker, { refresh: forceRefresh });
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
      setRefreshing(false);
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

        <Section title={report.etf_info ? 'ETF 뉴스' : 'AI 뉴스 요약'}>
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

        {Array.isArray(report.etf_constituent_news_items) && report.etf_constituent_news_items.length > 0 && (
          <Section title="구성종목 주요 뉴스">
            {report.etf_constituent_news_items.map((item, idx) => (
              <NewsRow
                key={`c-${idx}-${item.title}`}
                item={item}
                isLast={idx === report.etf_constituent_news_items.length - 1}
              />
            ))}
          </Section>
        )}

        <EtfSection
          etf={report.etf_info}
          krxCode={ticker.split('.')[0]}
        />

        {!report.etf_info && (
          <Section title="주요 재무 수치">
            {(() => {
              const usSrc = report.region === 'US' ? 'Yahoo' : null;
              return (<>
                <FinancialRow label="PER" value={f.per} suffix="배" basis={f.per_basis} naReason={f.per_na_reason} source={f.per_source || usSrc} />
                <FinancialRow label="PBR" value={f.pbr} suffix="배" basis={f.pbr_basis} naReason={f.pbr_na_reason} source={f.pbr_source || usSrc} />
                <FinancialRow label="ROE" value={f.roe} suffix="%" basis={f.roe_basis} naReason={f.roe_na_reason} source={f.roe_source || usSrc} />
                <FinancialRow label="매출 성장률" value={f.revenue_growth} suffix="%" basis={f.revenue_growth_basis} naReason={f.revenue_growth_na_reason} source={f.revenue_growth_source || usSrc} />
                <FinancialRow label="영업이익률" value={f.operating_margin} suffix="%" basis={f.operating_margin_basis} naReason={f.operating_margin_na_reason} source={f.operating_margin_source || usSrc} />
                <FinancialRow label="부채비율" value={f.debt_ratio} suffix="%" basis={f.debt_ratio_basis} naReason={f.debt_ratio_na_reason} source={f.debt_ratio_source || usSrc} />
              </>);
            })()}
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

/** ETF 네이버 코드 등록/수정 인라인 편집기 */
function EtfNaverCodeEditor({ krxCode }) {
  const [currentCode, setCurrentCode] = useState(null); // null=로딩중
  const [editCode, setEditCode] = useState('');
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getEtfNaverCodes()
      .then(codes => {
        const code = codes[krxCode] || '';
        setCurrentCode(code);
        setEditCode(code);
      })
      .catch(() => setCurrentCode(''));
  }, [krxCode]);

  const handleSave = async () => {
    if (!editCode.trim()) return;
    setSaving(true);
    try {
      await putEtfNaverCode(krxCode, editCode.trim());
      setCurrentCode(editCode.trim());
      setEditing(false);
      Alert.alert('저장 완료', '다음 분석부터 구성종목을 자동으로 가져옵니다.');
    } catch (e) {
      Alert.alert('저장 실패', e?.message || '오류');
    } finally {
      setSaving(false);
    }
  };

  if (currentCode === null) return null;

  return (
    <View style={styles.naverCodeRow}>
      <Text style={styles.naverCodeLabel}>네이버 ETF 코드</Text>
      {editing ? (
        <View style={styles.naverCodeEditWrap}>
          <TextInput
            style={styles.naverCodeInput}
            value={editCode}
            onChangeText={setEditCode}
            placeholder="예: 0167A0"
            placeholderTextColor="#8E8E93"
            autoCapitalize="none"
            autoCorrect={false}
          />
          <Pressable
            style={[styles.naverCodeBtn, { backgroundColor: '#007AFF' }]}
            onPress={handleSave}
            disabled={saving}
          >
            <Text style={styles.naverCodeBtnText}>{saving ? '...' : '저장'}</Text>
          </Pressable>
          <Pressable
            style={[styles.naverCodeBtn, { backgroundColor: '#8E8E93', marginLeft: 4 }]}
            onPress={() => { setEditing(false); setEditCode(currentCode); }}
          >
            <Text style={styles.naverCodeBtnText}>취소</Text>
          </Pressable>
        </View>
      ) : (
        <Pressable onPress={() => setEditing(true)} style={styles.naverCodeValueWrap}>
          <Text style={[styles.naverCodeValue, !currentCode && { color: '#8E8E93' }]}>
            {currentCode || '미등록 (탭하여 등록)'}
          </Text>
          <Text style={styles.naverCodeEdit}>✏️</Text>
        </Pressable>
      )}
    </View>
  );
}

/** 물타기/불타기 점수 바 컴포넌트 */
function TradeScoreBar({ label, score, color, reasons }) {
  if (score == null) return null;
  const pct = Math.min(Math.max(score, 0), 100);
  return (
    <View style={styles.tradeScoreBlock}>
      <View style={styles.tradeScoreHeader}>
        <Text style={[styles.tradeScoreLabel, { color }]}>{label}</Text>
        <Text style={[styles.tradeScoreNum, { color }]}>{score}점</Text>
      </View>
      <View style={styles.tradeScoreTrack}>
        <View style={[styles.tradeScoreFill, { width: `${pct}%`, backgroundColor: color }]} />
      </View>
      {reasons && reasons.length > 0 ? (
        <View style={styles.tradeScoreReasons}>
          {reasons.map((r, i) => (
            <Text key={i} style={styles.tradeScoreReason}>• {r}</Text>
          ))}
        </View>
      ) : null}
    </View>
  );
}

function EtfSection({ etf, krxCode }) {
  if (!etf) return null;
  const isKR = etf.market === 'KR';
  return (
    <Section title="ETF 정보">
      {etf.fund_name ? (
        <Text style={[styles.body, { marginBottom: 8, fontWeight: '600' }]}>{etf.fund_name}</Text>
      ) : null}
      {etf.fund_family ? <EtfRow label="운용사" value={etf.fund_family} /> : null}
      {etf.category    ? <EtfRow label="유형" value={etf.category} /> : null}
      {etf.benchmark_index ? <EtfRow label="기초지수" value={etf.benchmark_index} /> : null}

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

      {/* 시세 정보 */}
      {(etf.price_52w_high != null || etf.price_52w_low != null) ? (
        <View style={styles.etf52wRow}>
          <Text style={styles.etf52wLabel}>52주</Text>
          <Text style={styles.etf52wVal}>
            {etf.price_52w_low != null ? `최저 ${etf.price_52w_low?.toLocaleString()}` : ''}
            {etf.price_52w_low != null && etf.price_52w_high != null ? ' · ' : ''}
            {etf.price_52w_high != null ? `최고 ${etf.price_52w_high?.toLocaleString()}` : ''}
          </Text>
        </View>
      ) : null}
      {etf.avg_volume_20d != null ? (
        <EtfRow label="20일 평균거래량" value={Math.round(etf.avg_volume_20d)?.toLocaleString()} suffix="주" />
      ) : null}

      {/* 수익률 */}
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

      {/* 물타기 / 불타기 점수 */}
      {(etf.water_score != null || etf.fire_score != null) ? (
        <View style={styles.tradeScoreSection}>
          <Text style={styles.tradeScoreTitle}>매수 타이밍</Text>
          <TradeScoreBar
            label="💧 물타기 (저점 매수)"
            score={etf.water_score}
            color="#2563EB"
            reasons={etf.water_reasons}
          />
          <TradeScoreBar
            label="🔥 불타기 (모멘텀 추가)"
            score={etf.fire_score}
            color="#EA580C"
            reasons={etf.fire_reasons}
          />
          <Text style={styles.tradeScoreNote}>
            점수가 높을수록 해당 전략 매수 시점에 가깝습니다 (참고용)
          </Text>
        </View>
      ) : null}

      {isKR && krxCode ? <EtfNaverCodeEditor krxCode={krxCode} /> : null}
    </Section>
  );
}

function FinancialRow({ label, value, suffix, basis, naReason, source }) {
  const isNull = value === null || value === undefined;
  const display = isNull ? (naReason ?? '-') : `${value}${suffix ?? ''}`;
  // 이유 텍스트 스타일: 적자=주황, 오류=빨강, 미제공=회색
  const reasonColor = !isNull ? null
    : naReason === '적자' ? '#FF9500'
    : naReason === '오류' ? '#FF3B30'
    : '#8E8E93';
  return (
    <View style={styles.finRow}>
      <View style={styles.finLabelWrap}>
        <Text style={styles.finLabel}>{label}</Text>
        {basis ? (
          <View style={styles.basisBadge}>
            <Text style={styles.basisText}>{basis}</Text>
          </View>
        ) : null}
        {source ? (
          <View style={styles.sourceBadge}>
            <Text style={styles.sourceText}>{source}</Text>
          </View>
        ) : null}
      </View>
      <Text style={[styles.finValue, reasonColor ? { color: reasonColor, fontSize: 13 } : null]}>
        {display}
      </Text>
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
  sourceBadge: {
    backgroundColor: '#F0FDF4',
    borderRadius: 4,
    paddingHorizontal: 5,
    paddingVertical: 1,
  },
  sourceText: { color: '#16A34A', fontSize: 10, fontWeight: '600' },
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

  // ETF 52주 고저
  etf52wRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: '#E2E8F0',
  },
  etf52wLabel: { fontSize: 12, color: '#64748B', fontWeight: '500' },
  etf52wVal: { fontSize: 12, color: '#0F172A' },

  // 물타기/불타기 점수
  tradeScoreSection: {
    marginTop: 16,
    paddingTop: 14,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#E2E8F0',
  },
  tradeScoreTitle: {
    fontSize: 13,
    fontWeight: '700',
    color: '#0F172A',
    marginBottom: 12,
  },
  tradeScoreBlock: {
    marginBottom: 14,
  },
  tradeScoreHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 6,
  },
  tradeScoreLabel: { fontSize: 13, fontWeight: '600' },
  tradeScoreNum: { fontSize: 15, fontWeight: '800' },
  tradeScoreTrack: {
    height: 8,
    backgroundColor: '#E2E8F0',
    borderRadius: 4,
    overflow: 'hidden',
  },
  tradeScoreFill: {
    height: 8,
    borderRadius: 4,
  },
  tradeScoreReasons: {
    marginTop: 6,
    gap: 2,
  },
  tradeScoreReason: {
    fontSize: 11,
    color: '#64748B',
    lineHeight: 16,
  },
  tradeScoreNote: {
    fontSize: 10,
    color: '#94A3B8',
    marginTop: 8,
    fontStyle: 'italic',
  },

  // ETF 네이버 코드 편집기
  naverCodeRow: {
    marginTop: 12,
    paddingTop: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: '#E2E8F0',
  },
  naverCodeLabel: { fontSize: 11, color: '#64748B', marginBottom: 6 },
  naverCodeValueWrap: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  naverCodeValue: { fontSize: 13, color: '#0F172A', fontWeight: '500' },
  naverCodeEdit: { fontSize: 13 },
  naverCodeEditWrap: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  naverCodeInput: {
    flex: 1,
    height: 34,
    borderWidth: 1,
    borderColor: '#CBD5E1',
    borderRadius: 6,
    paddingHorizontal: 8,
    fontSize: 13,
    color: '#0F172A',
    backgroundColor: '#fff',
  },
  naverCodeBtn: {
    height: 34,
    paddingHorizontal: 12,
    borderRadius: 6,
    justifyContent: 'center',
    alignItems: 'center',
  },
  naverCodeBtnText: { color: '#fff', fontSize: 13, fontWeight: '600' },
});