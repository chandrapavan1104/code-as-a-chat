import 'package:gajala/core/voice_journal.dart';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/chat_controller.dart';
import 'package:gajala/core/outbox.dart';

class AskingApi extends GajalaApi {
  final posted = <String, Map<String, dynamic>>{};
  AskingApi() : super('http://127.0.0.1:1', 'test');

  @override
  Future<String> classifyWork(String id, String prompt) async => 'new_task';

  @override
  Future<void> phoneResult(String id, Map<String, dynamic> result) async {
    posted[id] = result;
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
    int? replyToMessageId,
  }) async* {
    yield {'type': 'step', 'label': 'Thinking…'};
    yield {
      'type': 'phone_request',
      'id': 'req-1',
      'command': 'location.get',
      'args': <String, dynamic>{},
    };
    await Future<void>.delayed(const Duration(milliseconds: 20));
    yield {
      'type': 'final',
      'result': 'You are near Charminar.',
      'workspace': 'general',
    };
  }
}

void main() {
  late Directory dir;
  setUp(() => dir = Directory.systemTemp.createTempSync('gajala-phone'));
  tearDown(() => dir.deleteSync(recursive: true));

  ChatController make(
    AskingApi api,
    Map<String, dynamic> answer,
    List<String> asked,
  ) => ChatController(
    api,
    const ChatKey('shell', 'app:test::general'),
    voiceJournal: VoiceJournal(dir: () async => dir),
    outbox: Outbox(dir: () async => dir),
    phone: (command, args, _) async {
      asked.add(command);
      return answer;
    },
  );

  test('answers the request and notes what was shared', () async {
    final api = AskingApi();
    final asked = <String>[];
    final chat = make(api, {
      'ok': true,
      'data': {'lat': 17.36, 'lon': 78.47},
    }, asked);
    await chat.send('where am I?');
    expect(asked, ['location.get']);
    expect(api.posted['req-1'], {
      'ok': true,
      'data': {'lat': 17.36, 'lon': 78.47},
    });
    final notes = chat.state.messages
        .where((m) => m.role == 'system')
        .map((m) => m.text);
    expect(notes, contains('📍 Shared your location with Gajala'));
    expect(chat.state.messages.last.text, 'You are near Charminar.');
  });

  test('a disabled ability is reported, not silently ignored', () async {
    final api = AskingApi();
    final chat = make(api, {
      'ok': false,
      'disabled': true,
      'error':
          'Location is off. Turn it on in Gajala → menu → Phone abilities.',
    }, []);
    await chat.send('where am I?');
    expect(api.posted['req-1']?['disabled'], true);
    expect(
      chat.state.messages.where((m) => m.role == 'system').single.text,
      'Gajala asked for location: Location is off. Turn it on in Gajala → menu → Phone abilities.',
    );
  });
  test('voice phone handler applies only to its own turn', () async {
    final api = AskingApi();
    final defaults = <String>[];
    final voice = <String>[];
    final chat = make(api, {'ok': false, 'error': 'default handler'}, defaults);
    await chat.send(
      'voice request',
      phone: (command, args, _) async {
        voice.add(command);
        return {'ok': false, 'error': 'voice confirmation cancelled'};
      },
    );
    expect(voice, ['location.get']);
    expect(defaults, isEmpty);
    expect(api.posted['req-1']?['error'], 'voice confirmation cancelled');
    await chat.send('ordinary request');
    expect(defaults, ['location.get']);
    expect(voice, hasLength(1));
    expect(api.posted['req-1']?['error'], 'default handler');
  });
}
