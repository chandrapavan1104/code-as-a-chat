// Persistent per-conversation chat state.
//
// This lives OUTSIDE the chat screen on purpose. Previously the messages and the
// in-flight turn lived in the screen's State, so navigating away destroyed them:
// a running turn's progress vanished and its reply "magically appeared" later,
// and a second message sent while busy was silently dropped. Now a controller
// owns the conversation — messages, the running turn, a visible queue, and your
// half-typed draft — so leaving and coming back shows the exact same state.

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:math';
import 'package:flutter/foundation.dart';
import 'package:dio/dio.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'api.dart';
import 'models.dart';
import 'outbox.dart';
import 'device_actions.dart';
import 'phone_abilities.dart';
import 'state.dart';
import 'voice_journal.dart';

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

/// Pulls an "[[ask:{…}]]" question card out of a reply → (clean text, card).
/// The server already validated it; anything unparsable is just dropped.
final _askMarker = RegExp(r'\[\[ask:(\{.*?\})\]\]', dotAll: true);
(String, AskCard?) splitAsk(String raw) {
  AskCard? card;
  final clean = raw.replaceAllMapped(_askMarker, (m) {
    try {
      final j = jsonDecode(m.group(1)!) as Map<String, dynamic>;
      final options = [
        for (final o in (j['options'] as List? ?? const [])) '$o',
      ];
      if ((j['question'] ?? '').toString().isNotEmpty && options.length >= 2) {
        card = AskCard(
          j['question'].toString(),
          options,
          multi: j['multi'] == true,
        );
      }
    } catch (_) {}
    return '';
  }).trim();
  return (clean, card);
}

/// Pulls a "[[move:dir]]" confirm-to-move marker out of a reply → (clean, dir).
final _moveMarker = RegExp(
  r'\[\[move:\s*([a-z0-9\-]+)\s*\]\]',
  caseSensitive: false,
);
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
final _switchMarker = RegExp(
  r'\[\[switch:\s*([^\]]+?)\s*\]\]',
  caseSensitive: false,
);
(String, String?) splitSwitch(String raw) {
  String? target;
  final clean = raw.replaceAllMapped(_switchMarker, (m) {
    target = m.group(1);
    return '';
  }).trim();
  return (clean, target);
}

/// The Gajala (shell) thread id for a project directory. Must match the
/// server's `workspace.slug()` — the suffix is how it recovers the project.
String shellSessionId(String installId, String? dir) {
  if (dir == null || dir.isEmpty) return installId;
  final slug = dir.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), '-');
  return '$installId::$slug';
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

/// A message waiting behind a running turn. Keep the attachment with its text
/// so an image sent while the assistant is busy is never silently discarded.
@immutable
class QueuedMessage {
  final String text;
  final String? imagePath;
  final String? continuationTaskId;
  final String? requestId; // its outbox entry, so a restart can still send it
  const QueuedMessage(
    this.text, {
    this.imagePath,
    this.continuationTaskId,
    this.requestId,
  });
}

/// Failures where the request may never have reached the Mac. Anything else
/// (an HTTP error status) means the server saw it, so it must not be resent.
bool isConnectivityError(Object e) {
  if (e is SocketException || e is HttpException || e is TimeoutException) {
    return true;
  }
  if (e is DioException) {
    return const {
      DioExceptionType.connectionError,
      DioExceptionType.connectionTimeout,
      DioExceptionType.sendTimeout,
      DioExceptionType.receiveTimeout,
      DioExceptionType.unknown,
    }.contains(e.type);
  }
  return false;
}

@immutable
class ChatState {
  final List<ChatMessage> messages;
  final bool sending;
  final List<QueuedMessage> queued; // typed while a turn was running
  final String draft; // half-typed input, kept across navigation
  final bool loaded;

  /// ONE-SHOT signal: "the last turn ended in this project". The screen follows
  /// it and then clears it via [ChatController.consumeWorkspace].
  ///
  /// It must not persist. Controllers are kept alive per thread for the app's
  /// lifetime, so a value left here is re-applied every rebuild — and because
  /// following it swaps which controller the screen watches, two threads each
  /// holding a stale value point at each other and the screen ping-pongs
  /// between them forever (visibly: switching to a project fails and the screen
  /// blinks).
  final String? workspace;
  final AssistantWork? work;
  final List<AssistantWork> backgroundResearch;
  const ChatState({
    this.messages = const [],
    this.sending = false,
    this.queued = const [],
    this.draft = '',
    this.loaded = false,
    this.workspace,
    this.work,
    this.backgroundResearch = const [],
  });

