// Persistent per-conversation chat state.
//
// This lives OUTSIDE the chat screen on purpose. Previously the messages and the
// in-flight turn lived in the screen's State, so navigating away destroyed them:
// a running turn's progress vanished and its reply "magically appeared" later,
// and a second message sent while busy was silently dropped. Now a controller
// owns the conversation — messages, the running turn, a visible queue, and your
// half-typed draft — so leaving and coming back shows the exact same state.

import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'api.dart';
import 'models.dart';
import 'state.dart';

/// Pulls "[image: /path]" markers out of an agent reply → (clean text, urls).
final _imgMarker = RegExp(r'\[image:\s*([^\]]+?)\s*\]');
(String, List<String>) splitImages(String raw, GajalaApi api) {
  final urls = <String>[];
  final clean = raw.replaceAllMapped(_imgMarker, (m) {
    urls.add(api.fileUrl(m.group(1)!.trim()));
    return '';
  }).trim();
  return (clean, urls);
}

/// Pulls a "[[move:dir]]" confirm-to-move marker out of a reply → (clean, dir).
final _moveMarker = RegExp(r'\[\[move:\s*([a-z0-9\-]+)\s*\]\]', caseSensitive: false);
(String, String?) splitMove(String raw) {
  String? target;
  final clean = raw.replaceAllMapped(_moveMarker, (m) {
    target = m.group(1);
    return '';
  }).trim();
  return (clean, target);
}

/// Pulls a "[[switch:name]]" marker out of a reply → (clean, project). The
/// agent emits it when it changed project mid-turn, so the app can move the
/// conversation to that project's thread.
final _switchMarker =
    RegExp(r'\[\[switch:\s*([^\]]+?)\s*\]\]', caseSensitive: false);
(String, String?) splitSwitch(String raw) {
  String? target;
  final clean = raw.replaceAllMapped(_switchMarker, (m) {
    target = m.group(1);
    return '';
  }).trim();
  return (clean, target);
}

/// Identifies one conversation: a skill tab, or a per-directory Gajala thread.
@immutable
class ChatKey {
  final String command;
  final String sid;
  const ChatKey(this.command, this.sid);
  @override
  bool operator ==(Object other) =>
      other is ChatKey && other.command == command && other.sid == sid;
  @override
  int get hashCode => Object.hash(command, sid);
}

@immutable
class ChatState {
  final List<ChatMessage> messages;
  final bool sending;
  final List<String> queued;   // typed while a turn was running
  final String draft;          // half-typed input, kept across navigation
  final bool loaded;
  final String? workspace;     // project the server reported after a turn
  const ChatState({
    this.messages = const [],
    this.sending = false,
    this.queued = const [],
    this.draft = '',
    this.loaded = false,
    this.workspace,
  });

  ChatState copyWith({
    List<ChatMessage>? messages,
    bool? sending,
    List<String>? queued,
    String? draft,
    bool? loaded,
    String? workspace,
  }) =>
      ChatState(
        messages: messages ?? this.messages,
        sending: sending ?? this.sending,
        queued: queued ?? this.queued,
        draft: draft ?? this.draft,
        loaded: loaded ?? this.loaded,
        workspace: workspace ?? this.workspace,
      );
}

class ChatController extends StateNotifier<ChatState> {
  final GajalaApi? _api;
  final ChatKey key;
  ChatController(this._api, this.key) : super(const ChatState());

  /// Load server history once per conversation (keeps an in-flight turn intact).
  Future<void> ensureLoaded({String? welcome}) async {
    if (state.loaded || state.sending) return;
    final api = _api;
    List<ChatMessage> loaded = const [];
    if (api != null) {
      try {
        final history = await api.chatHistory(key.sid);
        loaded = history.map((m) {
          if (m.role != 'bot') return m;
          final (clean, urls) = splitImages(m.text, api);
          if (urls.isEmpty) return m;
          return ChatMessage('bot', clean, remoteImages: urls);
        }).toList();
      } catch (_) {/* fall through to welcome */}
    }
    state = state.copyWith(
      loaded: true,
      messages: loaded.isNotEmpty
          ? loaded
          : [if (welcome != null) ChatMessage('bot', welcome)],
    );
  }

