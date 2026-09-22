import 'dart:async';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/chat_controller.dart';
import 'package:gajala/core/models.dart';

class FakeApi extends GajalaApi {
  final started = Completer<void>();
  final release = Completer<void>();
  final List<String?> continuations = [];
  final List<String> prompts = [];
  int steerCalls = 0;
  String relation = 'new_task';
  bool holdFirst = true;
  int _runs = 0;

  FakeApi() : super('http://127.0.0.1:1', 'test');

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
  }) async* {
    continuations.add(continueTaskId);
    prompts.add(prompt);
    _runs++;
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
      final controller = ChatController(
        api,
        const ChatKey('shell', 'app:test'),
      );
      final first = controller.send('first');
      await api.started.future;

      final queued = controller.send(
        'photo later',
        imagePath: photo.path,
      );
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
    final controller = ChatController(api, const ChatKey('shell', 'app:test'));

    await controller.send('first');
    await controller.send('please fix that');

    expect(api.continuations, [null, 'work-1']);
    expect(api.steerCalls, 0);
  });

  test('new task classification does not reuse prior work', () async {
    final api = FakeApi()..holdFirst = false;
    final controller = ChatController(api, const ChatKey('shell', 'app:test'));

    await controller.send('first');
    await controller.send('unrelated question');

    expect(api.continuations, [null, null]);
  });
}
