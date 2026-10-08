import 'package:gajala/core/voice_journal.dart';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/chat_controller.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/core/outbox.dart';

const _key = ChatKey('shell', 'app:test::general');

/// A Mac that is unreachable until [online] is set.
class FlakyApi extends GajalaApi {
  bool online = false;
  bool rejectWith500 = false;
  final List<String?> requestIds = [];
  final List<String> prompts = [];
  final List<int?> replyIds = [];

  FlakyApi() : super('http://127.0.0.1:1', 'test');

  @override
  Future<List<ChatMessage>> chatHistory(
    String sessionId, {
    int limit = 50,
  }) async => [];

  @override
  Future<List<AssistantWork>> assistantWork(String sessionId) async => [];

  @override
  Future<String> classifyWork(String id, String prompt) async => 'new_task';

  @override
  Stream<Map<String, dynamic>> runStream(
    String command,
    String prompt,
    String sessionId, {
    bool notify = false,
    String? project,
    String? requestId,
    String? continueTaskId,
    int? replyToMessageId,
  }) async* {
    final options = RequestOptions(path: '/run/stream');
    if (rejectWith500) {
      throw DioException(
        requestOptions: options,
        type: DioExceptionType.badResponse,
        response: Response(requestOptions: options, statusCode: 500),
      );
    }
    if (!online) {
      throw DioException(
        requestOptions: options,
        type: DioExceptionType.connectionError,
      );
    }
    requestIds.add(requestId);
    prompts.add(prompt);
    replyIds.add(replyToMessageId);
    yield {'type': 'step', 'label': 'Thinking…'};
    yield {
      'type': 'final',
      'result': 'reply to $prompt',
      'workspace': 'general',
    };
  }
}

