import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/chat_controller.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/core/outbox.dart';
import 'package:gajala/core/voice_journal.dart';

const _key = ChatKey('shell', 'app:voice::general');

class JournalApi extends GajalaApi {
  bool online = false;
  bool historyFails = true;
  int executions = 0;
  List<ChatMessage> history = [];
  final List<String> storedIds = [];
  final List<List<Map<String, String>>> storedMessages = [];

  JournalApi() : super('http://127.0.0.1:1', 'test');

  @override
  Future<List<ChatMessage>> chatHistory(
    String sessionId, {
    int limit = 50,
  }) async {
    if (historyFails) throw const SocketException('offline');
    return history;
  }

  @override
  Future<List<AssistantWork>> assistantWork(String sessionId) async => [];

  @override
  Future<void> storeLocalChatTurn(
    String sessionId,
    String requestId,
    List<Map<String, String>> messages,
  ) async {
    if (!online) throw const SocketException('offline');
    storedIds.add(requestId);
    storedMessages.add(messages);
  }

  @override
  Stream<Map<String, dynamic>> runStream(
    String command,
    String prompt,
    String sessionId, {
    bool notify = false,
    String? project,
    String? requestId,
    String? continueTaskId,
  }) async* {
    executions++;
    yield {'type': 'final', 'result': 'unexpected'};
  }
}

void main() {
  late Directory dir;
  late VoiceJournal journal;
  late Outbox outbox;

  setUp(() async {
    dir = await Directory.systemTemp.createTemp('gajala-voice-journal');
    journal = VoiceJournal(dir: () async => dir);
    outbox = Outbox(dir: () async => dir);
  });

  tearDown(() async {
    await dir.delete(recursive: true);
  });

  test(
    'offline voice transcript survives reload and is never executed',
    () async {
      final api = JournalApi();
      final first = ChatController(
        api,
        _key,
        outbox: outbox,
        voiceJournal: journal,
      );
      final id = await first.beginLocalVoiceTurn('play No Surprises');
      await first.appendLocalVoiceMessage(
        id,
        'assistant',
        'Opening YouTube Music.',
      );
      await first.finishLocalVoiceTurn(id);

      final reopened = ChatController(
        api,
        _key,
        outbox: outbox,
        voiceJournal: VoiceJournal(dir: () async => dir),
      );
      await reopened.ensureLoaded();

      expect(reopened.state.messages.map((message) => message.text), [
        'play No Surprises',
        'Opening YouTube Music.',
      ]);
      expect(api.executions, 0);
      final saved = await journal.load(sessionId: _key.sid);
      expect(saved.single.completed, isTrue);
      expect(saved.single.synced, isFalse);
    },
  );

  test('reconnect retries one idempotent local-turn request', () async {
    final api = JournalApi();
    final chat = ChatController(
      api,
      _key,
      outbox: outbox,
      voiceJournal: journal,
    );
    final id = await chat.beginLocalVoiceTurn('call PSS');
    await chat.appendLocalVoiceMessage(id, 'assistant', 'Calling PSS.');
    await chat.finishLocalVoiceTurn(id); // first background attempt is offline
    await Future<void>.delayed(const Duration(milliseconds: 20));

    api.online = true;
    await chat.replayOutbox();
    await chat.replayOutbox();

    expect(api.storedIds, [id]);
    expect(api.storedMessages.single, [
      {'role': 'user', 'content': 'call PSS'},
      {'role': 'assistant', 'content': 'Calling PSS.'},
    ]);
    expect((await journal.load()).single.synced, isTrue);
    expect(api.executions, 0);
  });

  test(
    'unfinished turn becomes a visible interrupted transcript on reload',
    () async {
      await journal.begin('voice-1', _key.sid, 'set an alarm');

      final chat = ChatController(
        JournalApi(),
        _key,
        outbox: outbox,
        voiceJournal: VoiceJournal(dir: () async => dir),
      );
      await chat.ensureLoaded();

      expect(chat.state.messages.map((message) => message.text), [
        'set an alarm',
        'This phone voice conversation was interrupted before it finished.',
      ]);
      final restored = (await journal.load()).single;
      expect(restored.completed, isTrue);
      expect(restored.synced, isFalse);
    },
  );
  test('history receipt prevents duplicate transcript after lost acknowledgement', () async {
    await journal.begin('voice-retry', _key.sid, 'call PSS');
    await journal.append('voice-retry', 'assistant', 'Calling PSS.');
    await journal.finish('voice-retry');
    final api = JournalApi()..historyFails = false;
    api.history = [
      ChatMessage('user', 'call PSS', localRequestId: 'voice-retry'),
      ChatMessage('bot', 'Calling PSS.', localRequestId: 'voice-retry'),
    ];
    final chat = ChatController(api, _key, outbox: outbox, voiceJournal: journal);
    await chat.ensureLoaded();
    await Future<void>.delayed(const Duration(milliseconds: 20));
    expect(chat.state.messages, hasLength(2));
    expect((await journal.load()).single.synced, isTrue);
    expect(api.executions, 0);
    expect(api.storedIds, isEmpty);
  });

}
