// 설정 화면
//   - 서버 URL (BASE_URL) 을 .apk 재빌드 없이 바꿀 수 있는 화면
//   - SQLite 의 settings.base_url 에 영구 저장
//   - "연결 테스트" 로 즉시 검증

import React, { useEffect, useState, useCallback } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  ScrollView,
  Alert,
  ActivityIndicator,
  Keyboard,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import {
  BASE_URL as DEFAULT_BASE_URL,
  getEffectiveBaseURL,
  updateBaseURL,
  pingServer,
} from '../services/api';

export default function SettingsScreen({ navigation }) {
  const [url, setUrl] = useState('');
  const [currentUrl, setCurrentUrl] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState(null); // { ok, ms, msg }

  const refresh = useCallback(async () => {
    setLoading(true);
    const eff = await getEffectiveBaseURL();
    setCurrentUrl(eff);
    setUrl(eff);
    setLoading(false);
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const handleSave = async () => {
    Keyboard.dismiss();
    setSaving(true);
    try {
      await updateBaseURL(url);
      setTestResult(null);
      await refresh();
      Alert.alert('저장됨', '새 서버 주소로 저장됐습니다.', [
        { text: '확인', onPress: () => navigation.goBack() },
      ]);
    } catch (e) {
      Alert.alert('저장 실패', e?.message ?? '알 수 없는 오류');
    } finally {
      setSaving(false);
    }
  };

  const handleResetToDefault = () => {
    Alert.alert(
      '기본값으로 복원',
      `현재 입력값을 ${DEFAULT_BASE_URL} 로 되돌릴까요?\n(저장 버튼을 눌러야 적용됩니다)`,
      [
        { text: '취소', style: 'cancel' },
        { text: '복원', onPress: () => { setUrl(DEFAULT_BASE_URL); setTestResult(null); } },
      ]
    );
  };

  const handleTest = async () => {
    Keyboard.dismiss();
    setTesting(true);
    setTestResult(null);
    try {
      // 임시로 입력값을 적용해서 ping
      await updateBaseURL(url);
      const ms = await pingServer();
      setTestResult({ ok: true, ms });
    } catch (e) {
      const msg =
        e?.code === 'ECONNABORTED'
          ? '응답 시간 초과 (서버가 깨어나는 중일 수 있어요. 다시 시도)'
          : e?.message ?? '연결 실패';
      setTestResult({ ok: false, msg });
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <SafeAreaView style={styles.center}>
        <ActivityIndicator size="large" color="#0F172A" />
      </SafeAreaView>
    );
  }

  return (
    <SafeAreaView style={styles.container} edges={['left', 'right']}>
      <ScrollView contentContainerStyle={{ padding: 16 }}>
        <Text style={styles.h2}>서버 주소</Text>
        <Text style={styles.help}>
          PC에서 돌고 있는 FastAPI 서버의 주소를 입력하세요. Cloudflare
          Tunnel 등으로 받은 주소가 바뀔 때마다 여기서 갱신하면 됩니다.
          (앱을 다시 빌드할 필요 없음)
        </Text>

        <TextInput
          style={styles.input}
          value={url}
          onChangeText={(v) => { setUrl(v); setTestResult(null); }}
          placeholder="https://your-tunnel.trycloudflare.com"
          placeholderTextColor="#94A3B8"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          spellCheck={false}
        />

        <View style={styles.row}>
          <Pressable
            onPress={handleTest}
            disabled={testing || !url.trim()}
            style={({ pressed }) => [
              styles.btn,
              styles.btnSecondary,
              (pressed || testing || !url.trim()) && { opacity: 0.7 },
            ]}
          >
            {testing
              ? <ActivityIndicator color="#0F172A" />
              : <Text style={styles.btnSecondaryText}>🔌 연결 테스트</Text>}
          </Pressable>

          <Pressable
            onPress={handleSave}
            disabled={saving || !url.trim()}
            style={({ pressed }) => [
              styles.btn,
              styles.btnPrimary,
              (pressed || saving || !url.trim()) && { opacity: 0.7 },
            ]}
          >
            {saving
              ? <ActivityIndicator color="#FFFFFF" />
              : <Text style={styles.btnPrimaryText}>💾 저장</Text>}
          </Pressable>
        </View>

        {testResult && (
          <View
            style={[
              styles.testBox,
              { backgroundColor: testResult.ok ? '#DCFCE7' : '#FEE2E2' },
            ]}
          >
            <Text
              style={[
                styles.testText,
                { color: testResult.ok ? '#166534' : '#991B1B' },
              ]}
            >
              {testResult.ok
                ? `✅ 연결 성공 — 응답 ${testResult.ms} ms`
                : `❌ 연결 실패 — ${testResult.msg}`}
            </Text>
          </View>
        )}

        <Pressable onPress={handleResetToDefault} style={styles.linkBtn}>
          <Text style={styles.linkText}>↺ 기본값으로 복원</Text>
        </Pressable>

        <View style={styles.divider} />

        <Text style={styles.h2}>현재 적용 중</Text>
        <View style={styles.infoBox}>
          <Text style={styles.infoLabel}>활성 BASE_URL</Text>
          <Text style={styles.infoValue} selectable>{currentUrl}</Text>
          <Text style={[styles.infoLabel, { marginTop: 12 }]}>빌드 기본값</Text>
          <Text style={styles.infoValue} selectable>{DEFAULT_BASE_URL}</Text>
        </View>

        <View style={styles.divider} />

        <Text style={styles.h2}>도움말</Text>
        <Text style={styles.help}>
          • 집 와이파이에서만 쓸 거면 {`http://192.168.x.x:8000`} 처럼 PC IP 입력{'\n'}
          • 밖에서도 쓰려면 Cloudflare Tunnel/ngrok 의 https URL 입력{'\n'}
          • 끝에 슬래시(/) 는 자동으로 제거됨{'\n'}
          • 저장 후 대시보드로 돌아가서 새로고침(↓) 하면 적용 확인 가능
        </Text>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#F1F5F9' },
  center: {
    flex: 1, alignItems: 'center', justifyContent: 'center',
  },
  h2: {
    fontSize: 14, fontWeight: '700', color: '#334155',
    marginBottom: 8,
  },
  help: {
    fontSize: 13, color: '#64748B', lineHeight: 19, marginBottom: 12,
  },
  input: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    fontSize: 15,
    color: '#0F172A',
    borderWidth: 1,
    borderColor: '#CBD5E1',
    marginBottom: 12,
  },
  row: {
    flexDirection: 'row',
    gap: 8,
  },
  btn: {
    flex: 1,
    paddingVertical: 14,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnPrimary: { backgroundColor: '#0F172A' },
  btnPrimaryText: { color: '#FFFFFF', fontWeight: '700', fontSize: 15 },
  btnSecondary: {
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#CBD5E1',
  },
  btnSecondaryText: { color: '#0F172A', fontWeight: '700', fontSize: 15 },
  testBox: {
    marginTop: 12,
    padding: 12,
    borderRadius: 10,
  },
  testText: { fontSize: 13, fontWeight: '600' },
  linkBtn: {
    alignSelf: 'flex-start',
    paddingVertical: 10,
    marginTop: 4,
  },
  linkText: { color: '#2563EB', fontSize: 13, fontWeight: '600' },
  divider: {
    height: 1,
    backgroundColor: '#E2E8F0',
    marginVertical: 16,
  },
  infoBox: {
    backgroundColor: '#FFFFFF',
    borderRadius: 12,
    padding: 14,
  },
  infoLabel: { color: '#94A3B8', fontSize: 11 },
  infoValue: {
    color: '#0F172A',
    fontSize: 13,
    fontWeight: '600',
    marginTop: 2,
  },
});
