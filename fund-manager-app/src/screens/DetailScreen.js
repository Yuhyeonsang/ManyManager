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
import { SafeAreaView } from 'react-native-safe-area-context';

import GradeBadge from '../components/GradeBadge';
import { fetchStockReport, fetchClipboardText } from '../services/api';
import { cacheReport, getCachedReport } from '../services/database';
import {
  copyToClipboard,
  formatReportForClipboard,
} from '../utils/clipboard';

export default function DetailScreen({ route }) {
  const { ticker } = route.params;
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [copying, setCopying] = useState(false);

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
      // 1순위: 서버가 정리해 둔 텍스트 사용 (있으면 가장 깔끔)
      let text;
      try {
        text = await fetchClipboardText(ticker);
      } catch {
        // 서버 엔드포인트가 없거나 오프라인이면 클라이언트에서 직접 포맷팅
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
      <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 120 }}>
        {offline && (
          <View style={styles.offlineBanner}>
            <Text style={styles.offlineText}>
              ⚠ 오프라인 — 마지막에 저장된 리포트입니다
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

        <Section title="📰 AI 뉴스 요약">
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

        <Section title="📊 주요 재무 수치">
          <FinancialRow label="PER" value={f.per} suffix="배" />
          <FinancialRow label="PBR" value={f.pbr} suffix="배" />
          <FinancialRow label="ROE" value={f.roe} suffix="%" />
          <FinancialRow
            label="매출 성장률"
            value={f.revenue_growth}
            suffix="%"
          />
          <FinancialRow
            label="영업이익률"
            value={f.operating_margin}
            suffix="%"
          />
          <FinancialRow label="부채비율" value={f.debt_ratio} suffix="%" />
        </Section>

        {report.updated_at && (
          <Text style={styles.updatedAt}>
            데이터 기준: {report.updated_at}
          </Text>
        )}
      </ScrollView>

      <View style={styles.footer}>
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
              📋 텍스트 리포트 복사 (Claude 웹에 붙여넣기)
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

function FinancialRow({ label, value, suffix }) {
  const display =
    value === null || value === undefined ? '-' : `${value}${suffix ?? ''}`;
  return (
    <View style={styles.finRow}>
      <Text style={styles.finLabel}>{label}</Text>
      <Text style={styles.finValue}>{display}</Text>
    </View>
  );
}

// ─────────────────────────────────────────────
// 뉴스 한 줄 — 탭하면 링크 열림, "N시간 전" 표시
// ─────────────────────────────────────────────
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
        <Text style={styles.newsLink}>탭하여 원문 보기 ›</Text>
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

// pub_date (RFC 2822 또는 ISO) → 현재 시각 기준 상대 시간
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
