/**
 * BacktestScreen.js
 * 이미지 조건 → 과거 데이터 백테스트 결과 표시
 */

import React, { useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import { analyzeTradeImage, runBacktest } from '../services/api';

const PERIODS = [
  { label: '1개월', days: 30 },
  { label: '3개월', days: 90 },
  { label: '6개월', days: 180 },
  { label: '1년',   days: 365 },
];

export default function BacktestScreen() {
  const [analyzing, setAnalyzing]   = useState(false);
  const [running,   setRunning]     = useState(false);
  const [conditions, setConditions] = useState(null);
  const [result,    setResult]      = useState(null);
  const [period,    setPeriod]      = useState(90);

  // ── 이미지 선택 & 분석 ──────────────────────
  const pickImage = async (fromCamera = false) => {
    const picker = fromCamera
      ? ImagePicker.launchCameraAsync
      : ImagePicker.launchImageLibraryAsync;
    const permFn = fromCamera
      ? ImagePicker.requestCameraPermissionsAsync
      : ImagePicker.requestMediaLibraryPermissionsAsync;

    const { status } = await permFn();
    if (status !== 'granted') {
      Alert.alert('권한 필요', '사진 접근 권한이 필요합니다.');
      return;
    }

    const res = await picker({ allowsEditing: false, quality: 0.85 });
    if (res.canceled) return;

    setAnalyzing(true);
    setResult(null);
    try {
      const asset = res.assets[0];
      const ext   = asset.uri.split('.').pop().toLowerCase();
      const mime  = ext === 'png' ? 'image/png' : 'image/jpeg';
      const data  = await analyzeTradeImage(asset.uri, mime);
      setConditions(data.conditions);
      Alert.alert('분석 완료', `📋 ${data.conditions.summary}`);
    } catch (e) {
      Alert.alert('분석 실패', e?.message ?? String(e));
    } finally {
      setAnalyzing(false);
    }
  };

  // ── 백테스트 실행 ──────────────────────────
  const doBacktest = async () => {
    if (!conditions) {
      Alert.alert('조건 없음', '먼저 이미지를 업로드해서 조건을 분석하세요.');
      return;
    }
    setRunning(true);
    setResult(null);
    try {
      const data = await runBacktest(conditions, period, 10_000_000);
      setResult(data);
    } catch (e) {
      Alert.alert('백테스트 실패', e?.message ?? String(e));
    } finally {
      setRunning(false);
    }
  };

  // ── 색상 헬퍼 ─────────────────────────────
  const returnColor = (pct) => {
    if (pct > 0) return '#16A34A';
    if (pct < 0) return '#DC2626';
    return '#64748B';
  };

  const formatMoney = (n) =>
    n >= 0
      ? `+${n.toLocaleString()}원`
      : `${n.toLocaleString()}원`;

  // ── 요약 카드 ─────────────────────────────
  const renderSummary = () => {
    const s = result.summary;
    if (s.error) {
      return (
        <View style={styles.errorCard}>
          <Text style={styles.errorCardText}>⚠️ {s.error}</Text>
        </View>
      );
    }
    return (
      <>
        {/* 수익률 빅 카드 */}
        <View style={[styles.bigCard, { borderColor: returnColor(s.total_return_pct) }]}>
          <Text style={styles.bigCardLabel}>총 수익률</Text>
          <Text style={[styles.bigCardValue, { color: returnColor(s.total_return_pct) }]}>
            {s.total_return_pct > 0 ? '+' : ''}{s.total_return_pct}%
          </Text>
          <Text style={[styles.bigCardSub, { color: returnColor(s.total_return_pct) }]}>
            {formatMoney(s.total_pnl)}
          </Text>
        </View>

        {/* 지표 그리드 */}
        <View style={styles.metricGrid}>
          <MetricBox label="최종 평가액" value={`${s.final_value?.toLocaleString()}원`} />
          <MetricBox label="총 거래횟수" value={`${s.trade_count}회`} />
          <MetricBox
            label="승률"
            value={`${s.win_rate}%`}
            valueColor={s.win_rate >= 50 ? '#16A34A' : '#DC2626'}
          />
          <MetricBox
            label="최대 낙폭(MDD)"
            value={`-${s.mdd_pct}%`}
            valueColor="#DC2626"
          />
          <MetricBox label="테스트 기간" value={`${s.period_days}일`} />
          <MetricBox label="초기 자본" value={`${s.initial_cash?.toLocaleString()}원`} />
        </View>
      </>
    );
  };

  // ── 종목별 결과 ───────────────────────────
  const renderPerStock = () => (
    <>
      <Text style={styles.sectionTitle}>📊 종목별 결과</Text>
      {result.per_stock.length === 0 && (
        <Text style={styles.emptyText}>체결된 매도 거래가 없습니다.</Text>
      )}
      {result.per_stock.map((s) => (
        <View key={s.ticker} style={styles.stockRow}>
          <View style={styles.stockInfo}>
            <Text style={styles.stockName}>{s.name}</Text>
            <Text style={styles.stockMeta}>{s.ticker} · {s.trade_count}회 거래 · 승률 {s.win_rate}%</Text>
          </View>
          <Text style={[styles.stockPnl, { color: returnColor(s.pnl) }]}>
            {formatMoney(s.pnl)}
          </Text>
        </View>
      ))}
    </>
  );

  // ── 거래 내역 ─────────────────────────────
  const renderTradeLog = () => (
    <>
      <Text style={styles.sectionTitle}>📜 거래 내역</Text>
      {result.trade_log.slice(0, 50).map((t, i) => (
        <View key={i} style={styles.logRow}>
          <Text style={[
            styles.logAction,
            t.action === '매수' ? styles.buyText :
            t.action === '매도' || t.action === '청산' ? styles.sellText :
            styles.mutedText
          ]}>
            {t.action}
          </Text>
          <View style={styles.logInfo}>
            <Text style={styles.logName}>{t.name} ({t.ticker})</Text>
            <Text style={styles.logMeta}>
              {t.price?.toLocaleString()}원 · {t.qty}주 · {t.date}
            </Text>
            {t.pnl != null && (
              <Text style={[styles.logPnl, { color: returnColor(t.pnl) }]}>
                {formatMoney(t.pnl)}  ({t.pnl_pct > 0 ? '+' : ''}{t.pnl_pct}%)
              </Text>
            )}
          </View>
        </View>
      ))}
      {result.trade_log.length > 50 && (
        <Text style={styles.emptyText}>... 외 {result.trade_log.length - 50}건</Text>
      )}
    </>
  );

  return (
    <SafeAreaView style={styles.container} edges={['bottom']}>
      <ScrollView contentContainerStyle={styles.scroll}>

        {/* ── 이미지 업로드 ── */}
        <Text style={styles.sectionTitle}>📸 조건 이미지 등록</Text>
        <View style={styles.imageButtons}>
          <TouchableOpacity
            style={[styles.imgBtn, analyzing && styles.btnDisabled]}
            onPress={() => pickImage(false)}
            disabled={analyzing}
          >
            {analyzing
              ? <ActivityIndicator color="#fff" />
              : <Text style={styles.imgBtnText}>📁 갤러리</Text>
            }
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.imgBtn, styles.imgBtnSec, analyzing && styles.btnDisabled]}
            onPress={() => pickImage(true)}
            disabled={analyzing}
          >
            <Text style={[styles.imgBtnText, { color: '#6366F1' }]}>📷 카메라</Text>
          </TouchableOpacity>
        </View>
        {analyzing && <Text style={styles.subText}>AI가 조건 분석 중...</Text>}

        {/* ── 추출된 조건 요약 ── */}
        {conditions && (
          <View style={styles.condSummaryCard}>
            <Text style={styles.condSummaryTitle}>📋 추출된 조건</Text>
            <Text style={styles.condSummaryText}>{conditions.summary}</Text>
            <Text style={styles.condSummaryMeta}>
              매수 {conditions.buy_conditions?.length ?? 0}건 · 매도 {conditions.sell_conditions?.length ?? 0}건
            </Text>
          </View>
        )}

        {/* ── 기간 선택 ── */}
        <Text style={styles.sectionTitle}>📅 백테스트 기간</Text>
        <View style={styles.periodRow}>
          {PERIODS.map((p) => (
            <TouchableOpacity
              key={p.days}
              style={[styles.periodBtn, period === p.days && styles.periodBtnActive]}
              onPress={() => setPeriod(p.days)}
            >
              <Text style={[styles.periodBtnText, period === p.days && styles.periodBtnTextActive]}>
                {p.label}
              </Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* ── 실행 버튼 ── */}
        <TouchableOpacity
          style={[styles.runBtn, (running || analyzing || !conditions) && styles.btnDisabled]}
          onPress={doBacktest}
          disabled={running || analyzing || !conditions}
        >
          {running
            ? <ActivityIndicator color="#fff" size="large" />
            : <Text style={styles.runBtnText}>🔍 백테스트 실행</Text>
          }
        </TouchableOpacity>
        {running && <Text style={styles.subText}>과거 데이터 다운로드 및 시뮬레이션 중...</Text>}

        {/* ── 결과 ── */}
        {result && (
          <>
            <Text style={styles.sectionTitle}>📈 백테스트 결과</Text>
            {renderSummary()}
            {result.per_stock && renderPerStock()}
            {result.trade_log && renderTradeLog()}
          </>
        )}

      </ScrollView>
    </SafeAreaView>
  );
}

function MetricBox({ label, value, valueColor }) {
  return (
    <View style={styles.metricBox}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={[styles.metricValue, valueColor ? { color: valueColor } : null]}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F1F5F9' },
  scroll: { padding: 16, paddingBottom: 40 },

  sectionTitle: { fontSize: 15, fontWeight: '700', color: '#1E293B', marginTop: 20, marginBottom: 10 },

  imageButtons: { flexDirection: 'row', gap: 10 },
  imgBtn: { flex: 1, backgroundColor: '#6366F1', padding: 14, borderRadius: 10, alignItems: 'center' },
  imgBtnSec: { backgroundColor: '#fff', borderWidth: 1.5, borderColor: '#6366F1' },
  imgBtnText: { color: '#fff', fontWeight: '700', fontSize: 14 },
  subText: { textAlign: 'center', color: '#6366F1', marginTop: 8, fontSize: 13 },
  btnDisabled: { opacity: 0.5 },

  condSummaryCard: {
    backgroundColor: '#fff', borderRadius: 10, padding: 14, marginTop: 12,
    borderLeftWidth: 3, borderLeftColor: '#6366F1',
  },
  condSummaryTitle: { fontSize: 13, fontWeight: '700', color: '#6366F1', marginBottom: 4 },
  condSummaryText:  { fontSize: 13, color: '#334155' },
  condSummaryMeta:  { fontSize: 12, color: '#94A3B8', marginTop: 6 },

  periodRow: { flexDirection: 'row', gap: 8 },
  periodBtn: {
    flex: 1, paddingVertical: 10, borderRadius: 8,
    backgroundColor: '#fff', borderWidth: 1, borderColor: '#CBD5E1', alignItems: 'center',
  },
  periodBtnActive: { backgroundColor: '#6366F1', borderColor: '#6366F1' },
  periodBtnText:   { fontSize: 13, fontWeight: '600', color: '#475569' },
  periodBtnTextActive: { color: '#fff' },

  runBtn: {
    marginTop: 20, backgroundColor: '#6366F1', padding: 18,
    borderRadius: 14, alignItems: 'center',
    shadowColor: '#000', shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1, shadowRadius: 4, elevation: 3,
  },
  runBtnText: { color: '#fff', fontSize: 18, fontWeight: '800' },

  // 결과 카드
  bigCard: {
    backgroundColor: '#fff', borderRadius: 14, padding: 24,
    alignItems: 'center', borderWidth: 2, marginBottom: 12,
    shadowColor: '#000', shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.08, shadowRadius: 4, elevation: 2,
  },
  bigCardLabel: { fontSize: 13, color: '#64748B', marginBottom: 4 },
  bigCardValue: { fontSize: 42, fontWeight: '900' },
  bigCardSub:   { fontSize: 16, fontWeight: '600', marginTop: 4 },

  metricGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 4 },
  metricBox: {
    width: '48%', backgroundColor: '#fff', borderRadius: 10,
    padding: 14, alignItems: 'center',
    shadowColor: '#000', shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05, shadowRadius: 2, elevation: 1,
  },
  metricLabel: { fontSize: 12, color: '#94A3B8', marginBottom: 4 },
  metricValue: { fontSize: 16, fontWeight: '700', color: '#1E293B' },

  errorCard: { backgroundColor: '#FEE2E2', borderRadius: 10, padding: 14, marginBottom: 8 },
  errorCardText: { color: '#DC2626', fontSize: 13 },

  stockRow: {
    flexDirection: 'row', backgroundColor: '#fff', borderRadius: 10,
    padding: 12, marginBottom: 8, alignItems: 'center',
    shadowColor: '#000', shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05, shadowRadius: 2, elevation: 1,
  },
  stockInfo: { flex: 1 },
  stockName: { fontSize: 14, fontWeight: '700', color: '#1E293B' },
  stockMeta: { fontSize: 12, color: '#94A3B8', marginTop: 2 },
  stockPnl:  { fontSize: 15, fontWeight: '700' },

  logRow: {
    flexDirection: 'row', backgroundColor: '#fff', borderRadius: 8,
    padding: 10, marginBottom: 6, alignItems: 'flex-start',
  },
  logAction: { fontSize: 12, fontWeight: '700', width: 38, marginTop: 2 },
  buyText:   { color: '#16A34A' },
  sellText:  { color: '#DC2626' },
  mutedText: { color: '#94A3B8' },
  logInfo:   { flex: 1 },
  logName:   { fontSize: 13, fontWeight: '600', color: '#1E293B' },
  logMeta:   { fontSize: 11, color: '#64748B', marginTop: 2 },
  logPnl:    { fontSize: 12, fontWeight: '700', marginTop: 2 },
  emptyText: { fontSize: 13, color: '#94A3B8', textAlign: 'center', marginVertical: 8 },
});