  ChatState copyWith({
    List<ChatMessage>? messages,
    bool? sending,
    List<QueuedMessage>? queued,
    String? draft,
    bool? loaded,
    String? workspace,
    // copyWith cannot express "set this back to null", and workspace is a
    // one-shot that MUST be clearable. An explicit flag keeps the rest of the
    // call sites unchanged.
    bool clearWorkspace = false,
    AssistantWork? work,
    List<AssistantWork>? backgroundResearch,
    bool clearWork = false,
  }) => ChatState(
    messages: messages ?? this.messages,
    sending: sending ?? this.sending,
    queued: queued ?? this.queued,
    draft: draft ?? this.draft,
    loaded: loaded ?? this.loaded,
    workspace: clearWorkspace ? null : (workspace ?? this.workspace),
    work: clearWork ? null : (work ?? this.work),
    backgroundResearch: backgroundResearch ?? this.backgroundResearch,
  );
}

typedef PhoneHandler =
    Future<Map<String, dynamic>> Function(
      String command,
      Map<String, dynamic> args,
      GajalaApi api,
    );

/// `action.*` = do something on the phone; anything else = read from it.
Future<Map<String, dynamic>> _defaultPhone(
  String command,
  Map<String, dynamic> args,
  GajalaApi api,
) => command.startsWith('action.')
    ? DeviceActions.instance.run(command, args)
    : PhoneAbilities.instance.handle(command, args, api);

class ChatController extends StateNotifier<ChatState> {
  final GajalaApi? _api;
  final ChatKey key;
  final Outbox _outbox;
  final VoiceJournal _voiceJournal;
  final PhoneHandler _phone;
  // Request ids queued in memory or currently being sent; a replay skips them.
  final Set<String> _active = {};
  final Set<String> _syncingVoice = {};
  bool _replaying = false;
  bool _refreshingResearch = false;
  ChatController(
    this._api,
    this.key, {
    Outbox? outbox,
    PhoneHandler? phone,
    VoiceJournal? voiceJournal,
  }) : _outbox = outbox ?? Outbox.instance,
       _voiceJournal = voiceJournal ?? VoiceJournal.instance,
       _phone = phone ?? _defaultPhone,
       super(const ChatState());

  /// The agent asked this phone for something (location, calendar…). Answer
  /// without blocking the stream, and say in the chat what was shared.
  Future<void> _answerPhone(
    GajalaApi api,
    Map<String, dynamic> ev, [
    PhoneHandler? turnPhone,
  ]) async {
    final id = ev['id']?.toString();
    final command = ev['command']?.toString() ?? '';
    if (id == null) return;
    final args = Map<String, dynamic>.from((ev['args'] as Map?) ?? const {});
    final result = await (turnPhone ?? _phone)(command, args, api);
    final ability = PhoneAbilities.instance.byId(command);
    if (mounted) {
      final done = (result['data'] as Map?)?['done']?.toString();
      final note = ChatMessage(
        'system',
        result['ok'] == true
            ? (done != null
                  ? '📱 $done'
                  : command == 'action.notifications'
                  ? '🔔 Read your notifications'
                  : ability?.chatNote ?? 'Shared $command with Gajala')
            : command.startsWith('action.')
            ? '📱 Could not do that on the phone: ${result['error']}'
            : 'Gajala asked for ${ability?.title.toLowerCase() ?? command}: '
                  '${result['error']}',
      );
      // Above the live bubble, so it reads question → what was shared → reply.
      final m = [...state.messages];
      final live = m.lastIndexWhere((x) => x.role == 'status');
      live >= 0 ? m.insert(live, note) : m.add(note);
      state = state.copyWith(messages: m);
    }
    try {
      await api.phoneResult(id, result);
    } catch (_) {
      /* the turn timed out or ended; nothing is waiting */
    }
  }

  /// Acknowledge the one-shot workspace signal. The screen calls this once it
  /// has acted on it (or decided not to), so build() stops re-scheduling.
  void consumeWorkspace() {
    if (state.workspace == null) return;
    state = state.copyWith(clearWorkspace: true);
  }

