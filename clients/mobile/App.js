import React, { useState, useEffect } from 'react';
import { View, ActivityIndicator, StyleSheet, StatusBar } from 'react-native';
import { theme } from './src/theme';
import { loadConfig, saveConfig } from './src/storage';
import SetupScreen from './src/screens/SetupScreen';
import ChatScreen from './src/screens/ChatScreen';

export default function App() {
  const [loading, setLoading] = useState(true);
  const [config, setConfig] = useState(null);
  const [forceSetup, setForceSetup] = useState(false);

  useEffect(() => {
    loadConfig().then((c) => { setConfig(c); setLoading(false); });
  }, []);

  async function handleSaved(cfg) {
    await saveConfig(cfg);
    setConfig(cfg);
    setForceSetup(false);
  }

  if (loading) {
    return (
      <View style={styles.center}>
        <StatusBar barStyle="light-content" />
        <ActivityIndicator color={theme.accent} size="large" />
      </View>
    );
  }

  const needsSetup = forceSetup || !config?.serverUrl || !config?.token;

  return (
    <View style={styles.root}>
      <StatusBar barStyle="light-content" backgroundColor={theme.card} />
      {needsSetup
        ? <SetupScreen initial={config} onSaved={handleSaved} />
        : <ChatScreen config={config} onOpenSettings={() => setForceSetup(true)} />}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.bg },
  center: { flex: 1, backgroundColor: theme.bg, alignItems: 'center', justifyContent: 'center' },
});
