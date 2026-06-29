import React from 'react';
import { Pressable, View, Text, StyleSheet } from 'react-native';
import GradeBadge from './GradeBadge';

// 티커로 통화 판별: KR(숫자/.KS/.KQ) → 원, 그 외 → 달러
function formatStockPrice(price, ticker) {
  const t = String(ticker || '');
  const isKR = /\d/.test(t) || /\.(KS|KQ)$/i.test(t);
  const n = Number(price ?? 0);
  if (isKR) return `${n.toLocaleString('ko-KR')}원`;
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export default function StockCard({ stock, onPress, isFavorited, onFavoriteToggle }) {
  const changeColor = (stock.change_pct ?? 0) >= 0 ? '#DC2626' : '#2563EB';
  const sign = (stock.change_pct ?? 0) >= 0 ? '+' : '';

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.card, pressed && styles.pressed]}
    >
      <View style={styles.row}>
        <View style={{ flex: 1 }}>
          <Text style={styles.name}>{stock.name}</Text>
          <Text style={styles.ticker}>{stock.ticker}</Text>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <GradeBadge grade={stock.grade} />
          {onFavoriteToggle && (
            <Pressable
              onPress={(e) => { e.stopPropagation?.(); onFavoriteToggle(stock); }}
              hitSlop={10}
              style={({ pressed }) => [styles.starBtn, pressed && { opacity: 0.5 }]}
            >
              <Text style={styles.starIcon}>{isFavorited ? '⭐' : '☆'}</Text>
            </Pressable>
          )}
        </View>
      </View>

      <View style={[styles.row, { marginTop: 12 }]}>
        <View>
          <Text style={styles.priceLabel}>현재가</Text>
          <Text style={styles.price}>
            {formatStockPrice(stock.price, stock.ticker)}
          </Text>
        </View>
        <View style={{ alignItems: 'flex-end' }}>
          <Text style={styles.priceLabel}>등락률</Text>
          <Text style={[styles.change, { color: changeColor }]}>
            {sign}{Number(stock.change_pct ?? 0).toFixed(2)}%
          </Text>
        </View>
      </View>

      {stock.summary ? (
        <Text style={styles.summary} numberOfLines={2}>
          {stock.summary}
        </Text>
      ) : null}

      <View style={styles.scoreBar}>
        <View
          style={[
            styles.scoreFill,
            { width: `${Math.max(0, Math.min(100, stock.score ?? 0))}%` },
          ]}
        />
      </View>
      <Text style={styles.scoreText}>AI 점수 {stock.score ?? '-'}/100</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#FFFFFF',
    borderRadius: 16,
    padding: 16,
    marginHorizontal: 16,
    marginVertical: 6,
    shadowColor: '#000',
    shadowOpacity: 0.06,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: 2,
  },
  pressed: { opacity: 0.7 },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  name: { fontSize: 17, fontWeight: '700', color: '#0F172A' },
  ticker: { fontSize: 12, color: '#64748B', marginTop: 2 },
  starBtn: { padding: 2 },
  starIcon: { fontSize: 20 },
  priceLabel: { fontSize: 11, color: '#94A3B8' },
  price: { fontSize: 16, fontWeight: '600', color: '#0F172A', marginTop: 2 },
  change: { fontSize: 16, fontWeight: '700', marginTop: 2 },
  summary: { marginTop: 10, color: '#475569', fontSize: 13, lineHeight: 18 },
  scoreBar: {
    height: 6,
    backgroundColor: '#E2E8F0',
    borderRadius: 999,
    marginTop: 12,
    overflow: 'hidden',
  },
  scoreFill: { height: '100%', backgroundColor: '#0F172A' },
  scoreText: { marginTop: 6, fontSize: 11, color: '#64748B' },
});
