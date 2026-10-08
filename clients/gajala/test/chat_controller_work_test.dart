import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/chat_controller.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/core/outbox.dart';

/// Each test gets its own throwaway outbox instead of the phone's storage.
Outbox _tempOutbox() {
  final dir = Directory.systemTemp.createTempSync('gajala-outbox');
  addTearDown(() => dir.delete(recursive: true));
  return Outbox(dir: () async => dir);
}

class FakeApi extends GajalaApi {
  final started = Completer<void>();
  final release = Completer<void>();
  final List<String?> continuations = [];
  final List<String> prompts = [];
  final List<String?> sentProjects = [];
  final List<int?> replyIds = [];
  List<ChatMessage> history = [];
  int steerCalls = 0;
  String relation = 'new_task';
  bool holdFirst = true;
  int _runs = 0;

  FakeApi() : super('http://127.0.0.1:1', 'test');

  @override
  Future<List<ChatMessage>> chatHistory(
    String sessionId, {
    int limit = 50,
  }) async => history;

  @override
  Future<String> classifyWork(String id, String prompt) async => relation;

  @override
  Future<AssistantWork> steerWork(String id) async {
    steerCalls++;
    return AssistantWork.fromJson({'id': id, 'status': 'working'});
  }

  @override
  Future<String> uploadImage(List<int> bytes, String filename) async =>
      '/uploads/$filename';

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
    sentProjects.add(project);
    replyIds.add(replyToMessageId);
    continuations.add(continueTaskId);
    prompts.add(prompt);
    _runs++;
    if (prompt == 'hydrate ids') {
      history = [
        ChatMessage('user', prompt, messageId: 91, localRequestId: requestId),
        ChatMessage('bot', 'done', messageId: 92, localRequestId: requestId),
      ];
    }
    if (_runs == 1) {
      started.complete();
      if (holdFirst) await release.future;
    }
    yield {
      'type': 'work',
      'work': {'id': 'work-1', 'status': 'accepted', 'revision': 1},
    };
    yield {'type': 'final', 'result': 'done', 'workspace': 'general'};
  }
}

void main() {
  test(
    'queued ordinary message does not steer and keeps its attachment',
    () async {
      final temp = await Directory.systemTemp.createTemp('gajala-photo-test');
      addTearDown(() => temp.delete(recursive: true));
      final photo = File('${temp.path}/photo.jpg');
      await photo.writeAsBytes([1, 2, 3]);
      final api = FakeApi();
      final outbox = _tempOutbox();
      final controller = ChatController(
        api,
        const ChatKey('shell', 'app:test'),
        outbox: outbox,
      );
      final first = controller.send('first');
      await api.started.future;

      final queued = controller.send('photo later', imagePath: photo.path);
      expect(api.steerCalls, 0);
      expect(controller.state.queued, hasLength(1));
      expect(controller.state.queued.single.imagePath, photo.path);

      api.release.complete();
      await Future.wait([first, queued]);
      expect(api.steerCalls, 0);
      expect(api.prompts.last, contains('/uploads/photo.jpg'));
      controller.dispose();
    },
  );

  test('classified correction reuses the active work id', () async {
    final api = FakeApi()..holdFirst = false;
    api.relation = 'correction';
    final controller = ChatController(
      api,
      const ChatKey('shell', 'app:test'),
      outbox: _tempOutbox(),
    );

    await controller.send('first');
    await controller.send('please fix that');

    expect(api.continuations, [null, 'work-1']);
    expect(api.steerCalls, 0);
  });

  test('new task classification does not reuse prior work', () async {
    final api = FakeApi()..holdFirst = false;
    final controller = ChatController(
      api,
      const ChatKey('shell', 'app:test'),
      outbox: _tempOutbox(),
    );

    await controller.send('first');
    await controller.send('unrelated question');

    expect(api.continuations, [null, null]);
  });

  test(
    'queued reply snapshots its project and skips unrelated work classification',
    () async {
      final api = FakeApi();
      final outbox = _tempOutbox();
      final controller = ChatController(
        api,
        const ChatKey('shell', 'app:test'),
        outbox: outbox,
      );
      controller.setProjectContext('alpha');
      final first = controller.send('first');
      await api.started.future;

      controller.setProjectContext('beta');
      final reply = controller.send(
        'answer that message',
        replyToMessageId: 17,
        replyToContent: 'Question',
        replyToRole: 'user',
      );
      await reply;
      expect((await outbox.load()).pending.last.project, 'beta');
      controller.setProjectContext('gamma');

      api.release.complete();
      await first;
      expect(api.sentProjects, ['alpha', 'beta']);
      expect(api.replyIds, [null, 17]);
      expect(api.continuations, [null, null]);
    },
  );

  test('history refresh hydrates IDs when a final event omits them', () async {
    final api = FakeApi()..holdFirst = false;
    final controller = ChatController(
      api,
      const ChatKey('shell', 'app:test'),
      outbox: _tempOutbox(),
    );
    await controller.send('hydrate ids');
    expect(controller.state.messages.first.messageId, 91);
    expect(controller.state.messages[1].messageId, 92);
  });
}