  /// Load server history once per conversation (keeps an in-flight turn intact).
  Future<void> ensureLoaded({String? welcome}) async {
    if (state.loaded || state.sending) return;
    final api = _api;
    List<ChatMessage> loaded = const [];
    AssistantWork? restoredWork;
    var historyLoaded = false;
    if (api != null) {
      try {
        final history = await api.chatHistory(key.sid);
        historyLoaded = true;
        loaded = history.map((m) {
          if (m.role != 'bot') return m;
          final (imgClean, urls) = splitImages(m.text, api);
          final (clean, ask) = splitAsk(imgClean);
          if (urls.isEmpty && ask == null) return m;
          return ChatMessage(
            'bot',
            clean,
            remoteImages: urls,
            ask: ask,
            runId: m.runId,
            localRequestId: m.localRequestId,
          );
        }).toList();
      } catch (_) {
        /* fall through to welcome */
      }
      if (key.command == 'shell') {
        try {
          for (final item in await api.assistantWork(key.sid)) {
            if (item.isActive && item.command != 'research') {
              restoredWork = item;
              break;
            }
          }
        } catch (_) {
          /* older servers have no durable-work endpoint */
        }
      }
    }
    final archivedIds = loaded
        .map((m) => m.localRequestId)
        .whereType<String>()
        .toSet();
    for (final id in archivedIds) {
      await _voiceJournal.markSynced(id);
    }
    final localTurns = await _restoreLocalVoiceTurns();
    final localMessages = [
      for (final turn in localTurns)
        if (!historyLoaded || !turn.synced)
          for (final message in turn.messages)
            ChatMessage(
              message.role == 'user' ? 'user' : 'bot',
              message.content,
              localRequestId: turn.id,
            ),
    ];
    // A local voice callback can append while network history is loading. Keep
    // anything added since this load began rather than replacing live state.
    final liveMessages = state.messages;
    final restoredMessages = [...loaded, ...localMessages];
    final mergedMessages =
        _containsMessageSequence(restoredMessages, liveMessages)
        ? restoredMessages
        : [...restoredMessages, ...liveMessages];
    state = state.copyWith(
      loaded: true,
      work: restoredWork,
      messages: mergedMessages.isNotEmpty
          ? mergedMessages
          : [if (welcome != null) ChatMessage('bot', welcome)],
    );
    await _showUnsent();
    unawaited(replayOutbox());
  }

  bool _containsMessageSequence(
    List<ChatMessage> haystack,
    List<ChatMessage> needle,
  ) {
    if (needle.isEmpty) return true;
    for (var start = 0; start + needle.length <= haystack.length; start++) {
      var matches = true;
      for (var offset = 0; offset < needle.length; offset++) {
        final left = haystack[start + offset];
        final right = needle[offset];
        if (left.role != right.role || left.text != right.text) {
          matches = false;
          break;
        }
      }
      if (matches) return true;
    }
    return false;
  }

  Future<List<VoiceJournalTurn>> _restoreLocalVoiceTurns() async {
    final turns = await _voiceJournal.load(sessionId: key.sid);
    for (final turn in turns.where(
      (turn) => !turn.completed && !_voiceJournal.isActive(turn.id),
    )) {
      await _voiceJournal.append(
        turn.id,
        'assistant',
        'This phone voice conversation was interrupted before it finished.',
      );
      await _voiceJournal.finish(turn.id);
    }
    final restored = await _voiceJournal.load(sessionId: key.sid);
    for (final turn in restored.where(
      (turn) => turn.completed && !turn.synced,
    )) {
      unawaited(_syncLocalVoiceTurn(turn));
    }
    return restored;
  }

  /// Starts a durable, phone-only voice transcript and immediately shows the
  /// user message in this conversation. It never calls /run.
  Future<String> beginLocalVoiceTurn(
    String text, {
    String role = 'user',
  }) async {
    if (role != 'user' && role != 'assistant') {
      throw ArgumentError.value(role, 'role', 'must be user or assistant');
    }
    final id = _requestId();
    await _voiceJournal.begin(id, key.sid, text, role: role);
    if (mounted) {
      state = state.copyWith(
        messages: [
          ...state.messages,
          ChatMessage(
            role == 'user' ? 'user' : 'bot',
            text,
            localRequestId: id,
          ),
        ],
      );
    }
    return id;
  }

