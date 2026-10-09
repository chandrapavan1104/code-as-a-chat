import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/screens/sessions_screen.dart';

class FakeSessionsApi {
  final requestedEngines = <String?>[];
  String? detailId;
  String? detailEngine;
  String? continueId;
  String? continueEngine;
  String? continueProject;
  String? clientId;

  Future<Map<String, dynamic>> librarySessions(
    String? engine,
    String? project,
  ) async {
    requestedEngines.add(engine);
    return {
      'items': [
        {
          'id': 'cli-session-1',
          'engine': engine == null || engine == 'all' ? 'claude' : engine,
          'title': 'Add the retry path',
          'project': '/work/demo',
          'preview': 'Recent request preview',
        },
      ],
    };
  }

  Future<Map<String, dynamic>> librarySession(String id, String engine) async {
    detailId = id;
    detailEngine = engine;
    return {
      'id': id,
      'engine': engine,
      'cwd': '/work/demo',
      'preview': 'Exact session preview',
      'turns': [
        {'role': 'user', 'content': 'Add the retry path'},
        {'role': 'assistant', 'content': 'I will inspect the retry flow.'},
      ],
      'truncated': false,
    };
  }

  Future<Map<String, dynamic>> continueLibrarySession(
    String id,
    String engine,
    String? project, {
    String? clientId,
  }) async {
    continueId = id;
    continueEngine = engine;
    continueProject = project;
    this.clientId = clientId;
    return {
      'session_id': 'app:continued-chat',
      'project': project,
      'engine': engine,
    };
  }
}

void main() {
  testWidgets('filters by engine, loads details, and continues with context', (
    tester,
  ) async {
    final api = FakeSessionsApi();
    Map<String, dynamic>? continued;
    await tester.pumpWidget(
      MaterialApp(
        home: SessionsScreen(
          api: api,
          project: '/work/demo',
          clientId: 'install-7',
          onContinue: (value) => continued = value,
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Add the retry path'), findsOneWidget);

    await tester.tap(find.text('Codex'));
    await tester.pumpAndSettle();
    expect(api.requestedEngines.last, 'codex');
    expect(find.text('Add the retry path'), findsOneWidget);

    await tester.tap(find.text('Add the retry path'));
    await tester.pumpAndSettle();
    expect(api.detailId, 'cli-session-1');
    expect(api.detailEngine, 'codex');
    expect(find.text('I will inspect the retry flow.'), findsOneWidget);

    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();
    expect(api.continueId, 'cli-session-1');
    expect(api.continueEngine, 'codex');
    expect(api.continueProject, '/work/demo');
    expect(api.clientId, 'install-7');
    expect(continued?['session_id'], 'app:continued-chat');
  });
}
