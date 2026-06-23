import React, { useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, StyleSheet,
  ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView,
} from 'react-native';
import { theme } from '../theme';
import { checkHealth, fetchSkills } from '../api';

export default function SetupScreen({ initial, onSaved }) {
  const [serverUrl, setServerUrl] = useState(initial?.serverUrl || 'http://');
  const [token, setToken] = useState(initial?.token || '');
  const [status, setStatus] = useState(null);   // {ok, msg}
  const [busy, setBusy] = useState(false);

  async function testAndSave() {
    setBusy(true);
    setStatus(null);
    try {
      await checkHealth(serverUrl);
      const skills = await fetchSkills(serverUrl, token);
      setStatus({ ok: true, msg: `Connected — ${skills.length} skills live 🔥` });
      onSaved({ serverUrl: serverUrl.replace(/\/+$/, ''), token });
    } catch (e) {
      setStatus({ ok: false, msg: String(e.message || e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
        <Text style={styles.title}>Code-as-a-Chat</Text>
        <Text style={styles.subtitle}>Connect to your Mac</Text>

        <Text style={styles.label}>Server URL</Text>
        <TextInput
          style={styles.input}
          value={serverUrl}
          onChangeText={setServerUrl}
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          placeholder="https://your-mac.ts.net"
          placeholderTextColor={theme.textDim}
        />
        <Text style={styles.hint}>
          Tailscale: https://&lt;mac&gt;.ts.net  ·  Same Wi-Fi: http://192.168.x.x:8000
        </Text>

        <Text style={styles.label}>API Token</Text>
        <TextInput
          style={styles.input}
          value={token}
          onChangeText={setToken}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
          placeholder="from ~/.codeasachat/api_token"
          placeholderTextColor={theme.textDim}
        />

        <TouchableOpacity
          style={[styles.btn, busy && styles.btnDisabled]}
          onPress={testAndSave}
          disabled={busy}>
          {busy
            ? <ActivityIndicator color="#fff" />
            : <Text style={styles.btnText}>Connect</Text>}
        </TouchableOpacity>

        {status && (
          <Text style={[styles.status, { color: status.ok ? theme.ok : theme.danger }]}>
            {status.msg}
          </Text>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.bg },
  scroll: { padding: 24, paddingTop: 80 },
  title: { color: theme.text, fontSize: 28, fontWeight: '700' },
  subtitle: { color: theme.textDim, fontSize: 15, marginBottom: 28 },
  label: { color: theme.textDim, fontSize: 13, marginBottom: 6, marginTop: 16 },
  input: {
    backgroundColor: theme.inputBg, color: theme.text, borderRadius: 10,
    paddingHorizontal: 14, paddingVertical: 12, fontSize: 16,
    borderWidth: 1, borderColor: theme.border,
  },
  hint: { color: theme.textDim, fontSize: 11, marginTop: 6 },
  btn: {
    backgroundColor: theme.accent, borderRadius: 10, paddingVertical: 14,
    alignItems: 'center', marginTop: 28,
  },
  btnDisabled: { backgroundColor: theme.accentDim },
  btnText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  status: { marginTop: 18, fontSize: 14, textAlign: 'center' },
});
