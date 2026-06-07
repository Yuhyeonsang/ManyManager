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
  TextInput,
  Image,
  Modal,
  KeyboardAvoidingView,
  Platform,
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

// 새 조건 기본 템플릿
const emptyBuy  = () => ({ label: '', name: '', ticker: '', ref_ticker: '', condition: '', weight_pct: '', weight_mode: 'add' });
const emptySell = () => ({ label: '', name: '', ticker: '', condition: '', sell_pct: '', sell_mode: 'initial_qty' });
const emptyLock = () => ({ condition: '', action: 'liquidate' });

export default function BacktestScreen() {
  const [analyzing, setAnalyzing]   = useState(false);
  const [running,   setRunning]     = useState(false);
  const [conditions, setConditions] = useState(null);
  const [result,    setResult]      = useState(null);
  const [period,    setPeriod]      = useState(90);
  const [cashInput, setCashInput]   = useState('10000000');
  const [condExpanded, setCondExpanded] = useState(false);

  // 이미지
  const [imageUri, setImageUri]     = useState(null);
  const [analyzeError, setAnalyzeError] = useState(null);
  const [imageModalVisible, setImageModalVisible] = useState(false);

  // 조건 편집 모달
  const [editModal, setEditModal] = useState({ visible: false, type: null, index: null, data: null });

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
      setAnalyzeError('사진 접근 권한이 필요합니다.');
      return;
    }

    const res = await picker({ allowsEditing: false, quality: 0.85 });
    if (res.canceled) return;

    setAnalyzing(true);
    setAnalyzeError(null);
    setResult(null);
    const asset = res.assets[0];
    setImageUri(asset.uri);

    try {
      const ext  = asset.uri.split('.').pop().toLowerCase();
      const mime = ext === 'png' ? 'image/png' : 'image/jpeg';
      const data = await analyzeTradeImage(asset.uri, mime);
      setConditions(data.conditions);
      setCondExpanded(true);
    } catch (e) {
      setAnalyzeError(e?.message ?? String(e));
      // 이미지는 유지, 조건만 없는 상태 → 수동으로 조건 추가 가능하도록
      if (!conditions) {
        setConditions({ summary: '수동 입력', buy_conditions: [], sell_conditions: [], lock_conditions: [] });
      }
    } finally {
      setAnalyzing(false);
    }
  };

  // ── 백테스트 실행 ──────────────────────────
  const doBacktest = async () => {
    if (!conditions) {
      Alert.alert('조건 없음', '먼저 이미지를 업로드하거나 조건을 입력하세요.');
      return;
    }
    setRunning(true);
    setResult(null);
    try {
      const cash = parseInt(cashInput.replace(/,/g, ''), 10) || 10_000_000;
      const data = await runBacktest(conditions, period, cash);
      setResult(data);
    } catch (e) {
      Alert.alert('백테스트 실패', e?.message ?? String(e));
    } finally {
      setRunning(false);
    }
  };

  // ── 조건 CRUD ────────────────────────────
  const openEdit = (type, index) => {
    const list =
      type === 'buy'  ? conditions.buy_conditions  :
      type === 'sell' ? conditions.sell_conditions :
      conditions.lock_conditions;
    setEditModal({ visible: true, type, index, data: { ...list[index] } });
  };

  const openAdd = (type) => {
    const data = type === 'buy' ? emptyBuy() : type === 'sell' ? emptySell() : emptyLock();
    setEditModal({ visible: true, type, index: null, data });
  };

  const saveEdit = () => {
    const { type, index, data } = editModal;
    setConditions(prev => {
      const key = type === 'buy' ? 'buy_conditions' : type === 'sell' ? 'sell_conditions' : 'lock_conditions';
      const list = [...(prev[key] || [])];
      if (index === null) list.push(data);
      else list[index] = data;
      return { ...prev, [key]: list };
    });
    setEditModal({ visible: false, type: null, index: null, data: null });
  };

  const deleteCondition = (type, index) => {
    Alert.alert('삭제', '이 조건을 삭제할까요?', [
      { text: '취소', style: 'cancel' },
      { text: '삭제', style: 'destructive', onPress: () => {
        setConditions(prev => {
          const key = type === 'buy' ? 'buy_conditions' : type === 'sell' ? 'sell_conditions' : 'lock_conditions';
          const list = [...(prev[key] || [])];
          list.splice(index, 1);
          return { ...prev, [key]: list };
        });
      }},
    ]);
  };

  const updateEditField = (field, value) => {
    setEditModal(prev => ({ ...prev, data: { ...prev.data, [field]: value } }));
  };

  // ── 색상 헬퍼 ─────────────────────────────
  const returnColor = (pct) => {
    if (pct > 0) return '#16A34A';
    if (pct < 0) return '#DC2626';
    return '#64748B';
  };

  const formatMoney = (n) =>
    n >= 0 ? `+${n.toLocaleString()}원` : `${n.toLocaleString()}원`;

  // ── 조건 편집 모달 렌더 ───────────────────
  const renderEditModal = () => {
    if (!editModal.visible || !editModal.data) return null;
    const { type, data } = editModal;
    const isNew = editModal.index === null;

    return (
      <Modal visible transparent animationType="slide" onRequestClose={() => setEditModal(v => ({ ...v, visible: false }))}>
        <KeyboardAvoidingView style={styles.modalOverlay} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <View style={styles.modalBox}>
            <Text style={styles.modalTitle}>
              {isNew ? '➕ 조건 추가' : '✏️ 조건 수정'}
              {type === 'buy' ? ' (매수)' : type === 'sell' ? ' (매도)' : ' (락)'}
            </Text>

            {(type === 'buy' || type === 'sell') && (
              <>
                <Text style={styles.modalLabel}>라벨 (예: Dip 1)</Text>
                <TextInput style={styles.modalInput} value={data.label ?? ''} onChangeText={v => updateEditField('label', v)} placeholder="선택" placeholderTextColor="#94A3B8" />
                <Text style={styles.modalLabel}>종목명</Text>
                <TextInput style={styles.modalInput} value={data.name ?? ''} onChangeText={v => updateEditField('name', v)} placeholder="예: TQQQ" placeholderTextColor="#94A3B8" />
                <Text style={styles.modalLabel}>티커</Text>
                <TextInput style={styles.modalInput} value={data.ticker ?? ''} onChangeText={v => updateEditField('ticker', v)} placeholder="예: TQQQ" placeholderTextColor="#94A3B8" />
              </>
            )}

            {type === 'buy' && (
              <>
                <Text style={styles.modalLabel}>기준 티커 (선택, 예: QQQ)</Text>
                <TextInput style={styles.modalInput} value={data.ref_ticker ?? ''} onChangeText={v => updateEditField('ref_ticker', v)} placeholder="없으면 비워두세요" placeholderTextColor="#94A3B8" />
              </>
            )}

            <Text style={styles.modalLabel}>조건 내용</Text>
            <TextInput
              style={[styles.modalInput, styles.modalInputMulti]}
              value={data.condition ?? ''}
              onChangeText={v => updateEditField('condition', v)}
              placeholder="예: QQQ 고점 대비 -10% 하락 시"
              placeholderTextColor="#94A3B8"
              multiline
            />

            {type === 'buy' && (
              <>
                <Text style={styles.modalLabel}>목표 비중 (%)</Text>
                <TextInput style={styles.modalInput} value={String(data.weight_pct ?? '')} onChangeText={v => updateEditField('weight_pct', v === '' ? '' : Number(v))} keyboardType="numeric" placeholder="예: 30" placeholderTextColor="#94A3B8" />
              </>
            )}

            {type === 'sell' && (
              <>
                <Text style={styles.modalLabel}>매도 비율 (%)</Text>
                <TextInput style={styles.modalInput} value={String(data.sell_pct ?? '')} onChangeText={v => updateEditField('sell_pct', v === '' ? '' : Number(v))} keyboardType="numeric" placeholder="예: 50 (100이면 전량)" placeholderTextColor="#94A3B8" />
              </>
            )}

            {type === 'lock' && (
              <>
                <Text style={styles.modalLabel}>액션</Text>
                <View style={styles.toggleRow}>
                  {['liquidate', 'lock'].map(a => (
                    <TouchableOpacity
                      key={a}
                      style={[styles.toggleBtn, data.action === a && styles.toggleBtnActive]}
                      onPress={() => updateEditField('action', a)}
                    >
                      <Text style={[styles.toggleBtnText, data.action === a && styles.toggleBtnTextActive]}>
                        {a === 'liquidate' ? '전량 청산+잠금' : '신규 매수 잠금'}
                      </Text>
                    </TouchableOpacity>
                  ))}
                </View>
              </>
            )}

            <View style={styles.modalBtns}>
              <TouchableOpacity style={styles.modalCancel} onPress={() => setEditModal(v => ({ ...v, visible: false }))}>
                <Text style={styles.modalCancelText}>취소</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.modalSave} onPress={saveEdit}>
                <Text style={styles.modalSaveText}>저장</Text>
              </TouchableOpacity>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    );
  };

  // ── 조건 상세 렌더 ────────────────────────
  const renderConditionDetail = () => (
    <View style={styles.condDetail}>
      {/* 매수 */}
      <View style={styles.condSectionHeader}>
        <Text style={styles.condDetailHeader}>🟢 매수 조건</Text>
        <TouchableOpacity style={styles.addBtn} onPress={() => openAdd('buy')}>
          <Text style={styles.addBtnText}>➕ 추가</Text>
        </TouchableOpacity>
      </View>
      {(conditions.buy_conditions?.length === 0) && <Text style={styles.emptyText}>조건 없음</Text>}
      {conditions.buy_conditions?.map((c, i) => (
        <View key={i} style={styles.condDetailRow}>
          <View style={styles.condDetailContent}>
            <Text style={styles.condDetailName}>
              {c.label ? `[${c.label}] ` : ''}{c.name || c.ticker || '-'}
              {c.ref_ticker ? ` (기준: ${c.ref_ticker})` : ''}
            </Text>
            <Text style={styles.condDetailCond}>조건: {c.condition}</Text>
            <Text style={styles.condDetailMeta}>
              {c.weight_pct != null
                ? `목표비중 ${c.weight_pct}% (${c.weight_mode === 'add' ? '추가' : '타겟'})`
                : c.qty != null ? `${c.qty}주` : ''}
              {c.ticker ? ` · ${c.ticker}` : ''}
            </Text>
          </View>
          <View style={styles.condActions}>
            <TouchableOpacity style={styles.condEditBtn} onPress={() => openEdit('buy', i)}>
              <Text style={styles.condEditBtnText}>✏️</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.condDelBtn} onPress={() => deleteCondition('buy', i)}>
              <Text style={styles.condDelBtnText}>🗑️</Text>
            </TouchableOpacity>
          </View>
        </View>
      ))}

      {/* 매도 */}
      <View style={styles.condSectionHeader}>
        <Text style={[styles.condDetailHeader, { color: '#DC2626' }]}>🔴 매도 조건</Text>
        <TouchableOpacity style={styles.addBtn} onPress={() => openAdd('sell')}>
          <Text style={styles.addBtnText}>➕ 추가</Text>
        </TouchableOpacity>
      </View>
      {(conditions.sell_conditions?.length === 0) && <Text style={styles.emptyText}>조건 없음</Text>}
      {conditions.sell_conditions?.map((c, i) => (
        <View key={i} style={styles.condDetailRow}>
          <View style={styles.condDetailContent}>
            <Text style={styles.condDetailName}>
              {c.label ? `[${c.label}] ` : ''}{c.name || c.ticker || '-'}
            </Text>
            <Text style={styles.condDetailCond}>조건: {c.condition}</Text>
            <Text style={styles.condDetailMeta}>
              {c.sell_pct != null
                ? `${c.sell_pct}% 매도 (${c.sell_mode === 'initial_qty' ? '최초수량 기준' : '현재보유 기준'})`
                : c.qty === 'all' ? '전량 매도' : c.qty != null ? `${c.qty}주` : ''}
              {c.ticker ? ` · ${c.ticker}` : ''}
            </Text>
          </View>
          <View style={styles.condActions}>
            <TouchableOpacity style={styles.condEditBtn} onPress={() => openEdit('sell', i)}>
              <Text style={styles.condEditBtnText}>✏️</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.condDelBtn} onPress={() => deleteCondition('sell', i)}>
              <Text style={styles.condDelBtnText}>🗑️</Text>
            </TouchableOpacity>
          </View>
        </View>
      ))}

      {/* 락 */}
      <View style={styles.condSectionHeader}>
        <Text style={[styles.condDetailHeader, { color: '#F59E0B' }]}>🔒 락 조건</Text>
        <TouchableOpacity style={styles.addBtn} onPress={() => openAdd('lock')}>
          <Text style={styles.addBtnText}>➕ 추가</Text>
        </TouchableOpacity>
      </View>
      {(conditions.lock_conditions?.length === 0) && <Text style={styles.emptyText}>조건 없음</Text>}
      {conditions.lock_conditions?.map((c, i) => (
        <View key={i} style={styles.condDetailRow}>
          <View style={styles.condDetailContent}>
            <Text style={styles.condDetailCond}>{c.condition}</Text>
            <Text style={styles.condDetailMeta}>
              {c.action === 'liquidate' ? '전량 청산 + 매수 잠금' : '신규 매수 잠금'}
            </Text>
          </View>
          <View style={styles.condActions}>
            <TouchableOpacity style={styles.condEditBtn} onPress={() => openEdit('lock', i)}>
              <Text style={styles.condEditBtnText}>✏️</Text>
            </TouchableOpacity>
            <TouchableOpacity style={styles.condDelBtn} onPress={() => deleteCondition('lock', i)}>
              <Text style={styles.condDelBtnText}>🗑️</Text>
            </TouchableOpacity>
          </View>
        </View>
      ))}
    </View>
  );

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
        <View style={[styles.bigCard, { borderColor: returnColor(s.total_return_pct) }]}>
          <Text style={styles.bigCardLabel}>총 수익률</Text>
          <Text style={[styles.bigCardValue, { color: returnColor(s.total_return_pct) }]}>
            {s.total_return_pct > 0 ? '+' : ''}{s.total_return_pct}%
          </Text>
          <Text style={[styles.bigCardSub, { color: returnColor(s.total_return_pct) }]}>
            {formatMoney(s.total_pnl)}
          </Text>
        </View>
        <View style={styles.metricGrid}>
          <MetricBox label="최종 평가액" value={`${s.final_value?.toLocaleString()}원`} />
          <MetricBox label="총 거래횟수" value={`${s.trade_count}회`} />
          <MetricBox label="승률" value={`${s.win_rate}%`} valueColor={s.win_rate >= 50 ? '#16A34A' : '#DC2626'} />
          <MetricBox label="최대 낙폭(MDD)" value={`-${s.mdd_pct}%`} valueColor="#DC2626" />
          <MetricBox label="테스트 기간" value={`${s.period_days}일`} />
          <MetricBox label="초기 자본" value={`${s.initial_cash?.toLocaleString()}원`} />
        </View>
      </>
    );
  };

  const renderPerStock = () => (
    <>
      <Text style={styles.sectionTitle}>📊 종목별 결과</Text>
      {result.per_stock.length === 0 && <Text style={styles.emptyText}>체결된 매도 거래가 없습니다.</Text>}
      {result.per_stock.map((s) => (
        <View key={s.ticker} style={styles.stockRow}>
          <View style={styles.stockInfo}>
            <Text style={styles.stockName}>{s.name}</Text>
            <Text style={styles.stockMeta}>{s.ticker} · {s.trade_count}회 거래 · 승률 {s.win_rate}%</Text>
          </View>
          <Text style={[styles.stockPnl, { color: returnColor(s.pnl) }]}>{formatMoney(s.pnl)}</Text>
        </View>
      ))}
    </>
  );

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
            <Text style={styles.logMeta}>{t.price?.toLocaleString()}원 · {t.qty}주 · {t.date}</Text>
            {t.pnl != null && (
              <Text style={[styles.logPnl, { color: returnColor(t.pnl) }]}>
                {formatMoney(t.pnl)} ({t.pnl_pct > 0 ? '+' : ''}{t.pnl_pct}%)
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
        <View style={styles.imageRow}>
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

          {/* 썸네일 */}
          {imageUri && (
            <TouchableOpacity onPress={() => setImageModalVisible(true)} style={styles.thumbnail}>
              <Image source={{ uri: imageUri }} style={styles.thumbnailImg} />
              <Text style={styles.thumbnailLabel}>원본 보기</Text>
            </TouchableOpacity>
          )}
        </View>

        {analyzing && <Text style={styles.subText}>AI가 조건 분석 중...</Text>}

        {/* 인라인 에러 */}
        {analyzeError && (
          <View style={styles.inlineError}>
            <Text style={styles.inlineErrorText}>⚠️ 분석 실패: {analyzeError}</Text>
            <Text style={styles.inlineErrorSub}>이미지는 저장됐습니다. 아래에서 조건을 직접 수정하세요.</Text>
          </View>
        )}

        {/* ── 추출된 조건 요약 ── */}
        {conditions && (
          <TouchableOpacity
            style={styles.condSummaryCard}
            onPress={() => setCondExpanded(v => !v)}
            activeOpacity={0.8}
          >
            <View style={styles.condSummaryHeader}>
              <Text style={styles.condSummaryTitle}>📋 추출된 조건</Text>
              <Text style={styles.condToggleIcon}>{condExpanded ? '▲' : '▼'}</Text>
            </View>
            <Text style={styles.condSummaryText}>{conditions.summary}</Text>
            <Text style={styles.condSummaryMeta}>
              매수 {conditions.buy_conditions?.length ?? 0}건 · 매도 {conditions.sell_conditions?.length ?? 0}건
              {conditions.lock_conditions?.length > 0 ? ` · 락 ${conditions.lock_conditions.length}건` : ''} · 탭하면 상세보기
            </Text>

            {condExpanded && renderConditionDetail()}
          </TouchableOpacity>
        )}

        {/* ── 초기 자본 입력 ── */}
        <Text style={styles.sectionTitle}>💰 초기 자본 (원)</Text>
        <View style={styles.cashRow}>
          {[1000000, 5000000, 10000000, 50000000].map((v) => (
            <TouchableOpacity
              key={v}
              style={[styles.cashBtn, cashInput === String(v) && styles.periodBtnActive]}
              onPress={() => setCashInput(String(v))}
            >
              <Text style={[styles.periodBtnText, cashInput === String(v) && styles.periodBtnTextActive]}>
                {v >= 10000000 ? `${v/10000000}천만` : `${v/10000}만`}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
        <TextInput
          style={styles.cashInput}
          value={cashInput}
          onChangeText={setCashInput}
          keyboardType="numeric"
          placeholder="직접 입력 (예: 30000000)"
          placeholderTextColor="#94A3B8"
        />

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

      {/* ── 원본 이미지 풀스크린 모달 ── */}
      <Modal visible={imageModalVisible} transparent animationType="fade" onRequestClose={() => setImageModalVisible(false)}>
        <View style={styles.imageModalOverlay}>
          <TouchableOpacity style={styles.imageModalClose} onPress={() => setImageModalVisible(false)}>
            <Text style={styles.imageModalCloseText}>✕ 닫기</Text>
          </TouchableOpacity>
          {imageUri && (
            <Image
              source={{ uri: imageUri }}
              style={styles.imageModalImg}
              resizeMode="contain"
            />
          )}
        </View>
      </Modal>

      {/* ── 조건 편집 모달 ── */}
      {renderEditModal()}
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

  // 이미지 영역
  imageRow: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  imageButtons: { flex: 1, gap: 10 },
  imgBtn: { backgroundColor: '#6366F1', padding: 14, borderRadius: 10, alignItems: 'center' },
  imgBtnSec: { backgroundColor: '#fff', borderWidth: 1.5, borderColor: '#6366F1' },
  imgBtnText: { color: '#fff', fontWeight: '700', fontSize: 14 },
  subText: { textAlign: 'center', color: '#6366F1', marginTop: 8, fontSize: 13 },
  btnDisabled: { opacity: 0.5 },

  // 썸네일
  thumbnail: { alignItems: 'center', gap: 4 },
  thumbnailImg: { width: 72, height: 72, borderRadius: 10, borderWidth: 2, borderColor: '#6366F1' },
  thumbnailLabel: { fontSize: 11, color: '#6366F1', fontWeight: '600' },

  // 인라인 에러
  inlineError: { backgroundColor: '#FEE2E2', borderRadius: 10, padding: 12, marginTop: 10 },
  inlineErrorText: { color: '#DC2626', fontWeight: '700', fontSize: 13 },
  inlineErrorSub: { color: '#991B1B', fontSize: 12, marginTop: 4 },

  // 조건 카드
  condSummaryCard: {
    backgroundColor: '#fff', borderRadius: 10, padding: 14, marginTop: 12,
    borderLeftWidth: 3, borderLeftColor: '#6366F1',
  },
  condSummaryHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  condSummaryTitle: { fontSize: 13, fontWeight: '700', color: '#6366F1' },
  condToggleIcon:   { fontSize: 12, color: '#6366F1' },
  condSummaryText:  { fontSize: 13, color: '#334155' },
  condSummaryMeta:  { fontSize: 12, color: '#94A3B8', marginTop: 6 },
  condDetail:       { marginTop: 12, borderTopWidth: 1, borderTopColor: '#E2E8F0', paddingTop: 10 },

  condSectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6, marginTop: 4 },
  condDetailHeader: { fontSize: 13, fontWeight: '700', color: '#16A34A' },
  addBtn: { backgroundColor: '#EEF2FF', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6 },
  addBtnText: { fontSize: 12, color: '#6366F1', fontWeight: '600' },

  condDetailRow: { backgroundColor: '#F8FAFC', borderRadius: 8, padding: 10, marginBottom: 6, flexDirection: 'row', alignItems: 'flex-start' },
  condDetailContent: { flex: 1 },
  condDetailName: { fontSize: 13, fontWeight: '700', color: '#1E293B' },
  condDetailCond: { fontSize: 12, color: '#475569', marginTop: 2 },
  condDetailMeta: { fontSize: 11, color: '#94A3B8', marginTop: 2 },

  condActions: { flexDirection: 'row', gap: 6, marginLeft: 8, alignItems: 'center' },
  condEditBtn: { backgroundColor: '#EEF2FF', borderRadius: 6, padding: 6 },
  condEditBtnText: { fontSize: 14 },
  condDelBtn: { backgroundColor: '#FEE2E2', borderRadius: 6, padding: 6 },
  condDelBtnText: { fontSize: 14 },

  // 자본/기간
  cashRow: { flexDirection: 'row', gap: 8, marginBottom: 8 },
  cashBtn: {
    flex: 1, paddingVertical: 10, borderRadius: 8,
    backgroundColor: '#fff', borderWidth: 1, borderColor: '#CBD5E1', alignItems: 'center',
  },
  cashInput: {
    backgroundColor: '#fff', borderRadius: 10, borderWidth: 1, borderColor: '#CBD5E1',
    paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: '#1E293B', marginBottom: 4,
  },
  periodRow: { flexDirection: 'row', gap: 8 },
  periodBtn: {
    flex: 1, paddingVertical: 10, borderRadius: 8,
    backgroundColor: '#fff', borderWidth: 1, borderColor: '#CBD5E1', alignItems: 'center',
  },
  periodBtnActive: { backgroundColor: '#6366F1', borderColor: '#6366F1' },
  periodBtnText:   { fontSize: 13, fontWeight: '600', color: '#475569' },
  periodBtnTextActive: { color: '#fff' },

  // 실행 버튼
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

  // 원본 이미지 모달
  imageModalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.92)', justifyContent: 'center', alignItems: 'center' },
  imageModalClose: { position: 'absolute', top: 50, right: 20, zIndex: 10, backgroundColor: 'rgba(255,255,255,0.2)', padding: 10, borderRadius: 8 },
  imageModalCloseText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  imageModalImg: { width: '100%', height: '80%' },

  // 편집 모달
  modalOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'flex-end' },
  modalBox: { backgroundColor: '#fff', borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 20, maxHeight: '85%' },
  modalTitle: { fontSize: 16, fontWeight: '800', color: '#1E293B', marginBottom: 16 },
  modalLabel: { fontSize: 12, fontWeight: '600', color: '#64748B', marginBottom: 4, marginTop: 10 },
  modalInput: { backgroundColor: '#F8FAFC', borderWidth: 1, borderColor: '#E2E8F0', borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, fontSize: 14, color: '#1E293B' },
  modalInputMulti: { height: 80, textAlignVertical: 'top' },
  toggleRow: { flexDirection: 'row', gap: 8, marginTop: 4 },
  toggleBtn: { flex: 1, paddingVertical: 10, borderRadius: 8, backgroundColor: '#F1F5F9', borderWidth: 1, borderColor: '#CBD5E1', alignItems: 'center' },
  toggleBtnActive: { backgroundColor: '#6366F1', borderColor: '#6366F1' },
  toggleBtnText: { fontSize: 12, fontWeight: '600', color: '#475569' },
  toggleBtnTextActive: { color: '#fff' },
  modalBtns: { flexDirection: 'row', gap: 10, marginTop: 20 },
  modalCancel: { flex: 1, paddingVertical: 14, borderRadius: 10, backgroundColor: '#F1F5F9', alignItems: 'center' },
  modalCancelText: { fontSize: 15, fontWeight: '700', color: '#64748B' },
  modalSave: { flex: 1, paddingVertical: 14, borderRadius: 10, backgroundColor: '#6366F1', alignItems: 'center' },
  modalSaveText: { fontSize: 15, fontWeight: '700', color: '#fff' },
});
