/**
 * BacktestScreen.js
 * 이미지 조건 → 과거 데이터 백테스트 결과 표시
 */

import React, { useState, useRef } from 'react';
import {
  View,
  Text,
  ScrollView,
  FlatList,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  Alert,
  TextInput,
  Image,
  Modal,
  KeyboardAvoidingView,
  Platform,
  Dimensions,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import { analyzeTradeImage, runBacktest } from '../services/api';

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');

const PERIODS = [
  { label: '1개월', days: 30 },
  { label: '3개월', days: 90 },
  { label: '6개월', days: 180 },
  { label: '1년',   days: 365 },
];

// 자주 쓰는 기본 티커 목록
const DEFAULT_TICKERS = ['TQQQ', 'QQQ', 'SPY', 'SQQQ', 'TLT', 'GLD', 'UPRO', 'TMF', 'UVXY'];

const emptyBuy  = () => ({ label: '', name: '', ticker: '', ref_ticker: '', condition: '', weight_pct: '', weight_mode: 'add' });
const emptySell = () => ({ label: '', name: '', ticker: '', condition: '', sell_pct: '', sell_mode: 'initial_qty' });
const emptyLock = () => ({ condition: '', action: 'liquidate' });

// 조건 유효성 검증
function validateCondition(type, data) {
  if (type === 'buy' || type === 'sell') {
    if (!data.ticker && !data.name) return '종목명 또는 티커를 입력하세요.';
    if (!data.condition) return '조건 내용을 입력하세요.';
  }
  if (type === 'lock') {
    if (!data.condition) return '조건 내용을 입력하세요.';
  }
  return null;
}

export default function BacktestScreen() {
  const [analyzing, setAnalyzing]   = useState(false);
  const [running,   setRunning]     = useState(false);
  const [conditions, setConditions] = useState(null);
  const [result,    setResult]      = useState(null);
  const [period,    setPeriod]      = useState(90);
  const [cashInput, setCashInput]   = useState('10000000');
  const [condExpanded, setCondExpanded] = useState(false);

  // 다중 이미지
  const [imageUris, setImageUris]   = useState([]);
  const [analyzeError, setAnalyzeError] = useState(null);

  // 갤러리 모달
  const [galleryVisible, setGalleryVisible] = useState(false);
  const [galleryIndex,   setGalleryIndex]   = useState(0);
  const galleryRef = useRef(null);

  // 조건 편집 모달
  const [editModal,    setEditModal]    = useState({ visible: false, type: null, index: null, data: null });
  const [editError,    setEditError]    = useState(null);

  // ── 기존 조건에서 티커 목록 추출 ────────────
  const knownTickers = React.useMemo(() => {
    const set = new Set(DEFAULT_TICKERS);
    if (!conditions) return [...set];
    [...(conditions.buy_conditions || []), ...(conditions.sell_conditions || [])].forEach(c => {
      if (c.ticker) set.add(c.ticker);
      if (c.ref_ticker) set.add(c.ref_ticker);
    });
    return [...set];
  }, [conditions]);

  const knownLabels = React.useMemo(() => {
    const set = new Set();
    [...(conditions?.buy_conditions || []), ...(conditions?.sell_conditions || [])].forEach(c => {
      if (c.label) set.add(c.label);
    });
    return [...set];
  }, [conditions]);

  // ── 이미지 선택 & 분석 ──────────────────────
  const pickImage = async (fromCamera = false) => {
    const picker = fromCamera ? ImagePicker.launchCameraAsync : ImagePicker.launchImageLibraryAsync;
    const permFn = fromCamera ? ImagePicker.requestCameraPermissionsAsync : ImagePicker.requestMediaLibraryPermissionsAsync;

    const { status } = await permFn();
    if (status !== 'granted') { setAnalyzeError('사진 접근 권한이 필요합니다.'); return; }

    const res = await picker({ allowsEditing: false, quality: 0.85, allowsMultipleSelection: !fromCamera });
    if (res.canceled) return;

    const assets = res.assets;
    setImageUris(prev => [...prev, ...assets.map(a => a.uri)]);
    setAnalyzing(true);
    setAnalyzeError(null);
    setResult(null);

    try {
      const asset = assets[0];
      const ext   = asset.uri.split('.').pop().toLowerCase();
      const mime  = ext === 'png' ? 'image/png' : 'image/jpeg';
      const data  = await analyzeTradeImage(asset.uri, mime);
      setConditions(prev => {
        if (!prev) return data.conditions;
        return {
          ...data.conditions,
          buy_conditions:  [...(prev.buy_conditions  || []), ...(data.conditions.buy_conditions  || [])],
          sell_conditions: [...(prev.sell_conditions || []), ...(data.conditions.sell_conditions || [])],
          lock_conditions: [...(prev.lock_conditions || []), ...(data.conditions.lock_conditions || [])],
        };
      });
      setCondExpanded(true);
    } catch (e) {
      setAnalyzeError(e?.message ?? String(e));
      if (!conditions) setConditions({ summary: '수동 입력', buy_conditions: [], sell_conditions: [], lock_conditions: [] });
    } finally {
      setAnalyzing(false);
    }
  };

  const removeImage = (index) => {
    Alert.alert('삭제', '이 이미지를 제거할까요?', [
      { text: '취소', style: 'cancel' },
      { text: '삭제', style: 'destructive', onPress: () => setImageUris(prev => prev.filter((_, i) => i !== index)) },
    ]);
  };

  const openGallery = (index) => { setGalleryIndex(index); setGalleryVisible(true); };

  // ── 백테스트 실행 ──────────────────────────
  const doBacktest = async () => {
    if (!conditions) { Alert.alert('조건 없음', '먼저 이미지를 업로드하거나 조건을 입력하세요.'); return; }
    setRunning(true); setResult(null);
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
    const list = type === 'buy' ? conditions.buy_conditions : type === 'sell' ? conditions.sell_conditions : conditions.lock_conditions;
    setEditError(null);
    setEditModal({ visible: true, type, index, data: { ...list[index] } });
  };

  const openAdd = (type) => {
    const data = type === 'buy' ? emptyBuy() : type === 'sell' ? emptySell() : emptyLock();
    setEditError(null);
    setEditModal({ visible: true, type, index: null, data });
  };

  const saveEdit = () => {
    const { type, index, data } = editModal;
    const err = validateCondition(type, data);
    if (err) { setEditError(err); return; }

    setConditions(prev => {
      const key  = type === 'buy' ? 'buy_conditions' : type === 'sell' ? 'sell_conditions' : 'lock_conditions';
      const list = [...(prev[key] || [])];
      if (index === null) list.push(data);
      else list[index] = data;
      return { ...prev, [key]: list };
    });
    setEditModal({ visible: false, type: null, index: null, data: null });
    setEditError(null);
  };

  const deleteCondition = (type, index) => {
    Alert.alert('삭제', '이 조건을 삭제할까요?', [
      { text: '취소', style: 'cancel' },
      { text: '삭제', style: 'destructive', onPress: () => {
        setConditions(prev => {
          const key  = type === 'buy' ? 'buy_conditions' : type === 'sell' ? 'sell_conditions' : 'lock_conditions';
          const list = [...(prev[key] || [])];
          list.splice(index, 1);
          return { ...prev, [key]: list };
        });
      }},
    ]);
  };

  const setField = (field, value) => setEditModal(prev => ({ ...prev, data: { ...prev.data, [field]: value } }));

  // ── 색상 헬퍼 ─────────────────────────────
  const returnColor = (pct) => pct > 0 ? '#16A34A' : pct < 0 ? '#DC2626' : '#64748B';
  const formatMoney = (n) => n >= 0 ? `+${n.toLocaleString()}원` : `${n.toLocaleString()}원`;

  // ── 칩 선택 컴포넌트 ─────────────────────
  const ChipRow = ({ items, onSelect, selected }) => {
    if (!items || items.length === 0) return null;
    return (
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.chipScroll}>
        {items.map((item, i) => (
          <TouchableOpacity
            key={i}
            style={[styles.chip, selected === item && styles.chipActive]}
            onPress={() => onSelect(item)}
          >
            <Text style={[styles.chipText, selected === item && styles.chipTextActive]}>{item}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>
    );
  };

  // ── 조건 편집 모달 ────────────────────────
  const renderEditModal = () => {
    if (!editModal.visible || !editModal.data) return null;
    const { type, data } = editModal;
    const isNew = editModal.index === null;

    return (
      <Modal visible transparent animationType="slide" onRequestClose={() => setEditModal(v => ({ ...v, visible: false }))}>
        <KeyboardAvoidingView
          style={{ flex: 1, justifyContent: 'flex-end' }}
          behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        >
          {/* 배경 탭으로 닫기 */}
          <TouchableOpacity style={StyleSheet.absoluteFill} activeOpacity={1} onPress={() => setEditModal(v => ({ ...v, visible: false }))} />

          <View style={styles.modalBox}>
            {/* 타이틀 */}
            <Text style={styles.modalTitle}>
              {isNew ? '➕ 조건 추가' : '✏️ 조건 수정'}
              {type === 'buy' ? ' (매수)' : type === 'sell' ? ' (매도)' : ' (락)'}
            </Text>

            {/* 에러 */}
            {editError && <View style={styles.modalError}><Text style={styles.modalErrorText}>⚠️ {editError}</Text></View>}

            {/* 스크롤 필드 영역 */}
            <ScrollView
              style={styles.modalScroll}
              contentContainerStyle={{ paddingBottom: 8 }}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              {(type === 'buy' || type === 'sell') && (
                <>
                  <Text style={styles.modalLabel}>라벨</Text>
                  <TextInput style={styles.modalInput} value={data.label ?? ''} onChangeText={v => setField('label', v)} placeholder="예: Dip 1" placeholderTextColor="#94A3B8" />
                  {knownLabels.size > 0 && (
                    <ChipRow items={[...knownLabels]} selected={data.label} onSelect={v => setField('label', v)} />
                  )}

                  <Text style={styles.modalLabel}>티커 <Text style={styles.modalLabelRequired}>*</Text></Text>
                  <TextInput
                    style={[styles.modalInput, !data.ticker && editError ? styles.modalInputErr : null]}
                    value={data.ticker ?? ''}
                    onChangeText={v => { setField('ticker', v); if (!data.name) setField('name', v); }}
                    placeholder="예: TQQQ"
                    placeholderTextColor="#94A3B8"
                    autoCapitalize="characters"
                  />
                  <ChipRow items={knownTickers} selected={data.ticker} onSelect={v => { setField('ticker', v); if (!data.name) setField('name', v); }} />

                  <Text style={styles.modalLabel}>종목명</Text>
                  <TextInput style={styles.modalInput} value={data.name ?? ''} onChangeText={v => setField('name', v)} placeholder="예: TQQQ (티커와 동일하면 생략가능)" placeholderTextColor="#94A3B8" />
                </>
              )}

              {type === 'buy' && (
                <>
                  <Text style={styles.modalLabel}>기준 티커 (선택)</Text>
                  <TextInput style={styles.modalInput} value={data.ref_ticker ?? ''} onChangeText={v => setField('ref_ticker', v)} placeholder="예: QQQ" placeholderTextColor="#94A3B8" autoCapitalize="characters" />
                  <ChipRow items={knownTickers} selected={data.ref_ticker} onSelect={v => setField('ref_ticker', v)} />
                </>
              )}

              <Text style={styles.modalLabel}>조건 내용 <Text style={styles.modalLabelRequired}>*</Text></Text>
              <TextInput
                style={[styles.modalInput, styles.modalInputMulti, !data.condition && editError ? styles.modalInputErr : null]}
                value={data.condition ?? ''}
                onChangeText={v => setField('condition', v)}
                placeholder="예: QQQ 고점 대비 -10% 하락 시"
                placeholderTextColor="#94A3B8"
                multiline
              />

              {type === 'buy' && (
                <>
                  <Text style={styles.modalLabel}>목표 비중 (%)</Text>
                  <TextInput style={styles.modalInput} value={String(data.weight_pct ?? '')} onChangeText={v => setField('weight_pct', v === '' ? '' : Number(v))} keyboardType="numeric" placeholder="예: 30" placeholderTextColor="#94A3B8" />
                  <ChipRow items={['10', '15', '20', '30', '50', '70', '100']} selected={String(data.weight_pct ?? '')} onSelect={v => setField('weight_pct', Number(v))} />

                  <Text style={styles.modalLabel}>비중 모드</Text>
                  <View style={styles.toggleRow}>
                    {[{ v: 'add', label: '추가' }, { v: 'target', label: '타겟' }].map(opt => (
                      <TouchableOpacity key={opt.v} style={[styles.toggleBtn, data.weight_mode === opt.v && styles.toggleBtnActive]} onPress={() => setField('weight_mode', opt.v)}>
                        <Text style={[styles.toggleBtnText, data.weight_mode === opt.v && styles.toggleBtnTextActive]}>{opt.label}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                </>
              )}

              {type === 'sell' && (
                <>
                  <Text style={styles.modalLabel}>매도 비율 (%)</Text>
                  <TextInput style={styles.modalInput} value={String(data.sell_pct ?? '')} onChangeText={v => setField('sell_pct', v === '' ? '' : Number(v))} keyboardType="numeric" placeholder="예: 50 (100이면 전량)" placeholderTextColor="#94A3B8" />
                  <ChipRow items={['25', '50', '75', '100']} selected={String(data.sell_pct ?? '')} onSelect={v => setField('sell_pct', Number(v))} />

                  <Text style={styles.modalLabel}>매도 기준</Text>
                  <View style={styles.toggleRow}>
                    {[{ v: 'initial_qty', label: '최초수량 기준' }, { v: 'current_qty', label: '현재보유 기준' }].map(opt => (
                      <TouchableOpacity key={opt.v} style={[styles.toggleBtn, data.sell_mode === opt.v && styles.toggleBtnActive]} onPress={() => setField('sell_mode', opt.v)}>
                        <Text style={[styles.toggleBtnText, data.sell_mode === opt.v && styles.toggleBtnTextActive]}>{opt.label}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                </>
              )}

              {type === 'lock' && (
                <>
                  <Text style={styles.modalLabel}>락 액션</Text>
                  <View style={styles.toggleRow}>
                    {[{ v: 'liquidate', label: '전량 청산+잠금' }, { v: 'lock', label: '신규 매수 잠금' }].map(opt => (
                      <TouchableOpacity key={opt.v} style={[styles.toggleBtn, data.action === opt.v && styles.toggleBtnActive]} onPress={() => setField('action', opt.v)}>
                        <Text style={[styles.toggleBtnText, data.action === opt.v && styles.toggleBtnTextActive]}>{opt.label}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                </>
              )}
            </ScrollView>

            {/* 버튼 — 스크롤 밖에 고정 */}
            <View style={styles.modalBtns}>
              <TouchableOpacity style={styles.modalCancel} onPress={() => { setEditModal(v => ({ ...v, visible: false })); setEditError(null); }}>
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

  // ── 갤러리 모달 ───────────────────────────
  const renderGalleryModal = () => (
    <Modal visible={galleryVisible} transparent animationType="fade" onRequestClose={() => setGalleryVisible(false)}>
      <View style={styles.galleryOverlay}>
        <View style={styles.galleryHeader}>
          <Text style={styles.galleryPageText}>{galleryIndex + 1} / {imageUris.length}</Text>
          <TouchableOpacity style={styles.galleryCloseBtn} onPress={() => setGalleryVisible(false)}>
            <Text style={styles.galleryCloseText}>✕ 닫기</Text>
          </TouchableOpacity>
        </View>
        <FlatList
          ref={galleryRef}
          data={imageUris}
          horizontal pagingEnabled
          showsHorizontalScrollIndicator={false}
          initialScrollIndex={galleryIndex}
          getItemLayout={(_, i) => ({ length: SCREEN_W, offset: SCREEN_W * i, index: i })}
          onMomentumScrollEnd={e => setGalleryIndex(Math.round(e.nativeEvent.contentOffset.x / SCREEN_W))}
          keyExtractor={(_, i) => String(i)}
          renderItem={({ item }) => (
            <ScrollView style={{ width: SCREEN_W, height: SCREEN_H }} contentContainerStyle={styles.galleryImgContainer} maximumZoomScale={5} minimumZoomScale={1} centerContent showsVerticalScrollIndicator={false} showsHorizontalScrollIndicator={false}>
              <Image source={{ uri: item }} style={{ width: SCREEN_W, height: SCREEN_H * 0.85 }} resizeMode="contain" />
            </ScrollView>
          )}
        />
        {imageUris.length > 1 && (
          <View style={styles.galleryDots}>
            {imageUris.map((_, i) => <View key={i} style={[styles.galleryDot, i === galleryIndex && styles.galleryDotActive]} />)}
          </View>
        )}
      </View>
    </Modal>
  );

  // ── 조건 상세 ────────────────────────────
  const renderConditionDetail = () => (
    <View style={styles.condDetail}>
      {[
        { type: 'buy',  label: '🟢 매수 조건', color: '#16A34A', list: conditions.buy_conditions },
        { type: 'sell', label: '🔴 매도 조건', color: '#DC2626', list: conditions.sell_conditions },
        { type: 'lock', label: '🔒 락 조건',   color: '#F59E0B', list: conditions.lock_conditions },
      ].map(({ type, label, color, list }) => (
        <View key={type}>
          <View style={styles.condSectionHeader}>
            <Text style={[styles.condDetailHeader, { color }]}>{label}</Text>
            <TouchableOpacity style={styles.addBtn} onPress={() => openAdd(type)}>
              <Text style={styles.addBtnText}>➕ 추가</Text>
            </TouchableOpacity>
          </View>
          {(!list || list.length === 0) && <Text style={styles.emptyText}>조건 없음</Text>}
          {(list || []).map((c, i) => (
            <View key={i} style={styles.condDetailRow}>
              <View style={styles.condDetailContent}>
                {type !== 'lock' && (
                  <Text style={styles.condDetailName}>
                    {c.label ? `[${c.label}] ` : ''}{c.name || c.ticker || '-'}
                    {c.ref_ticker ? ` (기준: ${c.ref_ticker})` : ''}
                  </Text>
                )}
                <Text style={styles.condDetailCond}>조건: {c.condition}</Text>
                <Text style={styles.condDetailMeta}>
                  {type === 'buy' && c.weight_pct != null ? `목표비중 ${c.weight_pct}% (${c.weight_mode === 'add' ? '추가' : '타겟'})` : ''}
                  {type === 'sell' && c.sell_pct != null ? `${c.sell_pct}% 매도 (${c.sell_mode === 'initial_qty' ? '최초수량' : '현재보유'} 기준)` : ''}
                  {type === 'lock' ? (c.action === 'liquidate' ? '전량 청산 + 매수 잠금' : '신규 매수 잠금') : ''}
                  {(type === 'buy' || type === 'sell') && c.ticker ? ` · ${c.ticker}` : ''}
                </Text>
              </View>
              <View style={styles.condActions}>
                <TouchableOpacity style={styles.condEditBtn} onPress={() => openEdit(type, i)}>
                  <Text>✏️</Text>
                </TouchableOpacity>
                <TouchableOpacity style={styles.condDelBtn} onPress={() => deleteCondition(type, i)}>
                  <Text>🗑️</Text>
                </TouchableOpacity>
              </View>
            </View>
          ))}
        </View>
      ))}
    </View>
  );

  // ── 결과 렌더 ─────────────────────────────
  const renderSummary = () => {
    const s = result.summary;
    if (s.error) return <View style={styles.errorCard}><Text style={styles.errorCardText}>⚠️ {s.error}</Text></View>;
    return (
      <>
        <View style={[styles.bigCard, { borderColor: returnColor(s.total_return_pct) }]}>
          <Text style={styles.bigCardLabel}>총 수익률</Text>
          <Text style={[styles.bigCardValue, { color: returnColor(s.total_return_pct) }]}>{s.total_return_pct > 0 ? '+' : ''}{s.total_return_pct}%</Text>
          <Text style={[styles.bigCardSub, { color: returnColor(s.total_return_pct) }]}>{formatMoney(s.total_pnl)}</Text>
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

  return (
    <SafeAreaView style={styles.container} edges={['bottom']}>
      <ScrollView contentContainerStyle={styles.scroll}>

        {/* ── 이미지 업로드 ── */}
        <Text style={styles.sectionTitle}>📸 조건 이미지 등록</Text>
        <View style={styles.uploadBtns}>
          <TouchableOpacity style={[styles.imgBtn, analyzing && styles.btnDisabled]} onPress={() => pickImage(false)} disabled={analyzing}>
            {analyzing ? <ActivityIndicator color="#fff" /> : <Text style={styles.imgBtnText}>📁 갤러리</Text>}
          </TouchableOpacity>
          <TouchableOpacity style={[styles.imgBtn, styles.imgBtnSec, analyzing && styles.btnDisabled]} onPress={() => pickImage(true)} disabled={analyzing}>
            <Text style={[styles.imgBtnText, { color: '#6366F1' }]}>📷 카메라</Text>
          </TouchableOpacity>
        </View>

        {imageUris.length > 0 && (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.thumbList}>
            {imageUris.map((uri, i) => (
              <TouchableOpacity key={i} style={styles.thumbWrap} onPress={() => openGallery(i)}>
                <Image source={{ uri }} style={styles.thumbImg} />
                <TouchableOpacity style={styles.thumbDel} onPress={() => removeImage(i)}>
                  <Text style={styles.thumbDelText}>✕</Text>
                </TouchableOpacity>
                <Text style={styles.thumbLabel}>사진 {i + 1}</Text>
              </TouchableOpacity>
            ))}
            <TouchableOpacity style={styles.thumbAdd} onPress={() => pickImage(false)}>
              <Text style={styles.thumbAddIcon}>＋</Text>
              <Text style={styles.thumbAddText}>추가</Text>
            </TouchableOpacity>
          </ScrollView>
        )}

        {analyzing && <Text style={styles.subText}>AI가 조건 분석 중...</Text>}
        {analyzeError && (
          <View style={styles.inlineError}>
            <Text style={styles.inlineErrorText}>⚠️ 분석 실패: {analyzeError}</Text>
            <Text style={styles.inlineErrorSub}>조건을 직접 입력하거나 수정하세요.</Text>
          </View>
        )}

        {/* ── 추출된 조건 ── */}
        {conditions && (
          <TouchableOpacity style={styles.condSummaryCard} onPress={() => setCondExpanded(v => !v)} activeOpacity={0.8}>
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

        {/* ── 초기 자본 ── */}
        <Text style={styles.sectionTitle}>💰 초기 자본 (원)</Text>
        <View style={styles.cashRow}>
          {[1000000, 5000000, 10000000, 50000000].map((v) => (
            <TouchableOpacity key={v} style={[styles.cashBtn, cashInput === String(v) && styles.periodBtnActive]} onPress={() => setCashInput(String(v))}>
              <Text style={[styles.periodBtnText, cashInput === String(v) && styles.periodBtnTextActive]}>
                {v >= 10000000 ? `${v/10000000}천만` : `${v/10000}만`}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
        <TextInput style={styles.cashInput} value={cashInput} onChangeText={setCashInput} keyboardType="numeric" placeholder="직접 입력 (예: 30000000)" placeholderTextColor="#94A3B8" />

        {/* ── 기간 ── */}
        <Text style={styles.sectionTitle}>📅 백테스트 기간</Text>
        <View style={styles.periodRow}>
          {PERIODS.map((p) => (
            <TouchableOpacity key={p.days} style={[styles.periodBtn, period === p.days && styles.periodBtnActive]} onPress={() => setPeriod(p.days)}>
              <Text style={[styles.periodBtnText, period === p.days && styles.periodBtnTextActive]}>{p.label}</Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* ── 실행 ── */}
        <TouchableOpacity style={[styles.runBtn, (running || analyzing || !conditions) && styles.btnDisabled]} onPress={doBacktest} disabled={running || analyzing || !conditions}>
          {running ? <ActivityIndicator color="#fff" size="large" /> : <Text style={styles.runBtnText}>🔍 백테스트 실행</Text>}
        </TouchableOpacity>
        {running && <Text style={styles.subText}>과거 데이터 다운로드 및 시뮬레이션 중...</Text>}

        {/* ── 결과 ── */}
        {result && (
          <>
            <Text style={styles.sectionTitle}>📈 백테스트 결과</Text>
            {renderSummary()}
            {result.per_stock && (
              <>
                <Text style={styles.sectionTitle}>📊 종목별 결과</Text>
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
            )}
            {result.trade_log && (
              <>
                <Text style={styles.sectionTitle}>📜 거래 내역</Text>
                {result.trade_log.slice(0, 50).map((t, i) => (
                  <View key={i} style={styles.logRow}>
                    <Text style={[styles.logAction, t.action === '매수' ? styles.buyText : t.action === '매도' || t.action === '청산' ? styles.sellText : styles.mutedText]}>{t.action}</Text>
                    <View style={styles.logInfo}>
                      <Text style={styles.logName}>{t.name} ({t.ticker})</Text>
                      <Text style={styles.logMeta}>{t.price?.toLocaleString()}원 · {t.qty}주 · {t.date}</Text>
                      {t.pnl != null && <Text style={[styles.logPnl, { color: returnColor(t.pnl) }]}>{formatMoney(t.pnl)} ({t.pnl_pct > 0 ? '+' : ''}{t.pnl_pct}%)</Text>}
                    </View>
                  </View>
                ))}
                {result.trade_log.length > 50 && <Text style={styles.emptyText}>... 외 {result.trade_log.length - 50}건</Text>}
              </>
            )}
          </>
        )}
      </ScrollView>

      {renderGalleryModal()}
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

  uploadBtns: { flexDirection: 'row', gap: 10 },
  imgBtn: { flex: 1, backgroundColor: '#6366F1', padding: 14, borderRadius: 10, alignItems: 'center' },
  imgBtnSec: { backgroundColor: '#fff', borderWidth: 1.5, borderColor: '#6366F1' },
  imgBtnText: { color: '#fff', fontWeight: '700', fontSize: 14 },
  subText: { textAlign: 'center', color: '#6366F1', marginTop: 8, fontSize: 13 },
  btnDisabled: { opacity: 0.5 },

  thumbList: { marginTop: 12, marginBottom: 4 },
  thumbWrap: { alignItems: 'center', marginRight: 10, position: 'relative' },
  thumbImg: { width: 76, height: 76, borderRadius: 10, borderWidth: 2, borderColor: '#6366F1' },
  thumbDel: { position: 'absolute', top: -6, right: -6, backgroundColor: '#DC2626', borderRadius: 10, width: 20, height: 20, alignItems: 'center', justifyContent: 'center' },
  thumbDelText: { color: '#fff', fontSize: 10, fontWeight: '900' },
  thumbLabel: { fontSize: 10, color: '#6366F1', fontWeight: '600', marginTop: 3 },
  thumbAdd: { width: 76, height: 76, borderRadius: 10, borderWidth: 2, borderColor: '#CBD5E1', borderStyle: 'dashed', alignItems: 'center', justifyContent: 'center', backgroundColor: '#F8FAFC' },
  thumbAddIcon: { fontSize: 22, color: '#94A3B8' },
  thumbAddText: { fontSize: 10, color: '#94A3B8', marginTop: 2 },

  inlineError: { backgroundColor: '#FEE2E2', borderRadius: 10, padding: 12, marginTop: 10 },
  inlineErrorText: { color: '#DC2626', fontWeight: '700', fontSize: 13 },
  inlineErrorSub: { color: '#991B1B', fontSize: 12, marginTop: 4 },

  condSummaryCard: { backgroundColor: '#fff', borderRadius: 10, padding: 14, marginTop: 12, borderLeftWidth: 3, borderLeftColor: '#6366F1' },
  condSummaryHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  condSummaryTitle: { fontSize: 13, fontWeight: '700', color: '#6366F1' },
  condToggleIcon: { fontSize: 12, color: '#6366F1' },
  condSummaryText: { fontSize: 13, color: '#334155' },
  condSummaryMeta: { fontSize: 12, color: '#94A3B8', marginTop: 6 },
  condDetail: { marginTop: 12, borderTopWidth: 1, borderTopColor: '#E2E8F0', paddingTop: 10 },
  condSectionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6, marginTop: 8 },
  condDetailHeader: { fontSize: 13, fontWeight: '700' },
  addBtn: { backgroundColor: '#EEF2FF', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 6 },
  addBtnText: { fontSize: 12, color: '#6366F1', fontWeight: '600' },
  condDetailRow: { backgroundColor: '#F8FAFC', borderRadius: 8, padding: 10, marginBottom: 6, flexDirection: 'row', alignItems: 'flex-start' },
  condDetailContent: { flex: 1 },
  condDetailName: { fontSize: 13, fontWeight: '700', color: '#1E293B' },
  condDetailCond: { fontSize: 12, color: '#475569', marginTop: 2 },
  condDetailMeta: { fontSize: 11, color: '#94A3B8', marginTop: 2 },
  condActions: { flexDirection: 'row', gap: 6, marginLeft: 8 },
  condEditBtn: { backgroundColor: '#EEF2FF', borderRadius: 6, padding: 6 },
  condDelBtn: { backgroundColor: '#FEE2E2', borderRadius: 6, padding: 6 },

  cashRow: { flexDirection: 'row', gap: 8, marginBottom: 8 },
  cashBtn: { flex: 1, paddingVertical: 10, borderRadius: 8, backgroundColor: '#fff', borderWidth: 1, borderColor: '#CBD5E1', alignItems: 'center' },
  cashInput: { backgroundColor: '#fff', borderRadius: 10, borderWidth: 1, borderColor: '#CBD5E1', paddingHorizontal: 14, paddingVertical: 12, fontSize: 15, color: '#1E293B', marginBottom: 4 },
  periodRow: { flexDirection: 'row', gap: 8 },
  periodBtn: { flex: 1, paddingVertical: 10, borderRadius: 8, backgroundColor: '#fff', borderWidth: 1, borderColor: '#CBD5E1', alignItems: 'center' },
  periodBtnActive: { backgroundColor: '#6366F1', borderColor: '#6366F1' },
  periodBtnText: { fontSize: 13, fontWeight: '600', color: '#475569' },
  periodBtnTextActive: { color: '#fff' },

  runBtn: { marginTop: 20, backgroundColor: '#6366F1', padding: 18, borderRadius: 14, alignItems: 'center', elevation: 3 },
  runBtnText: { color: '#fff', fontSize: 18, fontWeight: '800' },

  bigCard: { backgroundColor: '#fff', borderRadius: 14, padding: 24, alignItems: 'center', borderWidth: 2, marginBottom: 12, elevation: 2 },
  bigCardLabel: { fontSize: 13, color: '#64748B', marginBottom: 4 },
  bigCardValue: { fontSize: 42, fontWeight: '900' },
  bigCardSub: { fontSize: 16, fontWeight: '600', marginTop: 4 },
  metricGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginBottom: 4 },
  metricBox: { width: '48%', backgroundColor: '#fff', borderRadius: 10, padding: 14, alignItems: 'center', elevation: 1 },
  metricLabel: { fontSize: 12, color: '#94A3B8', marginBottom: 4 },
  metricValue: { fontSize: 16, fontWeight: '700', color: '#1E293B' },
  errorCard: { backgroundColor: '#FEE2E2', borderRadius: 10, padding: 14, marginBottom: 8 },
  errorCardText: { color: '#DC2626', fontSize: 13 },
  stockRow: { flexDirection: 'row', backgroundColor: '#fff', borderRadius: 10, padding: 12, marginBottom: 8, alignItems: 'center', elevation: 1 },
  stockInfo: { flex: 1 },
  stockName: { fontSize: 14, fontWeight: '700', color: '#1E293B' },
  stockMeta: { fontSize: 12, color: '#94A3B8', marginTop: 2 },
  stockPnl: { fontSize: 15, fontWeight: '700' },
  logRow: { flexDirection: 'row', backgroundColor: '#fff', borderRadius: 8, padding: 10, marginBottom: 6, alignItems: 'flex-start' },
  logAction: { fontSize: 12, fontWeight: '700', width: 38, marginTop: 2 },
  buyText: { color: '#16A34A' },
  sellText: { color: '#DC2626' },
  mutedText: { color: '#94A3B8' },
  logInfo: { flex: 1 },
  logName: { fontSize: 13, fontWeight: '600', color: '#1E293B' },
  logMeta: { fontSize: 11, color: '#64748B', marginTop: 2 },
  logPnl: { fontSize: 12, fontWeight: '700', marginTop: 2 },
  emptyText: { fontSize: 13, color: '#94A3B8', textAlign: 'center', marginVertical: 8 },

  // 갤러리
  galleryOverlay: { flex: 1, backgroundColor: '#000' },
  galleryHeader: { position: 'absolute', top: 50, left: 0, right: 0, zIndex: 10, flexDirection: 'row', justifyContent: 'space-between', paddingHorizontal: 20 },
  galleryPageText: { color: '#fff', fontSize: 15, fontWeight: '700' },
  galleryCloseBtn: { backgroundColor: 'rgba(255,255,255,0.2)', padding: 10, borderRadius: 8 },
  galleryCloseText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  galleryImgContainer: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  galleryDots: { position: 'absolute', bottom: 40, left: 0, right: 0, flexDirection: 'row', justifyContent: 'center', gap: 6 },
  galleryDot: { width: 7, height: 7, borderRadius: 4, backgroundColor: 'rgba(255,255,255,0.4)' },
  galleryDotActive: { backgroundColor: '#fff', width: 18 },

  // 편집 모달
  modalBox: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: Platform.OS === 'ios' ? 34 : 20,
    maxHeight: SCREEN_H * 0.80,
    // flex column: title + scroll + buttons
  },
  modalTitle: { fontSize: 16, fontWeight: '800', color: '#1E293B', marginBottom: 8 },
  modalError: { backgroundColor: '#FEE2E2', borderRadius: 8, padding: 10, marginBottom: 8 },
  modalErrorText: { color: '#DC2626', fontSize: 13, fontWeight: '600' },
  modalScroll: { flexShrink: 1 },   // 남은 공간만 차지, 버튼 밀어내지 않음
  modalLabel: { fontSize: 12, fontWeight: '600', color: '#64748B', marginBottom: 4, marginTop: 12 },
  modalLabelRequired: { color: '#DC2626' },
  modalInput: { backgroundColor: '#F8FAFC', borderWidth: 1, borderColor: '#E2E8F0', borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, fontSize: 14, color: '#1E293B' },
  modalInputMulti: { height: 72, textAlignVertical: 'top' },
  modalInputErr: { borderColor: '#DC2626' },

  // 칩
  chipScroll: { marginTop: 6, marginBottom: 2 },
  chip: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 20, backgroundColor: '#F1F5F9', borderWidth: 1, borderColor: '#CBD5E1', marginRight: 6 },
  chipActive: { backgroundColor: '#6366F1', borderColor: '#6366F1' },
  chipText: { fontSize: 12, fontWeight: '600', color: '#475569' },
  chipTextActive: { color: '#fff' },

  toggleRow: { flexDirection: 'row', gap: 8, marginTop: 4 },
  toggleBtn: { flex: 1, paddingVertical: 10, borderRadius: 8, backgroundColor: '#F1F5F9', borderWidth: 1, borderColor: '#CBD5E1', alignItems: 'center' },
  toggleBtnActive: { backgroundColor: '#6366F1', borderColor: '#6366F1' },
  toggleBtnText: { fontSize: 12, fontWeight: '600', color: '#475569' },
  toggleBtnTextActive: { color: '#fff' },

  modalBtns: { flexDirection: 'row', gap: 10, marginTop: 16 },
  modalCancel: { flex: 1, paddingVertical: 14, borderRadius: 10, backgroundColor: '#F1F5F9', alignItems: 'center' },
  modalCancelText: { fontSize: 15, fontWeight: '700', color: '#64748B' },
  modalSave: { flex: 1, paddingVertical: 14, borderRadius: 10, backgroundColor: '#6366F1', alignItems: 'center' },
  modalSaveText: { fontSize: 15, fontWeight: '700', color: '#fff' },
});