void main() {
  late Directory dir;
  late Outbox outbox;

  setUp(() async {
    dir = await Directory.systemTemp.createTemp('gajala-outbox');
    outbox = Outbox(dir: () async => dir);
  });
  tearDown(() => dir.delete(recursive: true));

  OutboxEntry entry(String id, {DateTime? at, String? image}) => OutboxEntry(
    requestId: id,
    command: 'shell',
    sid: 'app:test::general',
    text: 'msg $id',
    imagePath: image,
    createdAt: at ?? DateTime.now(),
  );

  group('Outbox store', () {
    test('keeps entries in order across instances, removes by id', () async {
      await outbox.add(entry('a'));
      await outbox.add(entry('b'));
      final reopened = Outbox(dir: () async => dir);
      expect((await reopened.load()).pending.map((e) => e.requestId), [
        'a',
        'b',
      ]);
      await reopened.remove('a');
      expect((await outbox.load()).pending.map((e) => e.requestId), ['b']);
    });

    test('drops entries older than 48 hours and reports them', () async {
      final now = DateTime(2026, 10, 6, 12);
      final timed = Outbox(dir: () async => dir, now: () => now);
      await timed.add(
        entry('old', at: now.subtract(const Duration(hours: 49))),
      );
      await timed.add(entry('new', at: now.subtract(const Duration(hours: 1))));
      final r = await timed.load();
      expect(r.pending.map((e) => e.requestId), ['new']);
      expect(r.expired.map((e) => e.requestId), ['old']);
      expect((await timed.load()).expired, isEmpty);
    });

    test('refuses more than 50 waiting messages', () async {
      for (var i = 0; i < Outbox.maxEntries; i++) {
        await outbox.add(entry('$i'));
      }
      expect(() => outbox.add(entry('51')), throwsA(isA<OutboxFull>()));
    });

    test(
      'copies the photo so it survives, deletes the copy once sent',
      () async {
        final photo = File('${dir.path}/picker-cache.jpg')
          ..writeAsBytesSync([1, 2, 3]);
        final saved = await outbox.add(entry('p', image: photo.path));
        expect(saved.imagePath, isNot(photo.path));
        photo.deleteSync(); // the picker's cache is gone after a restart
        expect(File(saved.imagePath!).readAsBytesSync(), [1, 2, 3]);
        await outbox.remove('p');
        expect(File(saved.imagePath!).existsSync(), isFalse);
      },
    );
  });

  group('ChatController', () {
    test(
      'reply target survives offline outbox persistence and replay',
      () async {
        final api = FlakyApi();
        final chat = ChatController(
          api,
          _key,
          outbox: outbox,
          voiceJournal: VoiceJournal(dir: () async => dir),
        );
        await chat.send(
          'follow up',
          replyToMessageId: 41,
          replyToContent: 'Original question',
          replyToRole: 'user',
        );
        final saved = (await outbox.load()).pending.single;
        expect(saved.replyToMessageId, 41);
        expect(saved.replyToContent, 'Original question');

        api.online = true;
        await chat.replayOutbox();
        expect(api.replyIds, [41]);
        expect(chat.state.messages.first.replyToMessageId, 41);
      },
    );

    test(
      'offline send is kept, then delivered once with the same request id',
      () async {
        final api = FlakyApi();
        final chat = ChatController(
          api,
          _key,
          outbox: outbox,
          voiceJournal: VoiceJournal(dir: () async => dir),
        );
        await chat.send('deploy status');

        expect(chat.state.messages.last.role, 'outbox');
        final waiting = (await outbox.load()).pending;
        expect(waiting, hasLength(1));

        api.online = true;
        await chat.replayOutbox();
        expect(api.requestIds, [waiting.single.requestId]);
        expect(chat.state.messages.map((m) => m.role), ['user', 'bot']);
        expect(chat.state.messages.last.text, 'reply to deploy status');
        expect((await outbox.load()).pending, isEmpty);

        await chat.replayOutbox(); // nothing left: no second delivery
        expect(api.requestIds, hasLength(1));
      },
    );

    test('a server error is not kept for resending', () async {
      final api = FlakyApi()..rejectWith500 = true;
      final chat = ChatController(
        api,
        _key,
        outbox: outbox,
        voiceJournal: VoiceJournal(dir: () async => dir),
      );
      // An HTTP error means the Mac saw the request; the existing recovery
      // then polls history for a reply, which this test does not wait out.
      chat.send('hello');
      await Future<void>.delayed(const Duration(milliseconds: 100));
      expect((await outbox.load()).pending, isEmpty);
      expect(chat.state.messages.where((m) => m.role == 'outbox'), isEmpty);
    });

    test('unsent messages survive an app restart and send on reopen', () async {
      final api = FlakyApi();
      await ChatController(
        api,
        _key,
        outbox: outbox,
        voiceJournal: VoiceJournal(dir: () async => dir),
      ).send('remember me');

      api.online = true;
      final reopened = ChatController(
        api,
        _key,
        outbox: outbox,
        voiceJournal: VoiceJournal(dir: () async => dir),
      );
      await reopened.ensureLoaded();
      // ensureLoaded kicks off the replay without awaiting it.
      await Future<void>.delayed(const Duration(milliseconds: 50));
      expect(api.prompts, ['remember me']);
      expect(reopened.state.messages.where((m) => m.role == 'outbox'), isEmpty);
      expect(reopened.state.messages.last.text, 'reply to remember me');
    });

    test(
      'messages behind an undeliverable one stay waiting in order',
      () async {
        final api = FlakyApi();
        final chat = ChatController(
          api,
          _key,
          outbox: outbox,
          voiceJournal: VoiceJournal(dir: () async => dir),
        );
        await chat.send('first');
        await chat.send('second');
        expect(chat.state.messages.map((m) => m.role), ['outbox', 'outbox']);

        api.online = true;
        await chat.replayOutbox();
        expect(api.prompts, ['first', 'second']);
        expect((await outbox.load()).pending, isEmpty);
      },
    );
  });
}
