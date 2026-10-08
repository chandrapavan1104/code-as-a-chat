import 'dart:io';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/core/chat_controller.dart';
import 'package:gajala/core/outbox.dart';
import 'package:gajala/core/voice_journal.dart';

class ResearchApi extends GajalaApi {
  ResearchApi() : super('http://127.0.0.1:1', 'test');
  String status = 'working';
  bool cancelled = false;
  @override
  Future<List<AssistantWork>> assistantWork(String sessionId) async => [
    AssistantWork(
      id: 'r1',
      command: 'research',
      status: status,
      summary: 'Checking sources',
      nextAction: '',
      blocker: '',
      revision: 1,
    ),
  ];
  @override
  Future<List<ChatMessage>> chatHistory(
    String sessionId, {
    int limit = 50,
  }) async => status == 'completed'
      ? [
          ChatMessage(
            'bot',
            'Research reply to: Compare providers\n\nReport',
            localRequestId: 'research-result:r1',
          ),
        ]
      : [];
  @override
  Future<void> cancelResearch(String id) async {
    cancelled = true;
    status = 'cancelled';
  }
}

void main() {
  late Directory dir;
  setUp(() => dir = Directory.systemTemp.createTempSync('research-chat'));
  tearDown(() => dir.deleteSync(recursive: true));
  ChatController make(ResearchApi api) => ChatController(
    api,
    const ChatKey('shell', 'app:test::general'),
    outbox: Outbox(dir: () async => dir),
    voiceJournal: VoiceJournal(dir: () async => dir),
  );
  test(
    'working job is visible, completion appends once without foreground run',
    () async {
      final api = ResearchApi();
      final chat = make(api);
      await chat.refreshBackgroundResearch();
      expect(chat.state.backgroundResearch.single.id, 'r1');
      expect(chat.state.sending, false);
      api.status = 'completed';
      await chat.refreshBackgroundResearch();
      await chat.refreshBackgroundResearch();
      expect(chat.state.backgroundResearch, isEmpty);
      expect(chat.state.messages, hasLength(1));
      expect(chat.state.messages.single.localRequestId, 'research-result:r1');
    },
  );
  test('background job can stop while foreground is idle', () async {
    final api = ResearchApi();
    final chat = make(api);
    await chat.refreshBackgroundResearch();
    await chat.cancelBackgroundResearch('r1');
    expect(api.cancelled, true);
    expect(chat.state.backgroundResearch, isEmpty);
  });
}
