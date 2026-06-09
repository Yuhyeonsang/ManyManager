/**
 * AutoTradeScreen.js
 * BacktestScreen과 동일한 조건 UI + 자동매매 실행/정지 + AI 이중 검증
 */

import React, { useState, useEffect, useCallback, useRef } from 'react';
import {
  View, Text, ScrollView, FlatList, TouchableOpacity, StyleSheet,
  ActivityIndicator, Alert, RefreshControl, Platform, Modal,
  TextInput, KeyboardAvoidingView, Image, Dimensions, Linking, Keyboard,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import * as ImagePicker from 'expo-image-picker';
import * as Clipboard from 'expo-clipboard';
import {
  analyzeTradeImage, analyzeTradeText,
  startAutoTrade, stopAutoTrade, getAutoTradeStatus,
  resetAutoTradeConditions, fixAutoTradeConditions,
  startPhaseTrading, listTemplates, getTemplate, saveTemplate, deleteTemplate,
} from '../services/api';

const { width: SCREEN_W, height: SCREEN_H } = Dimensions.get('window');
const DEFAULT_TICKERS = ['TQQQ', 'QQQ', 'SPY', 'SQQQ', 'TLT', 'GLD', 'UPRO', 'TMF'];

const CONDITION_TEMPLATES = {
  buy: [
    '고점 대비 -10% 하락', '고점 대비 -20% 하락', '고점 대비 -30% 하락',
    'RSI 35 이하', 'RSI 25 이하', '골든크로스 발생',
    '200일선 이하', '200일선 대비 -7% 이하', '볼린저 하단 이탈', '항상',
  ],
  sell: [
    'TP1 +15% 도달', 'TP2 +100% 도달', 'TP3 +350% 도달',
    '+30% 도달', '+50% 도달', '+200% 도달',
    '데드크로스 발생', '손절 -10%', 'RSI 70 이상', '볼린저 상단 돌파',
  ],
  lock: [
    '고점 대비 -40% 이하', '극단적 하락 (-40% 이하)', 'RSI 20 이하',
  ],
};

const emptyBuy  = () => ({ label: '', name: '', ticker: '', ref_ticker: '', condition: '', weight_pct: '', weight_mode: 'add' });
const emptySell = () => ({ label: '', name: '', ticker: '', condition: '', sell_pct: '', sell_mode: 'initial_qty' });
const emptyLock = () => ({ condition: '', action: 'liquidate' });

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

export default function AutoTradeScreen() {
  // ── 자동매매 상태 ─────────────────────────
  const [status,    setStatus]    = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const [toggling,  setToggling]  = useState(false);
  const [tradeMode, setTradeMode] = useState('paper');

  // ── 조건 ─────────────────────────────────
  const [conditions,    setConditions]    = useState(null);
  const [verification,  setVerification]  = useState(null);
  const [showMissing,   setShowMissing]   = useState(false);
  const [condExpanded,  setCondExpanded]  = useState(false);

  // ── 이미지 ───────────────────────────────
  const [imageUris,     setImageUris]     = useState([]);
  const [analyzing,     setAnalyzing]     = useState(false);
  const [analyzeError,  setAnalyzeError]  = useState(null);
  const [galleryVisible, setGalleryVisible] = useState(false);
  const [galleryIndex,  setGalleryIndex]  = useState(0);
  const galleryRef = useRef(null);

  // ── 텍스트 모달 ──────────────────────────
  const [textModalVisible, setTextModalVisible] = useState(false);
  const [pasteText,  setPasteText]  = useState('');
  const [kbHeight,   setKbHeight]   = useState(0);

  // ── 템플릿 ───────────────────────────────
  const [templateModal, setTemplateModal] = useState(false);
  const [templateList,  setTemplateList]  = useState([]);
  const [activeStrategy, setActiveStrategy] = useState(null); // 현재 로드된 Phase 전략
  const [saveNameModal, setSaveNameModal] = useState(false);
  const [saveNameText,  setSaveNameText]  = useState('');


  // ── 조건 편집 모달 ────────────────────────
  const [editModal, setEditModal] = useState({ visible: false, type: null, index: null, data: null });
  const [editError, setEditError] = useState(null);

  // ── 자동완성 ────────────────────────────
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

  // ── 상태 폴링 ────────────────────────────
  const loadStatus = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    try {
      const s = await getAutoTradeStatus();
      setStatus(s);
      if (s.conditions && !conditions) setConditions(s.conditions);
      if (s.strategy && !activeStrategy) setActiveStrategy(s.strategy);
    } catch (e) {
      if (!silent) Alert.alert('오류', '서버 연결 실패: ' + (e?.message ?? e));
    } finally {
      setRefreshing(false);
    }
  }, [conditions]);

  useEffect(() => {
    loadStatus();
    const timer = setInterval(() => loadStatus(true), 10000);
    return () => clearInterval(timer);
  }, [loadStatus]);

  useEffect(() => {
    const show = Keyboard.addListener('keyboardDidShow', e => setKbHeight(e.endCoordinates.height));
    const hide = Keyboard.addListener('keyboardDidHide', () => setKbHeight(0));
    return () => { show.remove(); hide.remove(); };
  }, []);

  // ── 이미지 선택 & 분석 ──────────────────
  const pickImage = async () => {
    const { status: perm } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (perm !== 'granted') { Alert.alert('권한 필요', '사진 접근 권한이 필요합니다.'); return; }

    const res = await ImagePicker.launchImageLibraryAsync({
      allowsEditing: false, quality: 0.85, allowsMultipleSelection: true,
    });
    if (res.canceled) return;

    const assets = res.assets;
    setImageUris(prev => [...prev, ...assets.map(a => a.uri)]);
    setAnalyzing(true);
    setAnalyzeError(null);

    try {
      const asset = assets[0];
      const ext  = asset.uri.split('.').pop().toLowerCase();
      const mime = ext === 'png' ? 'image/png' : 'image/jpeg';
      const data = await analyzeTradeImage(asset.uri, mime);
      setConditions(prev => {
        if (!prev) return data.conditions;
        return {
          ...data.conditions,
          buy_conditions:  [...(prev.buy_conditions  || []), ...(data.conditions.buy_conditions  || [])],
          sell_conditions: [...(prev.sell_conditions || []), ...(data.conditions.sell_conditions || [])],
          lock_conditions: [...(prev.lock_conditions || []), ...(data.conditions.lock_conditions || [])],
        };
      });
      setVerification(data.verification ?? null);
      setShowMissing(false);
      setCondExpanded(true);
    } catch (e) {
      setAnalyzeError(e?.message ?? String(e));
      if (!conditions) setConditions({ summary: '수동 입력', buy_conditions: [], sell_conditions: [], lock_conditions: [] });
    } finally {
      setAnalyzing(false);
    }
  };

  // ── 텍스트 분석 ──────────────────────────
  const analyzeText = async () => {
    const text = pasteText.trim();
    if (!text) { Alert.alert('입력 없음', '텍스트를 붙여넣으세요.'); return; }
    setTextModalVisible(false);
    setPasteText('');
    setAnalyzing(true);
    setAnalyzeError(null);
    try {
      const data = await analyzeTradeText(text);
      setConditions(prev => {
        if (!prev) return data.conditions;
        return {
          ...data.conditions,
          buy_conditions:  [...(prev.buy_conditions  || []), ...(data.conditions.buy_conditions  || [])],
          sell_conditions: [...(prev.sell_conditions || []), ...(data.conditions.sell_conditions || [])],
          lock_conditions: [...(prev.lock_conditions || []), ...(data.conditions.lock_conditions || [])],
        };
      });
      setVerification(data.verification ?? null);
      setShowMissing(false);
      setCondExpanded(true);
    } catch (e) {
      setAnalyzeError(e?.message ?? String(e));
      if (!conditions) setConditions({ summary: '수동 입력', buy_conditions: [], sell_conditions: [], lock_conditions: [] });
    } finally {
      setAnalyzing(false);
    }
  };

  // ── 조건 → 읽기 편한 텍스트 변환 ──────────
  const formatConditionsAsText = () => {
    if (!conditions) return '';
    const lines = [`[전략] ${conditions.summary || '자동매매 전략'}\n`];

    if (conditions.buy_conditions?.length) {
      lines.push('=== 매수 조건 ===');
      conditions.buy_conditions.forEach((c, i) => {
        const name = c.name || c.label || `매수${i + 1}`;
        const ticker = c.ticker ? `[${c.ticker}]` : '';
        const ref = c.ref_ticker ? ` (기준: ${c.ref_ticker})` : '';
        const weight = c.weight_pct ? ` | 목표비중 ${c.weight_pct}% (${c.weight_mode === 'target' ? '타겟' : '추가'})` : '';
        lines.push(`• ${name} ${ticker}${ref}: ${c.condition}${weight}`);
      });
      lines.push('');
    }

    if (conditions.sell_conditions?.length) {
      lines.push('=== 매도 조건 ===');
      conditions.sell_conditions.forEach((c, i) => {
        const name = c.name || c.label || `매도${i + 1}`;
        const ticker = c.ticker ? `[${c.ticker}]` : '';
        const sell = c.sell_pct ? ` | ${c.sell_pct}% 매도 (${c.sell_mode === 'initial_qty' ? '최초수량 기준' : '현재보유 기준'})` : '';
        lines.push(`• ${name} ${ticker}: ${c.condition}${sell}`);
      });
      lines.push('');
    }

    if (conditions.lock_conditions?.length) {
      lines.push('=== 락 조건 ===');
      conditions.lock_conditions.forEach((c, i) => {
        lines.push(`• 조건: ${c.condition} → ${c.action === 'liquidate' ? '전량 청산' : '신규 매수 잠금'}`);
      });
      lines.push('');
    }

    return lines.join('\n');
  };

  // ── Phase 템플릿 → 텍스트 포맷 ──
  const formatStrategyAsText = (strat) => {
    if (!strat) return '';
    const lines = [
      `[Phase 전략] ${strat.name}`,
      `설명: ${strat.description || ''}`,
      `종목: ${strat.ticker || ''}  참조: ${strat.ref_ticker || ''}`,
      '',
    ];

    // Phases
    if (strat.phases) {
      lines.push('=== Phase 단계 ===');
      Object.entries(strat.phases).forEach(([k, v]) => lines.push(`  Phase ${k}: ${v}`));
      lines.push('');
    }

    // 매수/매도 조건
    const buys = (strat.conditions || []).filter(c => c.type === 'buy');
    const sells = (strat.conditions || []).filter(c => c.type === 'sell');

    if (buys.length) {
      lines.push('=== 매수 조건 ===');
      buys.forEach(c => {
        const phases = Array.isArray(c.required_phase) ? `Phase [${c.required_phase.join(',')}]` : `Phase ${c.required_phase}`;
        const next = c.next_phase != null ? ` → Phase ${c.next_phase}` : '';
        const one = c.one_time ? ' [1회]' : '';
        const reset = c.reset_on_trigger ? ' [리셋]' : '';
        const action = c.action?.weight_mode === 'target'
          ? `목표비중 ${c.action.weight_pct}% (타겟)`
          : `+${c.action?.weight_pct}% 추가`;
        lines.push(`• ${c.name} [${c.ticker}] (기준: ${c.ref_ticker})`);
        lines.push(`  조건: ${c.condition}`);
        lines.push(`  실행: ${action} | ${phases}${next}${one}${reset}`);
      });
      lines.push('');
    }

    if (sells.length) {
      lines.push('=== 매도 조건 ===');
      sells.forEach(c => {
        const phases = Array.isArray(c.required_phase) ? `Phase [${c.required_phase.join(',')}]` : `Phase ${c.required_phase}`;
        const next = c.next_phase != null ? ` → Phase ${c.next_phase}` : '';
        const one = c.one_time ? ' [1회]' : '';
        const reset = c.reset_on_trigger ? ' [리셋]' : '';
        const mode = c.action?.sell_mode === 'initial_qty' ? '최초수량' : '현재보유';
        lines.push(`• ${c.name} [${c.ticker}]`);
        lines.push(`  조건: ${c.condition}`);
        lines.push(`  실행: ${c.action?.sell_pct}% 매도 (${mode}) | ${phases}${next}${one}${reset}`);
      });
      lines.push('');
    }

    if (strat.lock_conditions?.length) {
      lines.push('=== 락 조건 ===');
      strat.lock_conditions.forEach(c => {
        lines.push(`• ${c.id}: ${c.condition} → 해제: ${c.release_condition || '없음'}`);
      });
      lines.push('');
    }

    return lines.join('\n');
  };

  // ── 일치 확인: 조건 텍스트 클립보드 복사 후 AI 앱 열기 ──
  const openMatchCheck = () => {
    const condText = activeStrategy
      ? formatStrategyAsText(activeStrategy)
      : formatConditionsAsText();
    if (!condText.trim()) { Alert.alert('조건 없음', '먼저 전략을 불러오거나 조건을 추출하세요.'); return; }
    const stratLabel = activeStrategy
      ? `Phase 전략 "${activeStrategy.name}"의 조건이 원본과 100% 일치하는지 검증해주세요.\n조건 순서, 수치, Phase 번호, 1회 여부, 리셋 여부 등 모든 항목 확인.`
      : '아래 조건이 원본 전략과 일치하는지 확인해주세요.\n1. 잘못된 조건 (수치 오류, 방향 반전)\n2. 빠진 조건\n3. 전체 일치율 (%)';
    const prompt = `${stratLabel}\n\n${condText}`;

    Alert.alert(
      '📋 일치 확인',
      '조건이 클립보드에 복사됩니다.\nAI 앱을 선택하면 앱이 열립니다.\n원본 전략과 함께 붙여넣어 비교하세요.',
      [
        { text: '취소', style: 'cancel' },
        {
          text: '✦ Gemini',
          onPress: async () => {
            await Clipboard.setStringAsync(prompt);
            Linking.openURL('https://gemini.google.com/app');
          },
        },
        {
          text: '◆ Claude',
          onPress: async () => {
            await Clipboard.setStringAsync(prompt);
            Linking.openURL('https://claude.ai/new');
          },
        },
        {
          text: '⬡ ChatGPT',
          onPress: async () => {
            await Clipboard.setStringAsync(prompt);
            Linking.openURL('https://chatgpt.com');
          },
        },
      ]
    );
  };

  // ── 수정 적용 (기존 조건 + 수정 텍스트 → AI 픽스) ──
  const applyFix = async () => {
    const text = pasteText.trim();
    if (!text) { Alert.alert('입력 없음', '수정 내용을 붙여넣으세요.'); return; }
    if (!conditions) { Alert.alert('조건 없음', '먼저 AI 분석으로 조건을 추출하세요.'); return; }
    setTextModalVisible(false);
    setPasteText('');
    setAnalyzing(true);
    setAnalyzeError(null);
    try {
      const data = await fixAutoTradeConditions(conditions, text);
      setConditions(data.conditions);
      setVerification(null);
      setCondExpanded(true);
    } catch (e) {
      setAnalyzeError(e?.message ?? String(e));
    } finally {
      setAnalyzing(false);
    }
  };

  const removeImage = (i) =>
    Alert.alert('삭제', '이 이미지를 제거할까요?', [
      { text: '취소', style: 'cancel' },
      { text: '삭제', style: 'destructive', onPress: () => setImageUris(prev => prev.filter((_, idx) => idx !== i)) },
    ]);

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

  const deleteCondition = (type, index) =>
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

  const setField = (field, value) =>
    setEditModal(prev => ({ ...prev, data: { ...prev.data, [field]: value } }));

  // ── 자동매매 시작/정지 ────────────────────
  const toggleTrading = async () => {
    const hasStrategy = !!activeStrategy;
    const hasConditions = !!conditions;
    if (!hasStrategy && !hasConditions) {
      Alert.alert('조건 없음', '먼저 템플릿을 불러오거나 이미지/텍스트로 조건을 설정하세요.');
      return;
    }
    const isRunning = status?.running;
    if (!isRunning) {
      const modeLabel = tradeMode === 'real' ? '🔴 실전투자' : '🟡 모의투자';
      const modeDesc  = tradeMode === 'real'
        ? `실제 계좌(${status?.account_no ?? '??'})로 실제 돈이 거래됩니다!`
        : '가상 머니로 연습합니다. 실제 거래 없음.';
      const stratName = hasStrategy ? `\n전략: ${activeStrategy.name}` : '';
      Alert.alert(`자동매매 시작 — ${modeLabel}`, `${modeDesc}${stratName}\n\n계속하시겠습니까?`, [
        { text: '취소', style: 'cancel' },
        { text: '시작', style: tradeMode === 'real' ? 'destructive' : 'default',
          onPress: async () => {
            setToggling(true);
            try {
              if (hasStrategy) {
                await startPhaseTrading(activeStrategy, tradeMode, false);
              } else {
                await startAutoTrade(conditions, tradeMode);
              }
              await loadStatus();
            }
            catch (e) { Alert.alert('오류', e?.message ?? String(e)); }
            finally { setToggling(false); }
          }},
      ]);
    } else {
      setToggling(true);
      try { await stopAutoTrade(); await loadStatus(); }
      catch (e) { Alert.alert('오류', e?.message ?? String(e)); }
      finally { setToggling(false); }
    }
  };

  // ── 템플릿 관련 ──────────────────────────
  const openTemplateModal = async () => {
    try {
      const list = await listTemplates();
      setTemplateList(list);
      setTemplateModal(true);
    } catch (e) { Alert.alert('오류', '템플릿 목록 조회 실패: ' + (e?.message ?? e)); }
  };

  const loadTemplateByName = async (name) => {
    try {
      const strategy = await getTemplate(name);
      setActiveStrategy(strategy);
      setConditions(null);
      setVerification(null);
      setTemplateModal(false);
      Alert.alert('✅ 템플릿 로드', `"${name}" 전략이 설정됐습니다.\n자동매매 시작 버튼을 누르세요.`);
    } catch (e) { Alert.alert('오류', e?.message ?? String(e)); }
  };

  const saveCurrentAsTemplate = async () => {
    const name = saveNameText.trim();
    if (!name) { Alert.alert('이름 필요', '저장할 전략 이름을 입력하세요.'); return; }
    const strategy = activeStrategy || conditions;
    if (!strategy) { Alert.alert('전략 없음', '저장할 전략이 없습니다.'); return; }
    try {
      await saveTemplate(name, strategy);
      setSaveNameModal(false);
      setSaveNameText('');
      Alert.alert('✅ 저장 완료', `"${name}"으로 저장됐습니다.`);
    } catch (e) { Alert.alert('오류', e?.message ?? String(e)); }
  };

  const deleteTemplateByName = async (name) => {
    Alert.alert('템플릿 삭제', `"${name}"을 삭제할까요?`, [
      { text: '취소', style: 'cancel' },
      { text: '삭제', style: 'destructive', onPress: async () => {
        try {
          await deleteTemplate(name);
          const list = await listTemplates();
          setTemplateList(list);
        } catch (e) { Alert.alert('오류', e?.message ?? String(e)); }
      }},
    ]);
  };

  // ── 자동완성 드롭다운 ────────────────────
  const AutoSuggest = ({ query, items, onSelect, showAll = false }) => {
    const q = (query || '').trim().toLowerCase();
    if (!showAll && q.length === 0) return null;
    const filtered = q.length === 0 ? items : items.filter(t => t.toLowerCase().includes(q));
    if (filtered.length === 0) return null;
    return (
      <View style={styles.suggestBox}>
        {filtered.map((item, i) => (
          <TouchableOpacity key={i} style={[styles.suggestItem, i < filtered.length - 1 && styles.suggestItemBorder]} onPress={() => onSelect(item)}>
            <Text style={styles.suggestText}>{item}</Text>
          </TouchableOpacity>
        ))}
      </View>
    );
  };

  // ── 조건 상세 표시 ───────────────────────
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
                  {type === 'buy'  && c.weight_pct != null ? `목표비중 ${c.weight_pct}% (${c.weight_mode === 'add' ? '추가' : '타겟'})` : ''}
                  {type === 'sell' && c.sell_pct   != null ? `${c.sell_pct}% 매도 (${c.sell_mode === 'initial_qty' ? '최초수량' : '현재보유'} 기준)` : ''}
                  {type === 'lock' ? (c.action === 'liquidate' ? '전량 청산+매수 잠금' : '신규 매수 잠금') : ''}
                  {(type === 'buy' || type === 'sell') && c.ticker ? ` · ${c.ticker}` : ''}
                </Text>
              </View>
              <View style={styles.condActions}>
                <TouchableOpacity style={styles.condEditBtn} onPress={() => openEdit(type, i)}><Text>✏️</Text></TouchableOpacity>
                <TouchableOpacity style={styles.condDelBtn} onPress={() => deleteCondition(type, i)}><Text>🗑️</Text></TouchableOpacity>
              </View>
            </View>
          ))}
        </View>
      ))}
    </View>
  );

  // ── 편집 모달 ────────────────────────────
  const renderEditModal = () => {
    if (!editModal.visible || !editModal.data) return null;
    const { type, data } = editModal;
    const isNew = editModal.index === null;
    return (
      <Modal visible transparent animationType="slide" onRequestClose={() => setEditModal(v => ({ ...v, visible: false }))}>
        <KeyboardAvoidingView style={{ flex: 1, justifyContent: 'flex-end' }} behavior="padding" enabled={Platform.OS === 'ios'}>
          <TouchableOpacity style={StyleSheet.absoluteFill} activeOpacity={1} onPress={() => setEditModal(v => ({ ...v, visible: false }))} />
          <View style={styles.modalBox}>
            <Text style={styles.modalTitle}>
              {isNew ? '➕ 조건 추가' : '✏️ 조건 수정'}
              {type === 'buy' ? ' (매수)' : type === 'sell' ? ' (매도)' : ' (락)'}
            </Text>
            {editError && <View style={styles.modalError}><Text style={styles.modalErrorText}>⚠️ {editError}</Text></View>}
            <ScrollView style={styles.modalScroll} contentContainerStyle={{ paddingBottom: 8 }} keyboardShouldPersistTaps="handled" showsVerticalScrollIndicator={false}>

              {(type === 'buy' || type === 'sell') && (
                <>
                  <Text style={styles.modalLabel}>라벨</Text>
                  <TextInput style={styles.modalInput} value={data.label ?? ''} onChangeText={v => setField('label', v)} placeholder="예: Dip 1 (선택)" placeholderTextColor="#94A3B8" />
                  <AutoSuggest query={data.label} items={[...knownLabels]} onSelect={v => setField('label', v)} />

                  <Text style={styles.modalLabel}>티커 <Text style={{ color: '#DC2626' }}>*</Text></Text>
                  <TextInput style={styles.modalInput} value={data.ticker ?? ''} onChangeText={v => { setField('ticker', v); if (!data.name) setField('name', v); }} placeholder="예: TQQQ" placeholderTextColor="#94A3B8" autoCapitalize="characters" />
                  <AutoSuggest query={data.ticker} items={knownTickers} onSelect={v => { setField('ticker', v); setField('name', v); }} showAll />

                  <Text style={styles.modalLabel}>종목명</Text>
                  <TextInput style={styles.modalInput} value={data.name ?? ''} onChangeText={v => setField('name', v)} placeholder="티커와 같으면 생략 가능" placeholderTextColor="#94A3B8" />
                </>
              )}

              {type === 'buy' && (
                <>
                  <Text style={styles.modalLabel}>기준 티커 (선택)</Text>
                  <TextInput style={styles.modalInput} value={data.ref_ticker ?? ''} onChangeText={v => setField('ref_ticker', v)} placeholder="예: QQQ" placeholderTextColor="#94A3B8" autoCapitalize="characters" />
                  <AutoSuggest query={data.ref_ticker} items={knownTickers} onSelect={v => setField('ref_ticker', v)} showAll />
                </>
              )}

              <Text style={styles.modalLabel}>조건 내용 <Text style={{ color: '#DC2626' }}>*</Text></Text>
              <TextInput style={[styles.modalInput, styles.modalInputMulti]} value={data.condition ?? ''} onChangeText={v => setField('condition', v)} placeholder="예: 고점 대비 -10% 이하" placeholderTextColor="#94A3B8" multiline />
              <AutoSuggest query={data.condition} items={CONDITION_TEMPLATES[type] || []} onSelect={v => setField('condition', v)} />

              {type === 'buy' && (
                <>
                  <Text style={styles.modalLabel}>목표 비중 (%)</Text>
                  <TextInput style={styles.modalInput} value={String(data.weight_pct ?? '')} onChangeText={v => setField('weight_pct', v === '' ? '' : Number(v))} keyboardType="numeric" placeholder="예: 30" placeholderTextColor="#94A3B8" />
                  <AutoSuggest query={String(data.weight_pct ?? '')} items={['10', '15', '20', '30', '50', '70', '100']} onSelect={v => setField('weight_pct', Number(v))} showAll />

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
                  <AutoSuggest query={String(data.sell_pct ?? '')} items={['25', '35', '50', '75', '100']} onSelect={v => setField('sell_pct', Number(v))} showAll />

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
                    {[{ v: 'liquidate', label: '전량 청산+잠금' }, { v: 'lock_buy', label: '신규 매수 잠금' }].map(opt => (
                      <TouchableOpacity key={opt.v} style={[styles.toggleBtn, data.action === opt.v && styles.toggleBtnActive]} onPress={() => setField('action', opt.v)}>
                        <Text style={[styles.toggleBtnText, data.action === opt.v && styles.toggleBtnTextActive]}>{opt.label}</Text>
                      </TouchableOpacity>
                    ))}
                  </View>
                </>
              )}
            </ScrollView>

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

  // ── 갤러리 모달 ──────────────────────────
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

  const isRunning = status?.running;

  return (
    <SafeAreaView style={styles.container} edges={['bottom']}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={loadStatus} tintColor="#6366F1" />}
      >
        {/* ── 상태 배너 ── */}
        <View style={[styles.statusBanner, isRunning ? styles.bannerRunning : styles.bannerStopped]}>
          <View style={[styles.statusDot, isRunning ? styles.dotRunning : styles.dotStopped]} />
          <Text style={styles.statusText}>{isRunning ? '자동매매 실행 중' : '자동매매 정지'}</Text>
          <Text style={styles.modeText}>{(status?.trade_mode ?? tradeMode) === 'real' ? '🔴 실전' : '🟡 모의'}</Text>
        </View>

        {status?.error && (
          <View style={styles.errorBanner}>
            <Text style={styles.errorBannerText}>⚠️ {status.error}</Text>
          </View>
        )}

        {/* ── 거래 모드 ── */}
        <Text style={styles.sectionTitle}>💼 거래 모드 선택</Text>
        <View style={styles.modeRow}>
          <TouchableOpacity style={[styles.modeBtn, tradeMode === 'paper' && styles.modeBtnPaper]} onPress={() => !isRunning && setTradeMode('paper')} disabled={isRunning}>
            <Text style={[styles.modeBtnText, tradeMode === 'paper' && styles.modeBtnTextActive]}>🟡 모의투자</Text>
            <Text style={styles.modeBtnSub}>가상머니 · 안전하게 연습</Text>
          </TouchableOpacity>
          <TouchableOpacity
            style={[styles.modeBtn, tradeMode === 'real' && styles.modeBtnReal]}
            onPress={() => {
              if (isRunning) return;
              Alert.alert('실전투자 선택', '실제 계좌의 실제 돈으로 거래됩니다.\n정말 전환하시겠습니까?', [
                { text: '취소', style: 'cancel' },
                { text: '실전으로 전환', style: 'destructive', onPress: () => setTradeMode('real') },
              ]);
            }}
            disabled={isRunning}
          >
            <Text style={[styles.modeBtnText, tradeMode === 'real' && styles.modeBtnTextActive]}>🔴 실전투자</Text>
            <Text style={styles.modeBtnSub}>실제 돈 · KIS 계좌 필요</Text>
          </TouchableOpacity>
        </View>
        {tradeMode === 'real' && (
          <View style={styles.realWarning}>
            <Text style={styles.realWarningText}>⚠️ 실전투자 모드입니다. 실제 돈으로 거래됩니다.</Text>
          </View>
        )}

        {/* ── 조건 등록 ── */}
        {/* ── Phase 상태 배너 ── */}
        {status?.phase_state && (
          <View style={[styles.phaseBanner, status.phase_state.locked && styles.phaseBannerLocked]}>
            <Text style={styles.phaseLabel}>
              {status.phase_state.locked ? '🔒 잠금' : `📍 Phase ${status.phase_state.phase}`}
            </Text>
            <Text style={styles.phaseName}>{status.phase_state.phase_name}</Text>
          </View>
        )}

        {/* ── 전략 템플릿 ── */}
        <View style={styles.templateRow}>
          <Text style={styles.sectionTitle}>📂 전략 템플릿</Text>
          <TouchableOpacity style={styles.templateSaveBtn} onPress={() => setSaveNameModal(true)}>
            <Text style={styles.templateSaveBtnText}>+ 저장</Text>
          </TouchableOpacity>
        </View>
        {activeStrategy ? (
          <View style={styles.activeStrategyCard}>
            <View style={{ flex: 1 }}>
              <Text style={styles.activeStrategyName}>{activeStrategy.name}</Text>
              <Text style={styles.activeStrategyDesc} numberOfLines={1}>{activeStrategy.description}</Text>
            </View>
            <TouchableOpacity onPress={openTemplateModal} style={styles.templateChangeBtn}>
              <Text style={styles.templateChangeBtnText}>변경</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={() => { setActiveStrategy(null); }} style={[styles.templateChangeBtn, { backgroundColor: '#FEE2E2', marginLeft: 6 }]}>
              <Text style={[styles.templateChangeBtnText, { color: '#DC2626' }]}>해제</Text>
            </TouchableOpacity>
          </View>
        ) : (
          <TouchableOpacity style={styles.templatePickBtn} onPress={openTemplateModal}>
            <Text style={styles.templatePickBtnText}>📂  전략 불러오기</Text>
            <Text style={styles.templatePickBtnSub}>눈덩이 TQQQ 등 저장된 전략 선택</Text>
          </TouchableOpacity>
        )}

        <Text style={styles.sectionTitle}>📋 조건 등록</Text>
        <View style={styles.uploadBtns}>
          <TouchableOpacity style={[styles.imgBtn, analyzing && styles.btnDisabled]} onPress={pickImage} disabled={analyzing}>
            {analyzing ? <ActivityIndicator color="#fff" /> : <Text style={styles.imgBtnText}>📁 갤러리에서 선택</Text>}
          </TouchableOpacity>
          <TouchableOpacity style={[styles.imgBtn, styles.imgBtnSec, analyzing && styles.btnDisabled]} onPress={() => setTextModalVisible(true)} disabled={analyzing}>
            <Text style={[styles.imgBtnText, { color: '#6366F1' }]}>📝 텍스트 붙여넣기</Text>
          </TouchableOpacity>
        </View>

        {/* ── AI 앱 바로가기 ── */}
        <View style={styles.aiShortcutRow}>
          <Text style={styles.aiShortcutLabel}>AI로 전략 만들기</Text>
          <View style={styles.aiShortcutBtns}>
            <TouchableOpacity
              style={[styles.aiShortcutBtn, { backgroundColor: '#1a73e8' }]}
              onPress={() => Linking.openURL('https://gemini.google.com/app').catch(() => Linking.openURL('https://gemini.google.com'))}
            >
              <Text style={styles.aiShortcutIcon}>✦</Text>
              <Text style={styles.aiShortcutText}>Gemini</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.aiShortcutBtn, { backgroundColor: '#D97706' }]}
              onPress={() => Linking.openURL('https://claude.ai/new').catch(() => Linking.openURL('https://claude.ai'))}
            >
              <Text style={styles.aiShortcutIcon}>◆</Text>
              <Text style={styles.aiShortcutText}>Claude</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.aiShortcutBtn, { backgroundColor: '#10A37F' }]}
              onPress={() => Linking.openURL('https://chatgpt.com').catch(() => Linking.openURL('https://chat.openai.com'))}
            >
              <Text style={styles.aiShortcutIcon}>⬡</Text>
              <Text style={styles.aiShortcutText}>ChatGPT</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* 이미지 썸네일 */}
        {imageUris.length > 0 && (
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.thumbList}>
            {imageUris.map((uri, i) => (
              <TouchableOpacity key={i} style={styles.thumbWrap} onPress={() => { setGalleryIndex(i); setGalleryVisible(true); }}>
                <Image source={{ uri }} style={styles.thumbImg} />
                <TouchableOpacity style={styles.thumbDel} onPress={() => removeImage(i)}>
                  <Text style={styles.thumbDelText}>✕</Text>
                </TouchableOpacity>
                <Text style={styles.thumbLabel}>사진 {i + 1}</Text>
              </TouchableOpacity>
            ))}
            <TouchableOpacity style={styles.thumbAdd} onPress={pickImage}>
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

        {/* ── 추출된 조건 + 일치율 ── */}
        {conditions && (
          <>
            {/* 일치율 카드 */}
            {verification && verification.match_pct >= 0 && (() => {
              const pct   = verification.match_pct ?? 0;
              const color = pct >= 80 ? '#16A34A' : pct >= 60 ? '#D97706' : '#DC2626';
              const bg    = pct >= 80 ? '#DCFCE7' : pct >= 60 ? '#FEF3C7' : '#FEE2E2';
              const label = pct >= 80 ? '높음' : pct >= 60 ? '보통' : '낮음';
              const missing = verification.missing ?? [];
              const wrong   = verification.wrong   ?? [];
              return (
                <View style={[styles.verifyCard, { borderColor: color }]}>
                  <View style={styles.verifyTop}>
                    <View style={[styles.verifyCircle, { backgroundColor: bg }]}>
                      <Text style={[styles.verifyPct, { color }]}>{pct}%</Text>
                    </View>
                    <View style={styles.verifyInfo}>
                      <Text style={styles.verifyTitle}>AI 이중 검증 — 일치율 <Text style={{ color }}>{label}</Text></Text>
                      <Text style={styles.verifyNotes}>{verification.notes}</Text>
                      <Text style={styles.verifyMeta}>원본 {verification.total_in_source ?? '?'}건 중 {verification.total_extracted ?? '?'}건 추출</Text>
                    </View>
                  </View>
                  {(missing.length > 0 || wrong.length > 0) && (
                    <TouchableOpacity style={styles.verifyToggle} onPress={() => setShowMissing(v => !v)}>
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

            {/* 조건 카드 */}
            <TouchableOpacity style={styles.condSummaryCard} onPress={() => setCondExpanded(v => !v)} activeOpacity={0.8}>
              <View style={styles.condSummaryHeader}>
                <Text style={styles.condSummaryTitle}>📋 추출된 조건</Text>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                  <TouchableOpacity
                    onPress={(e) => {
                      e.stopPropagation();
                      Alert.alert('전체 삭제', '모든 조건과 거래 로그를 초기화할까요?\n(자동매매가 실행 중이면 자동으로 정지됩니다)', [
                        { text: '취소', style: 'cancel' },
                        { text: '삭제', style: 'destructive', onPress: async () => {
                          try {
                            await resetAutoTradeConditions();
                            setConditions(null);
                            setVerification(null);
                            setImageUris([]);
                            setCondExpanded(false);
                            await loadStatus(true);
                          } catch (err) {
                            Alert.alert('오류', '초기화 실패: ' + (err?.message ?? err));
                          }
                        }},
                      ]);
                    }}
                    style={styles.resetAllBtn}
                    hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                  >
                    <Text style={styles.resetAllBtnText}>전체 삭제</Text>
                  </TouchableOpacity>
                  <Text style={styles.condToggleIcon}>{condExpanded ? '▲' : '▼'}</Text>
                </View>
              </View>
              <Text style={styles.condSummaryText}>{conditions.summary}</Text>
              <Text style={styles.condSummaryMeta}>
                매수 {conditions.buy_conditions?.length ?? 0}건 · 매도 {conditions.sell_conditions?.length ?? 0}건
                {conditions.lock_conditions?.length > 0 ? ` · 락 ${conditions.lock_conditions.length}건` : ''} · 탭하면 상세보기
              </Text>
              {condExpanded && renderConditionDetail()}
            </TouchableOpacity>
          </>
        )}

        {/* ── 일치 확인 버튼 ── */}
        {(conditions || activeStrategy) && (
          <TouchableOpacity style={styles.matchCheckBtn} onPress={openMatchCheck}>
            <Text style={styles.matchCheckBtnText}>🔍 일치 확인</Text>
            <Text style={styles.matchCheckBtnSub}>
              {activeStrategy ? `"${activeStrategy.name}" 전략 전체 조건 복사 후 AI 검증` : '조건을 복사 후 AI 앱에서 원본과 비교'}
            </Text>
          </TouchableOpacity>
        )}

        {/* ── 시작/정지 버튼 ── */}
        <TouchableOpacity
          style={[styles.mainBtn, isRunning ? styles.stopBtn : styles.startBtn, (toggling || analyzing) && styles.btnDisabled]}
          onPress={toggleTrading}
          disabled={toggling || analyzing}
        >
          {toggling ? <ActivityIndicator color="#fff" size="large" /> : (
            <Text style={styles.mainBtnText}>{isRunning ? '⏹  자동매매 정지' : '▶  자동매매 시작'}</Text>
          )}
        </TouchableOpacity>

        {/* ── 거래 로그 ── */}
        {status?.trade_log?.length > 0 && (
          <>
            <Text style={styles.sectionTitle}>📜 최근 거래 내역</Text>
            {status.trade_log.map((e, i) => {
              const isError = e.action.includes('실패');
              return (
                <View key={i} style={styles.logRow}>
                  <Text style={[styles.logAction, isError ? styles.errText : e.action === '매수' ? styles.buyText : styles.sellText]}>{e.action}</Text>
                  <View style={styles.logDetail}>
                    <Text style={styles.logName}>{e.name} ({e.ticker})</Text>
                    <Text style={styles.logMeta}>{e.price?.toLocaleString()}원 · {e.qty}주 · {e.time}</Text>
                    <Text style={styles.logResult} numberOfLines={1}>{e.result}</Text>
                  </View>
                </View>
              );
            })}
          </>
        )}

        {status?.last_check && (
          <Text style={styles.lastCheck}>마지막 체크: {status.last_check}</Text>
        )}
      </ScrollView>

      {/* 템플릿 선택 모달 */}
      <Modal visible={templateModal} transparent animationType="slide" onRequestClose={() => setTemplateModal(false)}>
        <View style={{ flex: 1, justifyContent: 'flex-end' }}>
          <TouchableOpacity style={StyleSheet.absoluteFill} activeOpacity={1} onPress={() => setTemplateModal(false)} />
          <View style={[styles.textModalBox, { paddingBottom: Platform.OS === 'ios' ? 34 : 16, maxHeight: SCREEN_H * 0.75 }]}>
            <Text style={styles.modalTitle}>📂 전략 템플릿</Text>
            <ScrollView style={{ maxHeight: SCREEN_H * 0.5 }} showsVerticalScrollIndicator={false}>
              {templateList.map((t, i) => (
                <View key={i} style={styles.templateItem}>
                  <TouchableOpacity style={{ flex: 1 }} onPress={() => loadTemplateByName(t.name)}>
                    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                      <Text style={styles.templateItemName}>{t.name}</Text>
                      {t.builtin && <View style={styles.builtinBadge}><Text style={styles.builtinBadgeText}>내장</Text></View>}
                    </View>
                    <Text style={styles.templateItemDesc} numberOfLines={1}>{t.description}</Text>
                    {t.ticker ? <Text style={styles.templateItemMeta}>{t.ticker}</Text> : null}
                  </TouchableOpacity>
                  {!t.builtin && (
                    <TouchableOpacity onPress={() => deleteTemplateByName(t.name)} style={styles.templateDelBtn}>
                      <Text style={styles.templateDelBtnText}>삭제</Text>
                    </TouchableOpacity>
                  )}
                </View>
              ))}
              {templateList.length === 0 && (
                <Text style={{ color: '#94A3B8', textAlign: 'center', paddingVertical: 20 }}>저장된 템플릿이 없습니다.</Text>
              )}
            </ScrollView>
            <TouchableOpacity style={[styles.modalCancel, { marginTop: 12 }]} onPress={() => setTemplateModal(false)}>
              <Text style={styles.modalCancelText}>닫기</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>

      {/* 템플릿 이름 저장 모달 */}
      <Modal visible={saveNameModal} transparent animationType="fade" onRequestClose={() => setSaveNameModal(false)}>
        <View style={{ flex: 1, justifyContent: 'center', alignItems: 'center', backgroundColor: 'rgba(0,0,0,0.4)' }}>
          <View style={{ backgroundColor: '#fff', borderRadius: 16, padding: 24, width: SCREEN_W - 48 }}>
            <Text style={styles.modalTitle}>💾 전략 저장</Text>
            <TextInput
              style={[styles.modalInput, { marginBottom: 16 }]}
              value={saveNameText}
              onChangeText={setSaveNameText}
              placeholder="전략 이름 (예: 나스닥 역추세)"
              placeholderTextColor="#94A3B8"
              autoFocus
            />
            <View style={styles.textModalBtns}>
              <TouchableOpacity style={styles.modalCancel} onPress={() => { setSaveNameModal(false); setSaveNameText(''); }}>
                <Text style={styles.modalCancelText}>취소</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.modalSave} onPress={saveCurrentAsTemplate}>
                <Text style={styles.modalSaveText}>저장</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {renderGalleryModal()}
      {renderEditModal()}

      {/* 텍스트 붙여넣기 모달 */}
      <Modal visible={textModalVisible} transparent animationType="slide" onRequestClose={() => setTextModalVisible(false)}>
        <View style={{ flex: 1, justifyContent: 'flex-end' }}>
          <TouchableOpacity style={StyleSheet.absoluteFill} activeOpacity={1} onPress={() => { setTextModalVisible(false); Keyboard.dismiss(); }} />
          <View style={[styles.textModalBox, { paddingBottom: Math.max(kbHeight, Platform.OS === 'ios' ? 34 : 16) }]}>
            <Text style={styles.modalTitle}>📝 전략 텍스트 붙여넣기</Text>
            {conditions ? (
              <Text style={[styles.modalLabel, { marginTop: 0, marginBottom: 8, color: '#F59E0B' }]}>
                ✏️ 수정 사항을 붙여넣으면 기존 조건에 AI가 반영합니다.{'\n'}새 전략으로 완전 교체하려면 "AI 분석"을 누르세요.
              </Text>
            ) : (
              <Text style={[styles.modalLabel, { marginTop: 0, marginBottom: 8 }]}>
                매수/매도 조건이 적힌 텍스트를 붙여넣으면 AI가 자동으로 조건을 추출합니다.
              </Text>
            )}
            <TextInput
              style={styles.textModalInput}
              value={pasteText}
              onChangeText={setPasteText}
              placeholder={conditions
                ? '예)\nRSI 35 조건이 이상→이하로 잘못됨, 수정 필요\n데드크로스 매도 수량 100% 전량 매도로 추가'
                : '예)\nTQQQ: QQQ 고점 대비 -10% 시 30% 매수\nTP1: +15% 시 최초수량 50% 매도\n락: QQQ -40% 이하 시 전량 청산'
              }
              placeholderTextColor="#94A3B8"
              multiline autoFocus
            />
            <View style={styles.textModalBtns}>
              <TouchableOpacity style={styles.modalCancel} onPress={() => { setTextModalVisible(false); setPasteText(''); Keyboard.dismiss(); }}>
                <Text style={styles.modalCancelText}>취소</Text>
              </TouchableOpacity>
              {conditions && (
                <TouchableOpacity style={[styles.modalSave, { backgroundColor: '#F59E0B' }]} onPress={applyFix}>
                  <Text style={styles.modalSaveText}>🔧 수정 적용</Text>
                </TouchableOpacity>
              )}
              <TouchableOpacity style={styles.modalSave} onPress={analyzeText}>
                <Text style={styles.modalSaveText}>✨ AI 분석</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F1F5F9' },
  scroll: { padding: 16, paddingBottom: 40 },
  sectionTitle: { fontSize: 15, fontWeight: '700', color: '#1E293B', marginTop: 20, marginBottom: 10 },
  btnDisabled: { opacity: 0.5 },

  // 상태 배너
  statusBanner: { flexDirection: 'row', alignItems: 'center', padding: 14, borderRadius: 12, marginBottom: 16 },
  bannerRunning: { backgroundColor: '#DCFCE7' },
  bannerStopped: { backgroundColor: '#F1F5F9', borderWidth: 1, borderColor: '#CBD5E1' },
  statusDot: { width: 10, height: 10, borderRadius: 5, marginRight: 8 },
  dotRunning: { backgroundColor: '#16A34A' },
  dotStopped: { backgroundColor: '#94A3B8' },
  statusText: { fontSize: 16, fontWeight: '700', color: '#1E293B', flex: 1 },
  modeText: { fontSize: 13, color: '#475569' },
  errorBanner: { backgroundColor: '#FEE2E2', padding: 10, borderRadius: 8, marginBottom: 12 },
  errorBannerText: { color: '#DC2626', fontSize: 13 },

  // 거래 모드
  modeRow: { flexDirection: 'row', gap: 10 },
  modeBtn: { flex: 1, padding: 14, borderRadius: 12, borderWidth: 2, borderColor: '#CBD5E1', backgroundColor: '#fff', alignItems: 'center' },
  modeBtnPaper: { borderColor: '#F59E0B', backgroundColor: '#FFFBEB' },
  modeBtnReal:  { borderColor: '#DC2626', backgroundColor: '#FEF2F2' },
  modeBtnText:  { fontSize: 15, fontWeight: '700', color: '#475569' },
  modeBtnTextActive: { color: '#1E293B' },
  modeBtnSub:   { fontSize: 11, color: '#94A3B8', marginTop: 3 },
  realWarning: { marginTop: 8, backgroundColor: '#FEE2E2', borderRadius: 8, padding: 10, borderLeftWidth: 3, borderLeftColor: '#DC2626' },
  realWarningText: { fontSize: 12, color: '#B91C1C', fontWeight: '600' },

  // 업로드
  uploadBtns: { flexDirection: 'row', gap: 10 },
  imgBtn: { flex: 1, backgroundColor: '#6366F1', padding: 14, borderRadius: 10, alignItems: 'center' },
  imgBtnSec: { backgroundColor: '#fff', borderWidth: 1.5, borderColor: '#6366F1' },
  imgBtnText: { color: '#fff', fontWeight: '700', fontSize: 14 },
  aiShortcutRow: { marginTop: 10, marginBottom: 4 },
  aiShortcutLabel: { fontSize: 11, color: '#94A3B8', marginBottom: 6, textAlign: 'center' },
  aiShortcutBtns: { flexDirection: 'row', gap: 8 },
  aiShortcutBtn: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, paddingVertical: 9, borderRadius: 10 },
  aiShortcutIcon: { fontSize: 14, color: '#fff', fontWeight: '700' },
  aiShortcutText: { fontSize: 13, color: '#fff', fontWeight: '700' },
  subText: { textAlign: 'center', color: '#6366F1', marginTop: 8, fontSize: 13 },
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

  // 일치율
  verifyCard: { backgroundColor: '#fff', borderRadius: 12, borderWidth: 1.5, padding: 14, marginTop: 12 },
  verifyTop: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  verifyCircle: { width: 64, height: 64, borderRadius: 32, alignItems: 'center', justifyContent: 'center' },
  verifyPct: { fontSize: 20, fontWeight: '800' },
  verifyInfo: { flex: 1 },
  verifyTitle: { fontSize: 13, fontWeight: '700', color: '#1E293B', marginBottom: 3 },
  verifyNotes: { fontSize: 12, color: '#475569', marginBottom: 2 },
  verifyMeta: { fontSize: 11, color: '#94A3B8' },
  verifyToggle: { marginTop: 10, alignItems: 'center', paddingVertical: 6 },
  verifyToggleText: { fontSize: 13, fontWeight: '600' },
  verifyDetail: { marginTop: 8, gap: 6 },
  verifyItem: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  verifyItemBadge: { fontSize: 10, fontWeight: '700', color: '#D97706', backgroundColor: '#FEF3C7', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, marginTop: 1 },
  verifyWrongBadge: { color: '#DC2626', backgroundColor: '#FEE2E2' },
  verifyItemText: { fontSize: 12, color: '#475569', flex: 1 },

  // 조건 카드
  condSummaryCard: { backgroundColor: '#fff', borderRadius: 10, padding: 14, marginTop: 12, borderLeftWidth: 3, borderLeftColor: '#6366F1' },
  condSummaryHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 },
  condSummaryTitle: { fontSize: 13, fontWeight: '700', color: '#6366F1' },
  condToggleIcon: { fontSize: 12, color: '#6366F1' },
  resetAllBtn: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6, backgroundColor: '#FEE2E2', borderWidth: 1, borderColor: '#FECACA' },
  resetAllBtnText: { fontSize: 11, color: '#DC2626', fontWeight: '600' },
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
  emptyText: { fontSize: 13, color: '#94A3B8', textAlign: 'center', marginVertical: 8 },

  // 시작/정지
  // Phase 배너
  phaseBanner: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: '#EEF2FF', borderRadius: 10, padding: 10, marginBottom: 8 },
  phaseBannerLocked: { backgroundColor: '#FEF2F2' },
  phaseLabel: { fontSize: 13, fontWeight: '800', color: '#6366F1' },
  phaseName: { fontSize: 12, color: '#475569', flex: 1 },
  // 템플릿
  templateRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 16, marginBottom: 8 },
  templateSaveBtn: { paddingHorizontal: 12, paddingVertical: 5, backgroundColor: '#EEF2FF', borderRadius: 8 },
  templateSaveBtnText: { fontSize: 12, color: '#6366F1', fontWeight: '700' },
  activeStrategyCard: { flexDirection: 'row', alignItems: 'center', backgroundColor: '#fff', borderRadius: 12, padding: 14, marginBottom: 8, borderWidth: 1.5, borderColor: '#6366F1', gap: 8 },
  activeStrategyName: { fontSize: 14, fontWeight: '800', color: '#1E293B' },
  activeStrategyDesc: { fontSize: 11, color: '#94A3B8', marginTop: 2 },
  templateChangeBtn: { paddingHorizontal: 10, paddingVertical: 5, backgroundColor: '#EEF2FF', borderRadius: 8 },
  templateChangeBtnText: { fontSize: 12, color: '#6366F1', fontWeight: '600' },
  templatePickBtn: { backgroundColor: '#fff', borderRadius: 12, padding: 16, marginBottom: 8, borderWidth: 1.5, borderColor: '#CBD5E1', alignItems: 'center' },
  templatePickBtnText: { fontSize: 14, fontWeight: '700', color: '#475569' },
  templatePickBtnSub: { fontSize: 11, color: '#94A3B8', marginTop: 3 },
  // 템플릿 모달 아이템
  templateItem: { flexDirection: 'row', alignItems: 'center', paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: '#F1F5F9' },
  templateItemName: { fontSize: 14, fontWeight: '700', color: '#1E293B' },
  templateItemDesc: { fontSize: 11, color: '#94A3B8', marginTop: 2 },
  templateItemMeta: { fontSize: 11, color: '#6366F1', marginTop: 2 },
  builtinBadge: { backgroundColor: '#EEF2FF', paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4 },
  builtinBadgeText: { fontSize: 10, color: '#6366F1', fontWeight: '700' },
  templateDelBtn: { paddingHorizontal: 10, paddingVertical: 5, backgroundColor: '#FEE2E2', borderRadius: 8, marginLeft: 8 },
  templateDelBtnText: { fontSize: 12, color: '#DC2626', fontWeight: '600' },
  matchCheckBtn: { marginTop: 16, padding: 14, borderRadius: 12, backgroundColor: '#fff', borderWidth: 1.5, borderColor: '#6366F1', alignItems: 'center' },
  matchCheckBtnText: { fontSize: 15, fontWeight: '700', color: '#6366F1' },
  matchCheckBtnSub: { fontSize: 11, color: '#94A3B8', marginTop: 2 },
  mainBtn: { marginTop: 12, padding: 18, borderRadius: 14, alignItems: 'center', elevation: 3 },
  startBtn: { backgroundColor: '#16A34A' },
  stopBtn:  { backgroundColor: '#DC2626' },
  mainBtnText: { color: '#fff', fontSize: 18, fontWeight: '800' },

  // 로그
  logRow: { flexDirection: 'row', backgroundColor: '#fff', borderRadius: 8, padding: 10, marginBottom: 6, alignItems: 'flex-start' },
  logAction: { fontSize: 12, fontWeight: '700', width: 44, marginTop: 2 },
  buyText:  { color: '#16A34A' },
  sellText: { color: '#DC2626' },
  errText:  { color: '#F59E0B' },
  logDetail: { flex: 1 },
  logName:   { fontSize: 13, fontWeight: '600', color: '#1E293B' },
  logMeta:   { fontSize: 11, color: '#64748B', marginTop: 2 },
  logResult: { fontSize: 11, color: '#94A3B8', marginTop: 2 },
  lastCheck: { textAlign: 'center', fontSize: 11, color: '#94A3B8', marginTop: 16 },

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

  // 모달
  textModalBox: { backgroundColor: '#fff', borderTopLeftRadius: 20, borderTopRightRadius: 20, paddingHorizontal: 20, paddingTop: 20, paddingBottom: Platform.OS === 'ios' ? 34 : 16 },
  textModalInput: { borderWidth: 1, borderColor: '#E2E8F0', borderRadius: 10, padding: 12, fontSize: 14, color: '#1E293B', height: 160, textAlignVertical: 'top', marginBottom: 12 },
  textModalBtns: { flexDirection: 'row', gap: 8 },
  modalBox: { backgroundColor: '#fff', borderTopLeftRadius: 20, borderTopRightRadius: 20, paddingHorizontal: 20, paddingTop: 20, paddingBottom: Platform.OS === 'ios' ? 34 : 20, height: SCREEN_H * 0.78, flexDirection: 'column' },
  modalTitle: { fontSize: 16, fontWeight: '800', color: '#1E293B', marginBottom: 8 },
  modalError: { backgroundColor: '#FEE2E2', borderRadius: 8, padding: 10, marginBottom: 8 },
  modalErrorText: { color: '#DC2626', fontSize: 13, fontWeight: '600' },
  modalScroll: { flex: 1 },
  modalLabel: { fontSize: 12, fontWeight: '600', color: '#64748B', marginBottom: 4, marginTop: 12 },
  modalInput: { backgroundColor: '#F8FAFC', borderWidth: 1, borderColor: '#E2E8F0', borderRadius: 8, paddingHorizontal: 12, paddingVertical: 10, fontSize: 14, color: '#1E293B' },
  modalInputMulti: { height: 72, textAlignVertical: 'top' },
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

  // 자동완성
  suggestBox: { backgroundColor: '#fff', borderWidth: 1, borderColor: '#E2E8F0', borderRadius: 10, marginTop: 4, overflow: 'hidden', elevation: 3 },
  suggestItem: { paddingHorizontal: 14, paddingVertical: 12 },
  suggestItemBorder: { borderBottomWidth: 1, borderBottomColor: '#F1F5F9' },
  suggestText: { fontSize: 13, color: '#1E293B' },
});