  void setDraft(String v) => state = state.copyWith(draft: v);

  void addSystemNote(String text) =>
      state = state.copyWith(messages: [...state.messages, ChatMessage('system', text)]);

  /// Send a message. If a turn is already running the message is QUEUED and
  /// shown as such, then sent automatically when the current turn finishes.
  Future<void> send(String text, {String? imagePath}) async {
    final t = text.trim();
    if (t.isEmpty && imagePath == null) return;
    if (state.sending) {
      state = state.copyWith(
        draft: '',
        queued: [...state.queued, t],
        messages: [...state.messages, ChatMessage('queued', t)],
      );
      return;
    }
    state = state.copyWith(draft: '');
    await _runTurn(t, imagePath);
    await _drainQueue();
  }

  Future<void> _drainQueue() async {
    while (state.queued.isNotEmpty && mounted) {
      final next = state.queued.first;
      final msgs = [...state.messages];
      final i = msgs.indexWhere((m) => m.role == 'queued' && m.text == next);
      if (i >= 0) msgs.removeAt(i);
      state = state.copyWith(queued: state.queued.sublist(1), messages: msgs);
      await _runTurn(next, null);
    }
  }

  Future<void> _runTurn(String text, String? imagePath) async {
    final api = _api;
    if (api == null) return;

    final seeded = [
      ...state.messages,
      ChatMessage('user', text.isEmpty ? '📷 Photo' : text, localImage: imagePath),
      ChatMessage('status', 'Gajala typing…'),
    ];
    final liveIdx = seeded.length - 1;   // queued bubbles append after this
    state = state.copyWith(messages: seeded, sending: true);

    void setLive(String s) {
      final m = [...state.messages];
      if (liveIdx < m.length && m[liveIdx].role == 'status') {
        m[liveIdx] = ChatMessage('status', s);
        state = state.copyWith(messages: m);
      }
    }

    /// The live bubble now renders the SAME trace widget the finished reply
    /// keeps, so what you watch is what you can reopen afterwards.
    void setLiveSteps(List<RunStep> steps, String? project) {
      final m = [...state.messages];
      if (liveIdx < m.length && m[liveIdx].role == 'status') {
        m[liveIdx] = ChatMessage('status', 'Gajala typing…',
            steps: steps, project: project);
        state = state.copyWith(messages: m);
      }
    }

    var replaced = false;
    void finish(ChatMessage msg) {
      final m = [...state.messages];
      if (liveIdx < m.length && m[liveIdx].role == 'status') {
        m[liveIdx] = msg;
      } else {
        m.add(msg);
      }
      state = state.copyWith(messages: m);
      replaced = true;
    }

    var prompt = text;
    // Steps arrive as two frames each: `step` when the tool starts, then
    // `step_result` with its outcome. Keyed by step number so the second frame
    // updates the row already on screen instead of appending a duplicate.
    final live = <int, RunStep>{};
    String? runId;
    String? project;

    void showSteps() {
      final ordered = live.keys.toList()..sort();
      setLiveSteps([for (final n in ordered) live[n]!], project);
    }

    try {
      if (imagePath != null) {
        setLive('Uploading image…');
        final bytes = await File(imagePath).readAsBytes();
        final serverPath =
            await api.uploadImage(bytes, imagePath.split('/').last);
        final marker = '[User sent an image, saved at: $serverPath]';
        prompt = prompt.isEmpty ? marker : '$marker\n$prompt';
      }

      await for (final ev in api.runStream(key.command, prompt, key.sid,
          notify: true, project: state.workspace)) {
        switch (ev['type']) {
          // Sent before any work starts, so a dropped stream can still fetch
          // the trace instead of leaving the user with nothing.
          case 'run':
            runId = ev['run_id']?.toString();
            project = ev['project']?.toString() ?? project;
            break;
          case 'step':
            // The stream opens with a bare {"type":"step","label":"Thinking…"}
            // to push bytes before the proxy's idle timeout. It has no step
            // number because it is not a tool call — show it as plain text.
            final n = (ev['n'] as num?)?.toInt();
            if (n == null) {
              final label = ev['label']?.toString() ?? '';
              if (label.isNotEmpty && live.isEmpty) setLive(label);
              break;
            }
            project = ev['project']?.toString() ?? project;
            live[n] = RunStep(
              idx: n,
              tool: ev['tool']?.toString() ?? ev['label']?.toString() ?? '',
              args: ev['args']?.toString() ?? '',
              result: '',
              workspace: project ?? '',
              ok: true,
              charged: true,
              durationMs: 0,
            );
            showSteps();
            break;
          case 'step_result':
            final n = (ev['n'] as num?)?.toInt() ?? live.length;
            final prev = live[n];
            live[n] = RunStep(
              idx: n,
              tool: ev['tool']?.toString() ?? prev?.tool ?? '',
              args: prev?.args ?? '',
              result: ev['summary']?.toString() ?? '',
              workspace: ev['project']?.toString() ?? prev?.workspace ?? '',
              ok: ev['ok'] != false,
              charged: ev['charged'] != false,
              durationMs: (ev['duration_ms'] as num?)?.toInt() ?? 0,
            );
            showSteps();
            break;
          case 'final':
            final ws = ev['workspace']?.toString();
            final (imgClean, urls) = splitImages(ev['result']?.toString() ?? '', api);
            final (moveClean, moveTo) = splitMove(imgClean);
            final (clean, switchedTo) = splitSwitch(moveClean);
            final ordered = live.keys.toList()..sort();
            finish(ChatMessage(
                'bot',
                clean.isEmpty && urls.isNotEmpty ? '' : (clean.isEmpty ? '(no result)' : clean),
                remoteImages: urls,
                moveTo: moveTo,
                runId: runId,
                steps: [for (final n in ordered) live[n]!],
                project: ws ?? project));
            // The agent may have switched project mid-turn; follow it so the
            // header and thread key land on the project the turn ended in.
            final landed = switchedTo ?? ws;
            if (landed != null && landed.isNotEmpty) {
              state = state.copyWith(workspace: landed);
            }
            break;
          case 'error':
            finish(ChatMessage('error', ev['message']?.toString() ?? 'Server error',
                runId: runId));
            break;
        }
      }
      if (!replaced) {
        setLive('Connection interrupted · still working…');
        finish(await _recoverReply(text, runId) ??
            ChatMessage('system',
                'Connection dropped mid-reply — it\'s still being written on the '
                'Mac. Reopen this chat in a moment to see it.',
                runId: runId));
      }
    } catch (e) {
      if (!replaced) {
        setLive('Connection interrupted · still working…');
        finish(await _recoverReply(text, runId) ??
            ChatMessage('error', friendlyError(e), runId: runId));
      }
    } finally {
      if (mounted) state = state.copyWith(sending: false);
    }
  }

  /// The live stream dropped before the final frame. The server still finishes
  /// the turn and persists the reply, so poll history until it lands.
  Future<ChatMessage?> _recoverReply(String userText, [String? runId]) async {
    final api = _api;
    final want = userText.trim();
    if (api == null || want.isEmpty) return null;
    for (var i = 0; i < 30 && mounted; i++) {
      try {
        final h = await api.chatHistory(key.sid);
        for (var j = h.length - 1; j >= 1; j--) {
          if (h[j].role == 'bot' &&
              h[j - 1].role == 'user' &&
              h[j - 1].text.trim() == want) {
            final (clean, urls) = splitImages(h[j].text, api);
            return ChatMessage('bot', clean.isEmpty ? h[j].text : clean,
                remoteImages: urls, runId: runId ?? h[j].runId);
          }
        }
      } catch (_) {/* keep polling */}
      await Future.delayed(const Duration(seconds: 4));
    }
    return null;
  }
}

/// One controller per conversation, kept alive for the app's lifetime so a
/// running turn (and your draft) survives navigating away and back.
final chatControllerProvider =
    StateNotifierProvider.family<ChatController, ChatState, ChatKey>((ref, key) {
  return ChatController(ref.watch(apiProvider), key);
});
