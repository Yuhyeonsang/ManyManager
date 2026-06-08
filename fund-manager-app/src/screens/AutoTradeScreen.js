/**
 * AutoTradeScreen.js
 * 이미지로 매수/매도 조건 등록 → 자동매매 실행/정지
 */

import React, { useState, useEffect, useCallback } from 'react';
import {
  View,
  Text,
  ScrollView,
  TouchableOpacity,
  StyleSheet,
  ActivityIndicator,
  Alert,
  RefreshControl,
  Platform,
  Modal,
  TextInput,
  KeyboardAvoidingView,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import {
  analyzeTradeImage,
  analyzeTradeText,
  startAutoTrade,
  stopAutoTrade,
  getAutoTradeStatus,
} from '../services/api';

export default function AutoTradeScreen() {
  const [status, setStatus] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [toggling, setToggling] = useState(false);
  const [tradeMode, setTradeMode] = useState('paper'); // 'paper' | 'real'
  const [refreshing, setRefreshing] = useState(false);
  const [conditions, setConditions] = useState(null);
  const [verification, setVerification] = useState(null);
  const [showMissing, setShowMissing] = useState(false);
  const [textModalVisible, setTextModalVisible] = useState(false);
  const [pasteText, setPasteText] = useState('');

  // ── 상태 폴링 ──────────────────────────────
  const loadStatus = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    try {
      const s = await getAutoTradeStatus();
      setStatus(s);
      if (s.conditions) setConditions(s.conditions);
    } catch (e) {
      if (!silent) Alert.alert('오류', '서버 연결 실패: ' + (e?.message ?? e));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    loadStatus();
    const timer = setInterval(() => loadStatus(true), 10000);
    return () => clearInterval(timer);
  }, [loadStatus]);

  // ── 이미지 선택 & 분석 ──────────────────────
  const pickAndAnalyze = async () => {
    const { status: perm } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (perm !== 'granted') {
      Alert.alert('권한 필요', '사진 접근 권한이 필요합니다.');
      return;
    }

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ImagePicker.MediaTypeOptions.Images,
      allowsEditing: false,
      quality: 0.85,
      base64: false,
    });

    if (result.canceled) return;

    setAnalyzing(true);
    try {
      const asset = result.assets[0];
      const uriParts = asset.uri.split('.');
      const ext = uriParts[uriParts.length - 1].toLowerCase();
      const mimeMap = { jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp' };
      const mime = mimeMap[ext] || 'image/jpeg';

      const data = await analyzeTradeImage(asset.uri, mime);
      setConditions(data.conditions);
      setVerification(data.verification ?? null);
      setShowMissing(false);
    } catch (e) {
      Alert.alert('분석 실패', e?.message ?? String(e));
    } finally {
      setAnalyzing(false);
    }
  };

  // ── 텍스트 붙여넣기 분석 ──────────────────
  const analyzeTextInput = async () => {
    const text = pasteText.trim();
    if (!text) { Alert.alert('입력 없음', '분석할 텍스트를 붙여넣으세요.'); return; }
    setTextModalVisible(false);
    setPasteText('');
    setAnalyzing(true);
    try {
      const data = await analyzeTradeText(text);
      setConditions(data.conditions);
      setVerification(data.verification ?? null);
      setShowMissing(false);
    } catch (e) {
      Alert.alert('분석 실패', e?.message ?? String(e));
    } finally {
      setAnalyzing(false);
    }
  };

  // ── 자동매매 시작/정지 ─────────────────────
  const toggleTrading = async () => {
    if (!conditions) {
      Alert.alert('조건 없음', '먼저 이미지를 업로드해서 조건을 분석하세요.');
      return;
    }

    const isRunning = status?.running;

    if (!isRunning) {
      const modeLabel = tradeMode === 'real' ? '🔴 실전투자' : '🟡 모의투자';
      const modeDesc  = tradeMode === 'real'
        ? `실제 계좌(${status?.account_no ?? '??'})로 실제 돈이 거래됩니다!`
        : '가상 머니로 연습합니다. 실제 거래 없음.';
      Alert.alert(
        `자동매매 시작 — ${modeLabel}`,
        `${modeDesc}\n\n계속하시겠습니까?`,
        [
          { text: '취소', style: 'cancel' },
          {
            text: '시작',
            style: tradeMode === 'real' ? 'destructive' : 'default',
            onPress: async () => {
              setToggling(true);
              try {
                await startAutoTrade(conditions, tradeMode);
                await loadStatus();
              } catch (e) {
                Alert.alert('오류', e?.message ?? String(e));
              } finally {
                setToggling(false);
              }
            },
          },
        ],
      );
    } else {
      setToggling(true);
      try {
        await stopAutoTrade();
        await loadStatus();
      } catch (e) {
        Alert.alert('오류', e?.message ?? String(e));
      } finally {
        setToggling(false);
      }
    }
  };

  // ── 렌더 헬퍼 ─────────────────────────────
  const renderConditionRow = (item, type) => (
    <View key={`${type}-${item.ticker}`} style={styles.condRow}>
      <View style={[styles.condBadge, type === 'buy' ? styles.buyBadge : styles.sellBadge]}>
        <Text style={styles.condBadgeText}>{type === 'buy' ? '매수' : '매도'}</Text>
      </View>
      <View style={styles.condInfo}>
        <Text style={styles.condName}>{item.name || item.ticker}</Text>
        <Text style={styles.condDetail}>{item.condition}</Text>
        <Text style={styles.condMeta}>
          {item.qty === 'all' ? '전량' : `${item.qty}주`} · {item.price_type === 'market' ? '시장가' : '지정가'}
        </Text>
      </View>
    </View>
  );

  const renderLogRow = (entry, idx) => {
    const isError = entry.action.includes('실패');
    return (
      <View key={idx} style={styles.logRow}>
        <Text style={[styles.logAction, isError ? styles.errorText : entry.action === '매수' ? styles.buyText : styles.sellText]}>
          {entry.action}
        </Text>
        <View style={styles.logDetail}>
          <Text style={styles.logName}>{entry.name} ({entry.ticker})</Text>
          <Text style={styles.logMeta}>
            {entry.price?.toLocaleString()}원 · {entry.qty}주 · {entry.time}
          </Text>
          <Text style={styles.logResult} numberOfLines={1}>{entry.result}</Text>
        </View>
      </View>
    );
  };

  const isRunning = status?.running;

  return (
    <SafeAreaView style={styles.container} edges={['bottom']}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={loadStatus} tintColor="#6366F1" />}
      >
        {/* ── 헤더 상태 ── */}
        <View style={[styles.statusBanner, isRunning ? styles.bannerRunning : styles.bannerStopped]}>
          <View style={[styles.statusDot, isRunning ? styles.dotRunning : styles.dotStopped]} />
          <Text style={styles.statusText}>
            {isRunning ? '자동매매 실행 중' : '자동매매 정지'}
          </Text>
          <Text style={styles.modeText}>
            {(status?.trade_mode ?? tradeMode) === 'real' ? '🔴 실전' : '🟡 모의'}
          </Text>
        </View>

        {status?.error && (
          <View style={styles.errorBanner}>
            <Text style={styles.errorBannerText}>⚠️ {status.error}</Text>
          </View>
        )}

        {/* ── 실전 / 모의 토글 ── */}
        <Text style={styles.sectionTitle}>💼 거래 모드 선택</Text>
        <View style={styles.modeRow}>
          <TouchableOpacity
            style={[styles.modeBtn, tradeMode === 'paper' && styles.modeBtnPaper]}
            onPress={() => !isRunning && setTradeMode('paper')}
            disabled={isRunning}
          >
            <Text style={[styles.modeBtnText, tradeMode === 'paper' && styles.modeBtnTextActive]}>
              🟡 모의투자
            </Text>
            <Text style={styles.modeBtnSub}>가상머니 · 안전하게 연습</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.modeBtn, tradeMode === 'real' && styles.modeBtnReal]}
            onPress={() => {
              if (isRunning) return;
              Alert.alert(
                '실전투자 선택',
                '실제 계좌의 실제 돈으로 거래됩니다.\n정말 실전투자 모드로 전환하시겠습니까?',
                [
                  { text: '취소', style: 'cancel' },
                  { text: '실전으로 전환', style: 'destructive', onPress: () => setTradeMode('real') },
                ],
              );
            }}
            disabled={isRunning}
          >
            <Text style={[styles.modeBtnText, tradeMode === 'real' && styles.modeBtnTextActive]}>
              🔴 실전투자
            </Text>
            <Text style={styles.modeBtnSub}>실제 돈 · KIS 계좌 필요</Text>
          </TouchableOpacity>
        </View>
        {tradeMode === 'real' && (
          <View style={styles.realWarning}>
            <Text style={styles.realWarningText}>
              ⚠️ 실전투자 모드입니다. 실제 돈으로 거래됩니다. 신중하게 사용하세요.
            </Text>
          </View>
        )}

        {/* ── 조건 등록 버튼 ── */}
        <Text style={styles.sectionTitle}>📋 조건 등록</Text>
        <View style={styles.imageButtons}>
          <TouchableOpacity
            style={[styles.imgBtn, analyzing && styles.btnDisabled]}
            onPress={pickAndAnalyze}
            disabled={analyzing}
          >
            {analyzing ? (
              <ActivityIndicator color="#fff" />
            ) : (
              <Text style={styles.imgBtnText}>📁 갤러리에서 선택</Text>
            )}
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.imgBtn, styles.imgBtnSecondary, analyzing && styles.btnDisabled]}
            onPress={() => setTextModalVisible(true)}
            disabled={analyzing}
          >
            <Text style={[styles.imgBtnText, styles.imgBtnTextSecondary]}>📝 텍스트 붙여넣기</Text>
          </TouchableOpacity>
        </View>
        {analyzing && (
          <Text style={styles.analyzingText}>AI가 조건을 분석 중입니다...</Text>
        )}

        {/* ── 추출된 조건 표시 ── */}
        {conditions && (
          <>
            <Text style={styles.sectionTitle}>📋 매매 조건</Text>
            {conditions.summary && (
              <Text style={styles.summary}>{conditions.summary}</Text>
            )}

            {/* ── 일치율 카드 ── */}
            {verification && verification.match_pct >= 0 && (() => {
              const pct = verification.match_pct ?? 0;
              const color = pct >= 80 ? '#16A34A' : pct >= 60 ? '#D97706' : '#DC2626';
              const bg    = pct >= 80 ? '#DCFCE7' : pct >= 60 ? '#FEF3C7' : '#FEE2E2';
              const label = pct >= 80 ? '높음' : pct >= 60 ? '보통' : '낮음';
              const missing  = verification.missing  ?? [];
              const wrong    = verification.wrong    ?? [];
              return (
                <View style={[styles.verifyCard, { borderColor: color }]}>
                  <View style={styles.verifyTop}>
                    <View style={[styles.verifyCircle, { backgroundColor: bg }]}>
                      <Text style={[styles.verifyPct, { color }]}>{pct}%</Text>
                    </View>
                    <View style={styles.verifyInfo}>
                      <Text style={styles.verifyTitle}>
                        AI 이중 검증 완료 — 일치율 <Text style={{ color }}>{label}</Text>
                      </Text>
                      <Text style={styles.verifyNotes}>{verification.notes}</Text>
                      <Text style={styles.verifyMeta}>
                        원본 {verification.total_in_source ?? '?'}건 중 {verification.total_extracted ?? '?'}건 추출
                      </Text>
                    </View>
                  </View>

                  {(missing.length > 0 || wrong.length > 0) && (
                    <TouchableOpacity
                      style={styles.verifyToggle}
                      onPress={() => setShowMissing(v => !v)}
                    >
                      <Text style={[styles.verifyToggleText, { color }]}>
                        {showMissing ? '▲ 닫기' : `▼ 누락/오류 ${missing.length + wrong.length}건 보기`}
                      </Text>
                    </TouchableOpacity>
                  )}

                  {showMissing && (
                    <View style={styles.verifyDetail}>
                      {missing.map((m, i) => (
                        <View key={`m${i}`} style={styles.verifyItem}>
                          <Text style={styles.verifyItemBadge}>누락</Text>
                          <Text style={styles.verifyItemText}>{m}</Text>
                        </View>
                      ))}
                      {wrong.map((w, i) => (
                        <View key={`w${i}`} style={styles.verifyItem}>
                          <Text style={[styles.verifyItemBadge, styles.verifyWrongBadge]}>오류</Text>
                          <Text style={styles.verifyItemText}>{w}</Text>
                        </View>
                      ))}
                    </View>
                  )}
                </View>
              );
            })()}

            <Text style={styles.condHeader}>
              체크 주기: {conditions.check_interval_minutes ?? 5}분마다
            </Text>
            {conditions.buy_conditions?.map((c) => renderConditionRow(c, 'buy'))}
            {conditions.sell_conditions?.map((c) => renderConditionRow(c, 'sell'))}
          </>
        )}

        {/* ── 시작/정지 버튼 ── */}
        <TouchableOpacity
          style={[
            styles.mainBtn,
            isRunning ? styles.stopBtn : styles.startBtn,
            (toggling || analyzing) && styles.btnDisabled,
          ]}
          onPress={toggleTrading}
          disabled={toggling || analyzing}
        >
          {toggling ? (
            <ActivityIndicator color="#fff" size="large" />
          ) : (
            <Text style={styles.mainBtnText}>
              {isRunning ? '⏹  자동매매 정지' : '▶  자동매매 시작'}
            </Text>
          )}
        </TouchableOpacity>

        {/* ── 거래 로그 ── */}
        {status?.trade_log?.length > 0 && (
          <>
            <Text style={styles.sectionTitle}>📜 최근 거래 내역</Text>
            {status.trade_log.map((e, i) => renderLogRow(e, i))}
          </>
        )}

        {status?.last_check && (
          <Text style={styles.lastCheck}>마지막 체크: {status.last_check}</Text>
        )}
      </ScrollView>

      {/* 텍스트 붙여넣기 모달 */}
      <Modal visible={textModalVisible} transparent animationType="slide" onRequestClose={() => setTextModalVisible(false)}>
        <KeyboardAvoidingView style={{ flex: 1, justifyContent: 'flex-end' }} behavior="padding" enabled={Platform.OS === 'ios'}>
          <TouchableOpacity style={{ ...StyleSheet.absoluteFillObject }} activeOpacity={1} onPress={() => setTextModalVisible(false)} />
          <View style={styles.textModal}>
            <Text style={styles.textModalTitle}>📝 전략 텍스트 붙여넣기</Text>
            <Text style={styles.textModalDesc}>
              매수/매도 조건이 적힌 텍스트를 붙여넣으면 AI가 자동으로 조건을 추출합니다.
            </Text>
            <TextInput
              style={styles.textModalInput}
              value={pasteText}
              onChangeText={setPasteText}
              placeholder={'예)\nTQQQ: QQQ 고점 대비 -10% 시 30% 매수\nTP1: +15% 시 최초수량 50% 매도\n락: QQQ -40% 이하 시 전량 청산'}
              placeholderTextColor="#94A3B8"
              multiline
              autoFocus
            />
            <View style={styles.textModalBtns}>
              <TouchableOpacity style={styles.textModalCancel} onPress={() => { setTextModalVisible(false); setPasteText(''); }}>
                <Text style={styles.textModalCancelText}>취소</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.textModalConfirm} onPress={analyzeTextInput}>
                <Text style={styles.textModalConfirmText}>✨ AI 분석</Text>
              </TouchableOpacity>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F1F5F9' },
  scroll: { padding: 16, paddingBottom: 40 },

  // 상태 배너
  statusBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 14,
    borderRadius: 12,
    marginBottom: 16,
  },
  bannerRunning: { backgroundColor: '#DCFCE7' },
  bannerStopped: { backgroundColor: '#F1F5F9', borderWidth: 1, borderColor: '#CBD5E1' },
  statusDot: { width: 10, height: 10, borderRadius: 5, marginRight: 8 },
  dotRunning: { backgroundColor: '#16A34A' },
  dotStopped: { backgroundColor: '#94A3B8' },
  statusText: { fontSize: 16, fontWeight: '700', color: '#1E293B', flex: 1 },
  modeText: { fontSize: 13, color: '#475569' },

  errorBanner: {
    backgroundColor: '#FEE2E2',
    padding: 10,
    borderRadius: 8,
    marginBottom: 12,
  },
  errorBannerText: { color: '#DC2626', fontSize: 13 },

  // 섹션 제목
  sectionTitle: {
    fontSize: 15,
    fontWeight: '700',
    color: '#1E293B',
    marginTop: 20,
    marginBottom: 10,
  },

  // 이미지 버튼
  imageButtons: { flexDirection: 'row', gap: 10 },
  imgBtn: {
    flex: 1,
    backgroundColor: '#6366F1',
    padding: 14,
    borderRadius: 10,
    alignItems: 'center',
  },
  imgBtnSecondary: { backgroundColor: '#fff', borderWidth: 1.5, borderColor: '#6366F1' },
  imgBtnText: { color: '#fff', fontWeight: '700', fontSize: 14 },
  imgBtnTextSecondary: { color: '#6366F1' },
  analyzingText: { textAlign: 'center', color: '#6366F1', marginTop: 8, fontSize: 13 },

  // 조건 카드
  summary: {
    fontSize: 13,
    color: '#475569',
    backgroundColor: '#fff',
    padding: 10,
    borderRadius: 8,
    marginBottom: 8,
  },
  condHeader: { fontSize: 12, color: '#94A3B8', marginBottom: 6 },
  condRow: {
    flexDirection: 'row',
    backgroundColor: '#fff',
    borderRadius: 10,
    padding: 12,
    marginBottom: 8,
    alignItems: 'flex-start',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.05,
    shadowRadius: 2,
    elevation: 1,
  },
  condBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 6,
    marginRight: 10,
    marginTop: 2,
  },
  buyBadge: { backgroundColor: '#DCFCE7' },
  sellBadge: { backgroundColor: '#FEE2E2' },
  condBadgeText: { fontSize: 11, fontWeight: '700', color: '#1E293B' },
  condInfo: { flex: 1 },
  condName: { fontSize: 14, fontWeight: '700', color: '#1E293B' },
  condDetail: { fontSize: 13, color: '#475569', marginTop: 2 },
  condMeta: { fontSize: 11, color: '#94A3B8', marginTop: 4 },

  // 시작/정지 버튼
  mainBtn: {
    marginTop: 24,
    padding: 18,
    borderRadius: 14,
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 4,
    elevation: 3,
  },
  startBtn: { backgroundColor: '#16A34A' },
  stopBtn: { backgroundColor: '#DC2626' },
  btnDisabled: { opacity: 0.5 },
  mainBtnText: { color: '#fff', fontSize: 18, fontWeight: '800' },

  // 거래 로그
  logRow: {
    flexDirection: 'row',
    backgroundColor: '#fff',
    borderRadius: 8,
    padding: 10,
    marginBottom: 6,
    alignItems: 'flex-start',
  },
  logAction: { fontSize: 12, fontWeight: '700', width: 44, marginTop: 2 },
  buyText: { color: '#16A34A' },
  sellText: { color: '#DC2626' },
  errorText: { color: '#F59E0B' },
  logDetail: { flex: 1 },
  logName: { fontSize: 13, fontWeight: '600', color: '#1E293B' },
  logMeta: { fontSize: 11, color: '#64748B', marginTop: 2 },
  logResult: { fontSize: 11, color: '#94A3B8', marginTop: 2 },
  lastCheck: { textAlign: 'center', fontSize: 11, color: '#94A3B8', marginTop: 16 },

  // 텍스트 붙여넣기 모달
  textModal: {
    backgroundColor: '#fff',
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingHorizontal: 20,
    paddingTop: 20,
    paddingBottom: Platform.OS === 'ios' ? 34 : 20,
    height: 400,
    flexDirection: 'column',
  },
  textModalTitle: { fontSize: 16, fontWeight: '800', color: '#1E293B', marginBottom: 6 },
  textModalDesc: { fontSize: 12, color: '#64748B', marginBottom: 10 },
  textModalInput: {
    flex: 1,
    backgroundColor: '#F8FAFC',
    borderWidth: 1,
    borderColor: '#E2E8F0',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
    color: '#1E293B',
    textAlignVertical: 'top',
    marginBottom: 12,
  },
  textModalBtns: { flexDirection: 'row', gap: 10 },
  textModalCancel: { flex: 1, paddingVertical: 14, borderRadius: 10, backgroundColor: '#F1F5F9', alignItems: 'center' },
  textModalCancelText: { fontSize: 15, fontWeight: '700', color: '#64748B' },
  textModalConfirm: { flex: 1, paddingVertical: 14, borderRadius: 10, backgroundColor: '#6366F1', alignItems: 'center' },
  textModalConfirmText: { fontSize: 15, fontWeight: '700', color: '#fff' },

  // 일치율 검증 카드
  verifyCard: {
    backgroundColor: '#fff',
    borderRadius: 12,
    borderWidth: 1.5,
    padding: 14,
    marginBottom: 12,
  },
  verifyTop: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  verifyCircle: {
    width: 64, height: 64, borderRadius: 32,
    alignItems: 'center', justifyContent: 'center',
  },
  verifyPct: { fontSize: 20, fontWeight: '800' },
  verifyInfo: { flex: 1 },
  verifyTitle: { fontSize: 13, fontWeight: '700', color: '#1E293B', marginBottom: 3 },
  verifyNotes: { fontSize: 12, color: '#475569', marginBottom: 2 },
  verifyMeta: { fontSize: 11, color: '#94A3B8' },
  verifyToggle: { marginTop: 10, alignItems: 'center', paddingVertical: 6 },
  verifyToggleText: { fontSize: 13, fontWeight: '600' },
  verifyDetail: { marginTop: 8, gap: 6 },
  verifyItem: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  verifyItemBadge: {
    fontSize: 10, fontWeight: '700', color: '#D97706',
    backgroundColor: '#FEF3C7', paddingHorizontal: 6, paddingVertical: 2,
    borderRadius: 4, marginTop: 1,
  },
  verifyWrongBadge: { color: '#DC2626', backgroundColor: '#FEE2E2' },
  verifyItemText: { fontSize: 12, color: '#475569', flex: 1 },

  // 거래 모드 토글
  modeRow: { flexDirection: 'row', gap: 10 },
  modeBtn: {
    flex: 1, padding: 14, borderRadius: 12, borderWidth: 2,
    borderColor: '#CBD5E1', backgroundColor: '#fff', alignItems: 'center',
  },
  modeBtnPaper: { borderColor: '#F59E0B', backgroundColor: '#FFFBEB' },
  modeBtnReal:  { borderColor: '#DC2626', backgroundColor: '#FEF2F2' },
  modeBtnText:  { fontSize: 15, fontWeight: '700', color: '#475569' },
  modeBtnTextActive: { color: '#1E293B' },
  modeBtnSub:   { fontSize: 11, color: '#94A3B8', marginTop: 3 },
  realWarning: {
    marginTop: 8, backgroundColor: '#FEE2E2', borderRadius: 8, padding: 10,
    borderLeftWidth: 3, borderLeftColor: '#DC2626',
  },
  realWarningText: { fontSize: 12, color: '#B91C1C', fontWeight: '600' },
});