  /// Appends one message after it is safely on disk. Voice uses `assistant`;
  /// chat renders that server-compatible role as a normal bot bubble.
  Future<void> appendLocalVoiceMessage(
    String id,
    String role,
    String text,
  ) async {
    if (role != 'user' && role != 'assistant') {
      throw ArgumentError.value(role, 'role', 'must be user or assistant');
    }
    await _voiceJournal.append(id, role, text);
    if (mounted) {
      state = state.copyWith(
        messages: [
          ...state.messages,
          ChatMessage(
            role == 'user' ? 'user' : 'bot',
            text,
            localRequestId: id,
          ),
        ],
      );
    }
  }

  /// Completes the local record before returning. Network archival runs in the
  /// background so dialing, media, and other phone actions are never delayed.
  Future<void> finishLocalVoiceTurn(String id) async {
    await _voiceJournal.finish(id);
    final turn = (await _voiceJournal.load(
      sessionId: key.sid,
    )).where((candidate) => candidate.id == id).firstOrNull;
    if (turn != null) unawaited(_syncLocalVoiceTurn(turn));
  }

  Future<void> _syncLocalVoiceTurn(VoiceJournalTurn turn) async {
    final api = _api;
    if (api == null ||
        turn.synced ||
        !turn.completed ||
        !_syncingVoice.add(turn.id)) {
      return;
    }
    try {
      await api.storeLocalChatTurn(turn.sessionId, turn.id, [
        for (final message in turn.messages)
          message.toJson().cast<String, String>(),
      ]);
      await _voiceJournal.markSynced(turn.id);
    } catch (_) {
      // The retained journal is the retry queue; replayOutbox/ensureLoaded retry.
    } finally {
      _syncingVoice.remove(turn.id);
    }
  }

  /// Unsent messages from an earlier app session, shown as waiting bubbles.
  Future<void> _showUnsent() async {
    final (:pending, :expired) = await _outbox.load();
    final mine = pending.where(_isMine).toList();
    final lost = expired.where(_isMine).length;
    if (mine.isEmpty && lost == 0) return;
    state = state.copyWith(
      messages: [
        ...state.messages,
        for (final e in mine)
          ChatMessage(
            'outbox',
            e.text.isEmpty ? '📷 Photo' : e.text,
            localImage: e.imagePath,
          ),
        if (lost > 0)
          ChatMessage(
            'system',
            '$lost unsent message${lost == 1 ? '' : 's'} older than 48 hours '
                'could not be delivered and were dropped.',
          ),
      ],
    );
  }

  bool _isMine(OutboxEntry e) => e.command == key.command && e.sid == key.sid;

  /// Send whatever is still waiting in the outbox for this conversation,
  /// oldest first, stopping at the first one that still cannot get through.
  Future<void> replayOutbox() async {
    if (_replaying || state.sending || _api == null || !mounted) return;
    _replaying = true;
    try {
      for (final turn in await _voiceJournal.load(sessionId: key.sid)) {
        if (turn.completed && !turn.synced) await _syncLocalVoiceTurn(turn);
      }
      final (:pending, expired: _) = await _outbox.load();
      for (final e in pending.where(_isMine)) {
        if (!mounted || state.sending) break;
        if (_active.contains(e.requestId)) continue;
        final delivered = await _runTurn(
          e.text,
          e.imagePath,
          requestId: e.requestId,
          forcedContinuation: e.continuationTaskId,
          project: e.project,
          replay: true,
        );
        if (!delivered) break;
      }
    } finally {
      _replaying = false;
    }
    await _drainQueue();
  }

  Future<OutboxEntry?> _persist(
    String text,
    String? imagePath, {
    required String requestId,
    String? continuationTaskId,
  }) async {
    try {
      return await _outbox.add(
        OutboxEntry(
          requestId: requestId,
          command: key.command,
          sid: key.sid,
          text: text,
          imagePath: imagePath,
          project: state.workspace,
          continuationTaskId: continuationTaskId,
          createdAt: DateTime.now(),
        ),
      );
    } on OutboxFull catch (e) {
      addSystemNote(e.toString());
      return null;
    }
  }

  void setDraft(String v) => state = state.copyWith(draft: v);

  void addSystemNote(String text) => state = state.copyWith(
    messages: [...state.messages, ChatMessage('system', text)],
  );

  String _requestId() =>
      '${key.sid}:${DateTime.now().microsecondsSinceEpoch}:'
      '${Random.secure().nextInt(1 << 32)}';

