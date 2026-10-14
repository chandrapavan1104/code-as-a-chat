import 'dart:async';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/state.dart';
import 'package:gajala/screens/terminal_screen.dart';

class FakeShellApi extends GajalaApi {
  FakeShellApi() : super('http://localhost', 'test');
  Map<String, dynamic> status = {'enabled': true, 'paired': false, 'this_device': false};
  final sessions = <Map<String, dynamic>>[];
  final inputs = <String>[];
  final attachedAt = <int>[];
  StreamController<Map<String, dynamic>>? out;
  String? pairedKey;

  @override
  Future<Map<String, dynamic>> shellStatus(String deviceKey) async => status;

  @override
  Future<Map<String, dynamic>> shellPair(String deviceKey, String label) async {
    pairedKey = deviceKey;
    status = {'enabled': true, 'paired': true, 'this_device': true, 'label': label};
    return {'paired': true};
  }

  @override
  Future<List<Map<String, dynamic>>> shellSessions(String deviceKey) async => sessions;

  @override
  Future<Map<String, dynamic>> projects() async =>
      {'current_name': 'alpha', 'projects': [{'name': 'alpha'}]};

  @override
  Future<Map<String, dynamic>> shellCreate(String deviceKey,
      {String? project, int cols = 80, int rows = 40}) async {
    final s = {'id': 'abcd1234', 'cwd': '/Users/me/$project', 'closed': false};
    sessions.add(s);
    return s;
  }

  @override
  Future<void> shellInput(String deviceKey, String id, String data) async => inputs.add(data);

  @override
  Stream<Map<String, dynamic>> shellOutput(String deviceKey, String id, int offset) {
    attachedAt.add(offset);
    out = StreamController<Map<String, dynamic>>();
    return out!.stream;
  }
}

Future<void> pumpTerminal(WidgetTester tester, FakeShellApi api) async {
  await tester.pumpWidget(ProviderScope(
    overrides: [apiProvider.overrideWithValue(api)],
    child: MaterialApp(
      home: TerminalScreen(requireUnlock: false, deviceKey: () async => 'k' * 43),
    ),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('pair, open a shell, stream output, run a command', (tester) async {
    final api = FakeShellApi();
    await pumpTerminal(tester, api);
    expect(find.text('Pair this phone'), findsOneWidget);
    await tester.tap(find.text('Pair this phone'));
    await tester.pumpAndSettle();
    expect(api.pairedKey, 'k' * 43);

    await tester.tap(find.text('Open a shell'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Open shell'));
    await tester.pumpAndSettle();
    expect(find.text('alpha · abcd'), findsOneWidget);

    api.out!.add({'type': 'hello'});
    api.out!.add({'type': 'output', 'data': '\x1B[32mready\x1B[0m\r\nme@mac alpha % ', 'next': 30});
    await tester.pumpAndSettle();
    expect(find.text('ready'), findsOneWidget);
    expect(find.text('me@mac alpha % '), findsOneWidget);

    await tester.enterText(find.byType(TextField), 'git status');
    await tester.testTextInput.receiveAction(TextInputAction.send);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Ctrl-C'));
    await tester.pumpAndSettle();
    expect(api.inputs, ['git status\n', '\x03']);
  });

  testWidgets('a dropped stream resumes from the last offset', (tester) async {
    final api = FakeShellApi()
      ..status = {'enabled': true, 'paired': true, 'this_device': true}
      ..sessions.add({'id': 'abcd1234', 'cwd': '/Users/me', 'closed': false});
    await pumpTerminal(tester, api);
    expect(api.attachedAt, [0]);
    api.out!.add({'type': 'output', 'data': 'hello\n', 'next': 6});
    await tester.pump();
    await api.out!.close(); // network drop, no exit frame
    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();
    expect(api.attachedAt, [0, 6]);
    api.out!.add({'type': 'exit', 'code': 0, 'next': 6});
    await tester.pumpAndSettle();
    expect(find.textContaining('session ended (exit 0)'), findsOneWidget);
    expect(find.byType(TextField), findsNothing);
  });

  testWidgets('another paired device is explained, not offered a shell', (tester) async {
    final api = FakeShellApi()
      ..status = {'enabled': true, 'paired': true, 'this_device': false, 'label': 'Old phone'};
    await pumpTerminal(tester, api);
    expect(find.textContaining('paired with Old phone'), findsOneWidget);
    expect(find.text('Pair this phone'), findsNothing);
  });

  testWidgets('server refusal detail is shown', (tester) async {
    final api = _FailingApi();
    await pumpTerminal(tester, api);
    expect(find.textContaining('turned off on the Mac'), findsOneWidget);
  });
}

class _FailingApi extends FakeShellApi {
  @override
  Future<Map<String, dynamic>> shellStatus(String deviceKey) async {
    final req = RequestOptions(path: '/api/shell/status');
    throw DioException(
      requestOptions: req,
      response: Response(requestOptions: req, statusCode: 403,
          data: {'detail': 'The remote shell is turned off on the Mac.'}),
    );
  }
}
