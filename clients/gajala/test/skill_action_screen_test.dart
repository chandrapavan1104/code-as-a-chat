import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/core/state.dart';
import 'package:gajala/screens/skill_action_screen.dart';

class FakeActionApi extends GajalaApi {
  FakeActionApi() : super('http://localhost', 'test');

  String? command;
  String? prompt;
  String? sessionId;
  String? project;
  List<Map<String, dynamic>> frames = [];

  @override
  Future<Map<String, dynamic>> projects() async => {
    'current_name': 'alpha',
    'projects': [
      {'name': 'alpha', 'active': true},
      {'name': 'beta', 'active': false},
    ],
  };

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
    this.command = command;
    this.prompt = prompt;
    this.sessionId = sessionId;
    this.project = project;
    for (final frame in frames) {
      yield frame;
    }
  }
}

Skill _skill(
  String name, {
  String? command,
  String help = 'Choose an action.',
}) => Skill(
  name: name,
  command: command ?? name,
  description: '$name skill',
  helpLine: help,
  exposeToAgent: true,
  enabled: true,
);

Future<void> _showSkill(
  WidgetTester tester,
  FakeActionApi api,
  Skill skill,
) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [apiProvider.overrideWithValue(api)],
      child: MaterialApp(
        home: SkillActionScreen(
          skill: skill,
          sessionIdLoader: () async => 'app:install-id',
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('coding task uses chosen project and renders streamed result', (
    tester,
  ) async {
    final api = FakeActionApi()
      ..frames = [
        {'type': 'step', 'label': 'Thinking…'},
        {'type': 'step', 'n': 1, 'tool': 'codex', 'project': 'beta'},
        {'type': 'final', 'result': 'Patch prepared.\n\nAll checks pass.'},
      ];
    await _showSkill(
      tester,
      api,
      _skill('codex', help: 'Write a focused task.'),
    );

    expect(api.command, isNull, reason: 'Opening a skill never runs it.');
    expect(find.text('Write a focused task.'), findsOneWidget);
    expect(find.text('alpha'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('project-picker')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('beta').last);
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'Fix retry handling');
    expect(
      tester.widget<TextField>(find.byType(TextField)).controller?.text,
      'Fix retry handling',
    );
    await tester.ensureVisible(find.text('Run task'));
    await tester.tap(find.text('Run task'));
    await tester.pumpAndSettle();

    expect(api.command, 'codex');
    expect(api.prompt, 'Fix retry handling');
    expect(api.sessionId, 'app:install-id');
    expect(api.project, 'beta');
    expect(find.text('Patch prepared.\n\nAll checks pass.'), findsOneWidget);
    expect(find.textContaining('Step 1 · codex'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'clearing errors requires confirmation and reports stream errors',
    (tester) async {
      final api = FakeActionApi()
        ..frames = [
          {'type': 'step', 'label': 'Reading error store'},
          {'type': 'error', 'message': 'Store is locked.'},
        ];
      await _showSkill(tester, api, _skill('errors'));

      await tester.tap(find.byKey(const ValueKey('action-errors')));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Clear errors').last);
      await tester.pumpAndSettle();
      expect(find.text('Run Clear errors'), findsOneWidget);
      await tester.ensureVisible(find.text('Run Clear errors'));
      await tester.tap(find.text('Run Clear errors'));
      await tester.pumpAndSettle();
      expect(find.text('Confirm action'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(api.command, isNull);

      await tester.tap(find.text('Run Clear errors'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Continue'));
      await tester.pumpAndSettle();
      expect(api.command, 'errors');
      expect(api.prompt, 'clear');
      expect(find.text('Store is locked.'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('fixed context actions are explicit and refresh is confirmed', (
    tester,
  ) async {
    final api = FakeActionApi()
      ..frames = [
        {'type': 'final', 'result': 'Context refreshed.'},
      ];
    await _showSkill(tester, api, _skill('context'));

    await tester.tap(find.byKey(const ValueKey('action-context')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Refresh context').last);
    await tester.pumpAndSettle();
    expect(find.text('Run Refresh context'), findsOneWidget);
    await tester.ensureVisible(find.text('Run Refresh context'));
    await tester.tap(find.text('Run Refresh context'));
    await tester.pumpAndSettle();
    expect(find.textContaining('rewrite the shared context'), findsOneWidget);
    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();
    expect(api.command, 'context');
    expect(api.prompt, 'refresh');
    expect(find.text('Context refreshed.'), findsOneWidget);
  });

  testWidgets('unknown skills use helpLine and explicit arguments', (
    tester,
  ) async {
    final api = FakeActionApi()
      ..frames = [
        {'type': 'final', 'result': 'Argument accepted.'},
      ];
    await _showSkill(
      tester,
      api,
      _skill(
        'custom',
        command: 'custom-command',
        help: 'Use --check or --repair.',
      ),
    );
    expect(find.text('Use --check or --repair.'), findsOneWidget);
    await tester.enterText(find.byType(TextField), '--check');
    await tester.tap(find.text('Run skill'));
    await tester.pumpAndSettle();
    expect(api.command, 'custom-command');
    expect(api.prompt, '--check');
  });

  testWidgets('Firebase deploy target is confirmed and scoped to project', (
    tester,
  ) async {
    final api = FakeActionApi()
      ..frames = [
        {'type': 'final', 'result': 'Hosting deployed.'},
      ];
    await _showSkill(tester, api, _skill('firebase'));

    await tester.tap(find.byKey(const ValueKey('action-firebase')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Deploy').last);
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'hosting');
    await tester.tap(find.text('Run Deploy'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Deploy Firebase resources'), findsOneWidget);
    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();

    expect(api.command, 'firebase');
    expect(api.prompt, 'deploy hosting');
    expect(api.project, 'alpha');
    expect(find.text('Hosting deployed.'), findsOneWidget);
  });

  testWidgets('shipping a fix requires explicit confirmation', (tester) async {
    final api = FakeActionApi()
      ..frames = [
        {'type': 'final', 'result': 'Pending fix merged.'},
      ];
    await _showSkill(tester, api, _skill('fix'));

    await tester.tap(find.byKey(const ValueKey('action-fix')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Ship pending fix').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Run Ship pending fix'));
    await tester.pumpAndSettle();
    expect(find.text('Confirm action'), findsOneWidget);
    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();

    expect(api.command, 'fix');
    expect(api.prompt, 'ship');
    expect(find.text('Pending fix merged.'), findsOneWidget);
  });

  testWidgets('Codex login action is confirmed before starting device flow', (
    tester,
  ) async {
    final api = FakeActionApi()
      ..frames = [
        {'type': 'final', 'result': 'Login device flow started.'},
      ];
    await _showSkill(tester, api, _skill('auth'));

    await tester.tap(find.byKey(const ValueKey('action-auth')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Start Codex login').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Run Start Codex login'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Codex device-code login'), findsOneWidget);
    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();

    expect(api.command, 'auth');
    expect(api.prompt, 'codex');
  });

  testWidgets('phone approval request becomes an explicit unsupported error', (
    tester,
  ) async {
    final api = FakeActionApi()
      ..frames = [
        {'type': 'phone_request', 'question': 'Allow access?'},
      ];
    await _showSkill(tester, api, _skill('custom'));
    await tester.tap(find.text('Run skill'));
    await tester.pumpAndSettle();
    expect(
      find.textContaining('on-phone approval that is unavailable'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
  });
}
