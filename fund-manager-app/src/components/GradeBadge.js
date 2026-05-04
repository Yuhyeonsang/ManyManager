import React from 'react';
import { View, Text, StyleSheet } from 'react-native';

const GRADE_STYLE = {
  BUY:  { bg: '#DCFCE7', fg: '#15803D', label: '매수' },
  HOLD: { bg: '#FEF9C3', fg: '#A16207', label: '관망' },
  SELL: { bg: '#FEE2E2', fg: '#B91C1C', label: '매도' },
};

export default function GradeBadge({ grade }) {
  const style = GRADE_STYLE[grade] || GRADE_STYLE.HOLD;
  return (
    <View style={[styles.badge, { backgroundColor: style.bg }]}>
      <Text style={[styles.text, { color: style.fg }]}>{style.label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
    alignSelf: 'flex-start',
  },
  text: {
    fontSize: 12,
    fontWeight: '700',
  },
});