  /// Background jobs keep running independently of this screen and /run stream.
  Future<void> refreshBackgroundResearch() async {
    final api = _api;
    if (api == null ||
        key.command != 'shell' ||
        !mounted ||
        state.sending ||
        _refreshingResearch)
      return;
    _refreshingResearch = true;
    try {
      final jobs = (await api.assistantWork(
        key.sid,
      )).where((w) => w.command == 'research').toList();
      if (!mounted || state.sending) return;
      state = state.copyWith(
        backgroundResearch: jobs
            .where(
              (w) => const {
                'accepted',
                'working',
                'recovering',
                'waiting_for_user',
              }.contains(w.status),
            )
            .toList(),
      );
      if (jobs.isEmpty) return;
      final history = await api.chatHistory(key.sid, limit: 200);
      if (!mounted || state.sending) return;
      final seen = state.messages
          .map((m) => m.localRequestId)
          .whereType<String>()
          .toSet();
      final fresh = history
          .where(
            (m) =>
                m.localRequestId?.startsWith('research-result:') == true &&
                !seen.contains(m.localRequestId),
          )
          .toList();
      if (fresh.isNotEmpty)
        state = state.copyWith(messages: [...state.messages, ...fresh]);
    } catch (_) {
      // A disconnected phone does not stop the server job; retry on next poll.
    } finally {
      _refreshingResearch = false;
    }
  }

  Future<void> cancelBackgroundResearch(String id) async {
    final api = _api;
    if (api == null) return;
    await api.cancelResearch(id);
    await refreshBackgroundResearch();
  }

  Future<void> stopWork() async {
    final api = _api;
    final work = state.work;
    if (api == null || work == null || !work.isActive) return;
    try {
      state = state.copyWith(work: await api.stopWork(work.id), sending: false);
    } catch (_) {
      /* retain the server-authoritative running state */
    }
  }

  /// Send a message. If a turn is already running the message is QUEUED and
  /// shown as such, then sent automatically when the current turn finishes.
  Future<void> send(
    String text, {
    String? imagePath,
    PhoneHandler? phone,
  }) async {
    final t = text.trim();
    if (t.isEmpty && imagePath == null) return;
    final id = _requestId();
    _active.add(id);
    if (state.sending) {
      final prior = state.work;
      final queued = QueuedMessage(t, imagePath: imagePath, requestId: id);
      state = state.copyWith(
        draft: '',
        queued: [...state.queued, queued],
        messages: [
          ...state.messages,
          ChatMessage(
            'queued',
            t.isEmpty ? '📷 Photo' : t,
            localImage: imagePath,
          ),
        ],
      );
      // Shown at once, then made durable before anything is sent: a restart
      // while this waits must not lose it.
      if (await _persist(t, imagePath, requestId: id) == null) {
        _active.remove(id);
        state = state.copyWith(
          queued: [...state.queued]..remove(queued),
          messages: [...state.messages]
            ..removeWhere(
              (m) =>
                  m.role == 'queued' && m.text == (t.isEmpty ? '📷 Photo' : t),
            ),
        );
        return;
      }
      // Put the message on the durable in-memory queue immediately. The
      // advisory classifier may be slow or unavailable, but must never make
      // an image/text submission disappear while the current turn completes.
      if (prior?.isContinuable == true) {
        final continuation = await _classifyContinuation(t, prior!);
        if (continuation != null && mounted) {
          final q = [...state.queued];
          final index = q.indexOf(queued);
          if (index >= 0) {
            q[index] = QueuedMessage(
              t,
              imagePath: queued.imagePath,
              continuationTaskId: continuation,
              requestId: id,
            );
            state = state.copyWith(queued: q);
          }
        }
      }
      return;
    }
    if (await _persist(t, imagePath, requestId: id) == null) {
      _active.remove(id);
      return;
    }
    state = state.copyWith(draft: '');
    await _runTurn(t, imagePath, requestId: id, phone: phone);
    await _drainQueue();
  }

