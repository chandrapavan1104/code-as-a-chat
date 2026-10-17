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

  Completer<void>? holdNextInput; // lets a test delay one request
  @override
  Future<void> shellInput(String deviceKey, String id, String data) async {
    final hold = holdNextInput;
    holdNextInput = null;
    if (hold != null) await hold.future;
    inputs.add(data);
  }

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

  Future<FakeShellApi> openSession(WidgetTester tester) async {
    final api = FakeShellApi()
      ..status = {'enabled': true, 'paired': true, 'this_device': true}
      ..sessions.add({'id': 'abcd1234', 'cwd': '/Users/me', 'closed': false});
    await pumpTerminal(tester, api);
    api.out!.add({'type': 'hello', 'mode': 'line', 'program': null});
    await tester.pumpAndSettle();
    return api;
  }

  Future<void> flushKeys(WidgetTester tester) async {
    await tester.pump(const Duration(milliseconds: 40));
    await tester.pumpAndSettle();
  }

  testWidgets('a raw-key menu switches to the key pad; arrows and Enter go through', (tester) async {
    final api = await openSession(tester);
    api.out!.add({'type': 'mode', 'mode': 'keys', 'program': 'gh'});
    await tester.pumpAndSettle();
    expect(find.textContaining('gh is reading keys'), findsOneWidget);
    await tester.tap(find.byIcon(Icons.arrow_downward));
    await tester.tap(find.byIcon(Icons.keyboard_return));
    await flushKeys(tester);
    expect(api.inputs, ['\x1B[B\r']); // batched, in order, Enter as CR

    await tester.enterText(find.byKey(const ValueKey('keys-input')), '\u200Bab');
    await flushKeys(tester);
    await tester.enterText(find.byKey(const ValueKey('keys-input')), '');
    await flushKeys(tester);
    expect(api.inputs.sublist(1), ['ab', '\x7f']);

    await tester.tap(find.text('Ctrl'));
    await tester.pump();
    await tester.enterText(find.byKey(const ValueKey('keys-input')), '\u200Bc');
    await flushKeys(tester);
    expect(api.inputs.last, '\x03');

    api.out!.add({'type': 'mode', 'mode': 'line', 'program': null});
    await tester.pumpAndSettle();
    expect(find.text('Command'), findsOneWidget);
  });

  testWidgets('application cursor mode changes the arrow sequence', (tester) async {
    final api = await openSession(tester);
    api.out!.add({'type': 'output', 'data': '\x1B[?1hmenu', 'next': 9});
    api.out!.add({'type': 'mode', 'mode': 'keys', 'program': 'fzf'});
    await tester.pumpAndSettle();
    await tester.tap(find.byIcon(Icons.arrow_upward));
    await flushKeys(tester);
    expect(api.inputs, ['\x1BOA']);
  });

  testWidgets('password prompts are masked and never kept in history', (tester) async {
    final api = await openSession(tester);
    api.out!.add({'type': 'mode', 'mode': 'secret', 'program': 'sudo'});
    await tester.pumpAndSettle();
    final field = tester.widget<TextField>(find.byKey(const ValueKey('secret-input')));
    expect(field.obscureText, isTrue);
    await tester.enterText(find.byKey(const ValueKey('secret-input')), 'hunter2');
    await tester.testTextInput.receiveAction(TextInputAction.send);
    await tester.pumpAndSettle();
    expect(api.inputs, ['hunter2\n']);
    api.out!.add({'type': 'mode', 'mode': 'line', 'program': null});
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Previous command'));
    await tester.pump();
    expect(find.text('hunter2'), findsNothing);
  });

  testWidgets('manual Keys toggle, and a prompt returning resets it', (tester) async {
    final api = await openSession(tester);
    await tester.tap(find.text('Keys'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Keys mode (manual)'), findsOneWidget);
    await tester.tap(find.text('Back to line'));
    await tester.pumpAndSettle();
    expect(find.text('Command'), findsOneWidget);
    expect(api.inputs, isEmpty);
  });

  testWidgets('inputs stay in order even when a request is slow', (tester) async {
    final api = await openSession(tester);
    final hold = Completer<void>();
    api.holdNextInput = hold;
    await tester.enterText(find.byType(TextField), 'first');
    await tester.testTextInput.receiveAction(TextInputAction.send);
    await tester.pump();
    await tester.tap(find.text('Ctrl-C'));
    await tester.pump();
    expect(api.inputs, isEmpty); // Ctrl-C waits behind the slow first send
    hold.complete();
    await tester.pumpAndSettle();
    expect(api.inputs, ['first\n', '\x03']);
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
