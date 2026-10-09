import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/core/state.dart';
import 'package:gajala/screens/tasks_screen.dart';
import 'package:gajala/screens/work_result_screen.dart';

const _payload = <String, dynamic>{
  'job_id': 42,
  'title': 'Research task from server',
  'result': {
    'available': true,
    'kind': 'research',
    'completeness': 'partial',
    'text': 'Research summary so far.\n\n```dart\nfinal answer = 42;\n```',
    'artifacts': [
      {'kind': 'git_branch', 'ref': 'research/queue-42', 'files_changed': []},
      {
        'kind': 'file',
        'path': '/uploads/queue/42/report.md',
        'name': 'report.md',
        'size': 128,
      },
    ],
  },
  'attempts': [
    {
      'id': 91,
      'attempt_no': 2,
      'engine': 'gemini',
      'stage': 'research',
      'status': 'failed',
      'started_at': 1780000000,
      'ended_at': 1780000030,
      'stderr': 'Provider timed out',
      'stderr_truncated': true,
    },
  ],
};

class ResultApi extends GajalaApi {
  ResultApi() : super('http://127.0.0.1:1', 'test');

  @override
  Future<QueueJobResult> queueJobResult(int id) async =>
      QueueJobResult.fromJson(_payload);

  @override
  Future<
    ({
      List<QueueJob> jobs,
      Map<String, dynamic> settings,
      Map<String, dynamic> health,
    })
  >
  queue() async => (
    jobs: [
      _job(1, 'queued', 'Queued coding work'),
      _job(2, 'needs_you', 'Decision needed'),
      _job(
        3,
        'completed',
        'Finished research',
        result: const QueueResultSummary(
          available: true,
          kind: 'research',
          completeness: 'complete',
        ),
      ),
    ],
    settings: const {
      'enabled': true,
      'start': '23:00',
      'end': '07:00',
      'max_jobs': 4,
    },
    health: const {'state': 'healthy', 'headline': 'All jobs accounted for.'},
  );
}

QueueJob _job(
  int id,
  String status,
  String title, {
  QueueResultSummary result = const QueueResultSummary(),
}) => QueueJob(
  id: id,
  project: '/repo',
  projectName: 'repo',
  task: title,
  title: title,
  readiness: 'refined',
  tag: 'auto',
  engine: 'auto',
  status: status,
  spec: const {},
  deployment: const {},
  supervision: const {},
  awareness: const {},
  result: result,
  filesChanged: const [],
  tokensTotal: 0,
  dependsOn: const [],
  blockedBy: const [],
  dependencies: const [],
  closureHistory: const [],
);

void main() {
  test('parses nested result contract and outer attempt history', () {
    final result = QueueJobResult.fromJson(_payload);

    expect(result.jobId, 42);
    expect(result.available, isTrue);
    expect(result.kind, 'research');
    expect(result.completeness, 'partial');
    expect(result.title, 'Research task from server');
    expect(result.text, contains('Research summary so far'));
    expect(result.artifacts.first.ref, 'research/queue-42');
    expect(result.artifacts.last.name, 'report.md');
    expect(result.artifacts.last.size, 128);
    expect(result.attempts.single.attemptNo, 2);
    expect(result.attempts.single.stderrTruncated, isTrue);
  });

  testWidgets('opens full partial research output and expandable attempt logs', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(800, 1200);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [apiProvider.overrideWithValue(ResultApi())],
        child: MaterialApp(
          home: const WorkResultScreen(jobId: 42, title: 'Research task'),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('Research task'), findsOneWidget);
    expect(
      find.text(
        'Partial result: the work stopped before completion. Review the available output and attempt details.',
      ),
      findsOneWidget,
    );
    expect(find.textContaining('Research summary so far.'), findsOneWidget);
    expect(find.text('DART'), findsOneWidget);
    expect(find.text('research/queue-42'), findsOneWidget);

    await tester.tap(find.text('Attempts and logs'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('Attempt 2 · gemini'));
    await tester.tap(find.text('Attempt 2 · gemini'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Provider timed out'), findsOneWidget);
    expect(find.textContaining('stderr truncated'), findsOneWidget);
  });

  testWidgets('Work tabs group jobs and app-bar settings own health/schedule', (
    tester,
  ) async {
    await tester.pumpWidget(
      ProviderScope(
        overrides: [apiProvider.overrideWithValue(ResultApi())],
        child: const MaterialApp(home: TasksScreen()),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Queued coding work'), findsOneWidget);
    expect(find.text('Finished research'), findsNothing);

    await tester.tap(find.text('Needs you'));
    await tester.pumpAndSettle();
    expect(find.text('Decision needed'), findsOneWidget);
    expect(find.text('Queued coding work'), findsNothing);

    await tester.tap(find.text('Results'));
    await tester.pumpAndSettle();
    expect(find.text('Finished research'), findsOneWidget);
    expect(find.text('Open result'), findsOneWidget);
    await tester.tap(find.byTooltip('Work settings'));
    await tester.pumpAndSettle();
    expect(find.text('QUEUE SUPERVISOR · HEALTHY'), findsOneWidget);
    expect(find.text('Night Shift'), findsOneWidget);
    expect(find.text('Max jobs / night'), findsOneWidget);
  });
}
