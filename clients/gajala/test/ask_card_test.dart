import 'package:gajala/core/voice_journal.dart';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/chat_controller.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/core/outbox.dart';

const _card =
    '[[ask:{"question": "Which build?", "options": ["Debug", "Release"], "multi": false}]]';

class CardApi extends GajalaApi {
  CardApi() : super('http://127.0.0.1:1', 'test');

  @override
  Future<String> classifyWork(String id, String prompt) async => 'new_task';

  @override
  Future<List<ChatMessage>> chatHistory(String sessionId, {int limit = 50}) async =>
      [ChatMessage('user', 'build'), ChatMessage('bot', 'Ready.\n\n$_card')];

  @override
  Future<List<AssistantWork>> assistantWork(String sessionId) async => [];

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
    yield {'type': 'final', 'result': 'Ready.\n\n$_card', 'workspace': 'general'};
  }
}

void main() {
  test('splitAsk extracts the card and cleans the text', () {
    final (clean, card) = splitAsk('Ready.\n\n$_card');
    expect(clean, 'Ready.');
    expect(card!.question, 'Which build?');
    expect(card.options, ['Debug', 'Release']);
    expect(card.multi, isFalse);
  });

  test('broken or one-option cards are dropped, text kept', () {
    expect(splitAsk('Hi [[ask:{nope}]]').$2, isNull);
    expect(splitAsk('Hi [[ask:{"question":"Q","options":["a"]}]]'),
        ('Hi', null));
  });

  test('live replies and reloaded history both carry the card', () async {
    final dir = Directory.systemTemp.createTempSync('gajala-card');
    addTearDown(() => dir.deleteSync(recursive: true));
    final api = CardApi();

    final chat = ChatController(api, const ChatKey('shell', 'a::general'),
        voiceJournal: VoiceJournal(dir: () async => dir), outbox: Outbox(dir: () async => dir));
    await chat.send('build');
    expect(chat.state.messages.last.text, 'Ready.');
    expect(chat.state.messages.last.ask?.options, ['Debug', 'Release']);

    final reloaded = ChatController(api, const ChatKey('shell', 'b::general'),
        voiceJournal: VoiceJournal(dir: () async => dir), outbox: Outbox(dir: () async => dir));
    await reloaded.ensureLoaded();
    expect(reloaded.state.messages.last.ask?.question, 'Which build?');
  });
}
