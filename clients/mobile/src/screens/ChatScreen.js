import React, { useState, useRef, useEffect, useCallback } from 'react';
import {
  View, Text, TextInput, TouchableOpacity, FlatList, StyleSheet,
  KeyboardAvoidingView, Platform, ActivityIndicator,
} from 'react-native';
import { theme } from '../theme';
import { runCommand } from '../api';
import { getSessionId } from '../storage';

let _id = 0;
const newMsg = (role, text) => ({ id: `${Date.now()}-${_id++}`, role, text });

export default function ChatScreen({ config, onOpenSettings }) {
  const [messages, setMessages] = useState([
    newMsg('bot', 'Em sangathi mava! Gajala ikkada 🔥\nCheppu — em kavali?'),
  ]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const sessionRef = useRef(null);
  const listRef = useRef(null);

  useEffect(() => { getSessionId().then((s) => (sessionRef.current = s)); }, []);

  const scrollEnd = useCallback(() => {
    requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
  }, []);

  async function send() {
    const text = input.trim();
    if (!text || sending) return;
    setInput('');
    setMessages((m) => [...m, newMsg('user', text)]);
    setSending(true);
    scrollEnd();

    // Explicit /command → that command; otherwise route through the shell agent.
    let command = 'shell';
    let prompt = text;
    if (text.startsWith('/')) {
      const sp = text.indexOf(' ');
      command = (sp === -1 ? text.slice(1) : text.slice(1, sp)).toLowerCase();
      prompt = sp === -1 ? '' : text.slice(sp + 1);
    }

    try {
      const result = await runCommand(
        config.serverUrl, config.token, command, prompt, sessionRef.current,
      );
      setMessages((m) => [...m, newMsg('bot', result)]);
    } catch (e) {
      setMessages((m) => [...m, newMsg('error', String(e.message || e))]);
    } finally {
      setSending(false);
      scrollEnd();
    }
  }

  const renderItem = ({ item }) => {
    const isUser = item.role === 'user';
    const isError = item.role === 'error';
    return (
      <View style={[styles.row, { justifyContent: isUser ? 'flex-end' : 'flex-start' }]}>
        <View style={[
          styles.bubble,
          isUser ? styles.userBubble : styles.botBubble,
          isError && styles.errorBubble,
        ]}>
          <Text style={[styles.bubbleText, isError && { color: theme.danger }]} selectable>
            {item.text}
          </Text>
        </View>
      </View>
    );
  };

  return (
    <KeyboardAvoidingView
      style={styles.root}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={Platform.OS === 'ios' ? 0 : 0}>
      <View style={styles.header}>
        <Text style={styles.headerTitle}>Gajala</Text>
        <TouchableOpacity onPress={onOpenSettings} hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}>
          <Text style={styles.gear}>⚙︎</Text>
        </TouchableOpacity>
      </View>

      <FlatList
        ref={listRef}
        data={messages}
        keyExtractor={(i) => i.id}
        renderItem={renderItem}
        contentContainerStyle={styles.list}
        onContentSizeChange={scrollEnd}
      />

      {sending && (
        <View style={styles.typing}>
          <ActivityIndicator size="small" color={theme.textDim} />
          <Text style={styles.typingText}>Gajala typing…</Text>
        </View>
      )}

      <View style={styles.inputBar}>
        <TextInput
          style={styles.input}
          value={input}
          onChangeText={setInput}
          placeholder="Message or /command…"
          placeholderTextColor={theme.textDim}
          multiline
          onSubmitEditing={send}
        />
        <TouchableOpacity
          style={[styles.sendBtn, (!input.trim() || sending) && styles.sendBtnOff]}
          onPress={send}
          disabled={!input.trim() || sending}>
          <Text style={styles.sendText}>➤</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: theme.bg },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingTop: 54, paddingBottom: 12, paddingHorizontal: 16,
    backgroundColor: theme.card, borderBottomWidth: 1, borderBottomColor: theme.border,
  },
  headerTitle: { color: theme.text, fontSize: 18, fontWeight: '700' },
  gear: { color: theme.textDim, fontSize: 22 },
  list: { padding: 12, paddingBottom: 8 },
  row: { marginVertical: 4, flexDirection: 'row' },
  bubble: { maxWidth: '82%', borderRadius: 14, paddingHorizontal: 13, paddingVertical: 9 },
  userBubble: { backgroundColor: theme.userBubble, borderBottomRightRadius: 4 },
  botBubble: { backgroundColor: theme.botBubble, borderBottomLeftRadius: 4 },
  errorBubble: { backgroundColor: theme.botBubble, borderWidth: 1, borderColor: theme.danger },
  bubbleText: { color: theme.text, fontSize: 15, lineHeight: 21 },
  typing: { flexDirection: 'row', alignItems: 'center', paddingHorizontal: 18, paddingBottom: 4 },
  typingText: { color: theme.textDim, fontSize: 12, marginLeft: 8 },
  inputBar: {
    flexDirection: 'row', alignItems: 'flex-end', padding: 8,
    backgroundColor: theme.card, borderTopWidth: 1, borderTopColor: theme.border,
  },
  input: {
    flex: 1, backgroundColor: theme.inputBg, color: theme.text, borderRadius: 20,
    paddingHorizontal: 16, paddingTop: 10, paddingBottom: 10, fontSize: 15, maxHeight: 120,
  },
  sendBtn: {
    marginLeft: 8, width: 44, height: 44, borderRadius: 22, backgroundColor: theme.accent,
    alignItems: 'center', justifyContent: 'center',
  },
  sendBtnOff: { backgroundColor: theme.accentDim, opacity: 0.6 },
  sendText: { color: '#fff', fontSize: 18 },
});
