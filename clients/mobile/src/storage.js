import AsyncStorage from '@react-native-async-storage/async-storage';

// Persisted connection config: where the Mac is and the API token.
const KEY = 'codeasachat.config.v1';

export async function loadConfig() {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export async function saveConfig(cfg) {
  await AsyncStorage.setItem(KEY, JSON.stringify(cfg));
}

export async function clearConfig() {
  await AsyncStorage.removeItem(KEY);
}

// A stable per-install session id so the server keeps conversation memory
// for this device (mirrors the bot's "tg:<chat>" scheme).
const SID_KEY = 'codeasachat.sid.v1';

export async function getSessionId() {
  let sid = await AsyncStorage.getItem(SID_KEY);
  if (!sid) {
    sid = 'app:' + Math.random().toString(36).slice(2, 10);
    await AsyncStorage.setItem(SID_KEY, sid);
  }
  return sid;
}