  Future<void> _drainQueue() async {
    while (state.queued.isNotEmpty && mounted) {
      final next = state.queued.first;
      final msgs = [...state.messages];
      final display = next.text.isEmpty ? '📷 Photo' : next.text;
      final i = msgs.indexWhere((m) => m.role == 'queued' && m.text == display);
      if (i >= 0) msgs.removeAt(i);
      state = state.copyWith(queued: state.queued.sublist(1), messages: msgs);
      final delivered = await _runTurn(
        next.text,
        next.imagePath,
        forcedContinuation: next.continuationTaskId,
        requestId: next.requestId,
      );
      if (!delivered) {
        // Offline: everything behind it stays in the outbox for the replay.
        for (final rest in state.queued) {
          if (rest.requestId != null) _active.remove(rest.requestId);
        }
        state = state.copyWith(
          queued: const [],
          messages: [
            for (final m in state.messages)
              m.role == 'queued'
                  ? ChatMessage('outbox', m.text, localImage: m.localImage)
                  : m,
          ],
        );
        break;
      }
    }
  }

  Future<String?> _classifyContinuation(
    String text,
    AssistantWork prior,
  ) async {
    final api = _api;
    if (api == null) return null;
    try {
      final relation = await api.classifyWork(prior.id, text);
      if (const {'correction', 'retry', 'continuation'}.contains(relation)) {
        return prior.id;
      }
    } catch (_) {
      /* unavailable classification leaves this as a new task */
    }
    return null;
  }

  /// Returns false only when the message could not reach the Mac and is still
  /// waiting in the outbox.
  Future<bool> _runTurn(
    String text,
    String? imagePath, {
    String? forcedContinuation,
    String? requestId,
    String? project,
    bool replay = false,
    PhoneHandler? phone,
  }) async {
    final api = _api;
    if (api == null) return true;
    final id = requestId ?? _requestId();
    _active.add(id);
    var delivered = false;
    Future<void> markDelivered() async {
      if (delivered) return;
      delivered = true;
      await _outbox.remove(id);
    }

    final display = text.isEmpty ? '📷 Photo' : text;
    final base = [...state.messages];
    if (replay) {
      final i = base.indexWhere((m) => m.role == 'outbox' && m.text == display);
      if (i >= 0) base.removeAt(i);
    }
    final seeded = [
      ...base,
      ChatMessage(
        'user',
        text.isEmpty ? '📷 Photo' : text,
        localImage: imagePath,
      ),
      ChatMessage('status', 'Gajala typing…'),
    ];
    // Located on every use, not remembered: notes (e.g. "shared your
    // location") are inserted above it mid-turn and shift its position.
    int liveIndex(List<ChatMessage> m) =>
        m.lastIndexWhere((x) => x.role == 'status');
    state = state.copyWith(messages: seeded, sending: true);

    void setLive(String s) {
      final m = [...state.messages];
      final liveIdx = liveIndex(m);
      if (liveIdx >= 0) {
        m[liveIdx] = ChatMessage('status', s);
        state = state.copyWith(messages: m);
      }
    }

    /// The live bubble now renders the SAME trace widget the finished reply
    /// keeps, so what you watch is what you can reopen afterwards.
    void setLiveSteps(List<RunStep> steps, String? project) {
      final m = [...state.messages];
      final liveIdx = liveIndex(m);
      if (liveIdx >= 0) {
        m[liveIdx] = ChatMessage(
          'status',
          'Gajala typing…',
          steps: steps,
          project: project,
        );
        state = state.copyWith(messages: m);
      }
    }

    var replaced = false;
    // Not delivered: the message becomes a single "waiting" bubble in place of
    // its sent bubble + live status, so a later replay doesn't show it twice.
    bool keepForLater() {
      final m = [...state.messages];
      final liveIdx = liveIndex(m);
      if (liveIdx >= 0) m.removeAt(liveIdx);
      final userIdx = m.lastIndexWhere(
        (x) => x.role == 'user' && x.text == display,
      );
      final waiting = ChatMessage('outbox', display, localImage: imagePath);
      if (userIdx >= 0 && userIdx < m.length && m[userIdx].role == 'user') {
        m[userIdx] = waiting;
      } else {
        m.add(waiting);
      }
      state = state.copyWith(messages: m);
      replaced = true;
      return false;
    }

    void finish(ChatMessage msg) {
      final m = [...state.messages];
      final liveIdx = liveIndex(m);
      if (liveIdx >= 0) {
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
    final String? sentProject = project ?? state.workspace;
    project = null;
    String? continuing = forcedContinuation;
    final priorWork = state.work;
    if (continuing == null && priorWork?.isContinuable == true) {
      continuing = await _classifyContinuation(text, priorWork!);
    }

    void showSteps() {
      final ordered = live.keys.toList()..sort();
      setLiveSteps([for (final n in ordered) live[n]!], project);
    }

    var requestStarted = false;
    try {
      if (imagePath != null) {
        setLive('Uploading image…');
        final bytes = await File(imagePath).readAsBytes();
        final serverPath = await api.uploadImage(
          bytes,
          imagePath.split('/').last,
        );
        final marker = '[User sent an image, saved at: $serverPath]';
        prompt = prompt.isEmpty ? marker : '$marker\n$prompt';
      }

      requestStarted = true;
      await for (final ev in api.runStream(
        key.command,
        prompt,
        key.sid,
        notify: true,
        project: sentProject,
        requestId: id,
        continueTaskId: continuing,
      )) {
        // Any frame proves the Mac has the request; it is no longer ours to resend.
        await markDelivered();
        switch (ev['type']) {
          case 'phone_request':
            unawaited(_answerPhone(api, ev, phone));
            break;
          case 'work':
            final raw = ev['work'];
            if (raw is Map) {
              state = state.copyWith(
                work: AssistantWork.fromJson(Map<String, dynamic>.from(raw)),
              );
            }
            break;
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
            final rawWork = ev['work'];
            if (rawWork is Map) {
              state = state.copyWith(
                work: AssistantWork.fromJson(
                  Map<String, dynamic>.from(rawWork),
                ),
              );
            }
            final ws = ev['workspace']?.toString();
            final (imgClean, urls) = splitImages(
              ev['result']?.toString() ?? '',
              api,
            );
            final (moveClean, moveTo) = splitMove(imgClean);
            final (switchClean, switchedTo) = splitSwitch(moveClean);
            final (clean, ask) = splitAsk(switchClean);
            final ordered = live.keys.toList()..sort();
            finish(
              ChatMessage(
                'bot',
                clean.isEmpty && urls.isNotEmpty
                    ? ''
                    : (clean.isEmpty ? '(no result)' : clean),
                ask: ask,
                remoteImages: urls,
                moveTo: moveTo,
                runId: runId,
                steps: [for (final n in ordered) live[n]!],
                project: ws ?? project,
              ),
            );
            // The agent may have switched project mid-turn; follow it so the
            // header and thread key land on the project the turn ended in.
            final landed = switchedTo ?? ws;
            if (landed != null && landed.isNotEmpty) {
              state = state.copyWith(workspace: landed);
            }
            break;
          case 'error':
            finish(
              ChatMessage(
                'error',
                ev['message']?.toString() ?? 'Server error',
                runId: runId,
              ),
            );
            break;
        }
      }
      if (!delivered) {
        // The stream closed without a single frame: treat as not sent.
        return keepForLater();
      }
      if (!replaced) {
        setLive('Connection interrupted · still working…');
        finish(
          await _recoverReply(text, runId) ??
              ChatMessage(
                'system',
                'Connection dropped mid-reply — it\'s still being written on the '
                    'Mac. Reopen this chat in a moment to see it.',
                runId: runId,
              ),
        );
      }
    } catch (e) {
      if (!delivered && isConnectivityError(e)) {
        // Never reached the Mac: keep it in the outbox and say so plainly.
        return keepForLater();
      }
      if (!delivered)
        await markDelivered(); // the server answered; don't resend
      if (!replaced) {
        if (!requestStarted) {
          finish(
            ChatMessage('error', 'Image upload failed. ${friendlyError(e)}'),
          );
          return true;
        }
        setLive('Connection interrupted · still working…');
        finish(
          await _recoverReply(text, runId) ??
              ChatMessage('error', friendlyError(e), runId: runId),
        );
      }
    } finally {
      _active.remove(id);
      if (mounted) state = state.copyWith(sending: false);
    }
    return true;
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
            return ChatMessage(
              'bot',
              clean.isEmpty ? h[j].text : clean,
              remoteImages: urls,
              runId: runId ?? h[j].runId,
            );
          }
        }
      } catch (_) {
        /* keep polling */
      }
      await Future.delayed(const Duration(seconds: 4));
    }
    return null;
  }
}

/// One controller per conversation, kept alive for the app's lifetime so a
/// running turn (and your draft) survives navigating away and back.
final chatControllerProvider =
    StateNotifierProvider.family<ChatController, ChatState, ChatKey>((
      ref,
      key,
    ) {
      return ChatController(ref.watch(apiProvider), key);
    });
