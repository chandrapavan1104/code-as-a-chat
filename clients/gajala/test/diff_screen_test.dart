import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/diff.dart';
import 'package:gajala/core/state.dart';
import 'package:gajala/screens/diff_screen.dart';

final _json = {
  'project': 'demo',
  'branch': 'main',
  'head': 'abc1234',
  'additions': 2,
  'deletions': 1,
  'omitted_files': 0,
  'files': [
    {
      'path': 'app.py',
      'status': 'M',
      'additions': 2,
      'deletions': 1,
      'patch': 'diff --git a/app.py b/app.py\n...',
      'hunks': [
        {
          'header': '@@ -1,3 +1,4 @@',
          'lines': [
            {'t': ' ', 'old': 1, 'new': 1, 'text': 'one'},
            {'t': '-', 'old': 2, 'new': null, 'text': 'two'},
            {'t': '+', 'old': null, 'new': 2, 'text': 'TWO'},
            {'t': '+', 'old': null, 'new': 3, 'text': 'four'},
          ],
        },
      ],
    },
    {
      'path': 'logo.png',
      'status': 'A',
      'binary': true,
      'patch': '',
      'hunks': [],
    },
  ],
};

class DiffApi extends GajalaApi {
  String? asked;
  DiffApi() : super('http://127.0.0.1:1', 'test');
  @override
  Future<ProjectDiff> projectDiff(String? project) async {
    asked = project;
    return ProjectDiff.fromJson(_json);
  }
}

void main() {
  test('snippet names the file and new line numbers', () {
    final f = ProjectDiff.fromJson(_json).files.first;
    final picked = f.allLines.sublist(1, 4);
    expect(snippetForChat(f, picked),
        'In `app.py` lines 2–3:\n```diff\n-two\n+TWO\n+four\n```\n');
  });

  testWidgets('select a range and send it to the chat', (tester) async {
    final api = DiffApi();
    String? returned;
    await tester.pumpWidget(ProviderScope(
      overrides: [apiProvider.overrideWithValue(api)],
      child: MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () async {
              returned = await Navigator.of(context).push<String>(
                MaterialPageRoute(builder: (_) => const DiffScreen(project: 'demo')),
              );
            },
            child: const Text('open'),
          ),
        ),
      ),
    ));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();

    expect(api.asked, 'demo');
    expect(find.text('main @ abc1234'), findsOneWidget);
    expect(find.text('Binary file — no text diff.'), findsOneWidget);

    await tester.longPress(find.text('- two'));
    await tester.pump();
    await tester.tap(find.text('+ four'));
    await tester.pump();
    expect(find.text('3 lines · app.py'), findsOneWidget);

    await tester.tap(find.text('To chat'));
    await tester.pumpAndSettle();
    expect(returned, 'In `app.py` lines 2–3:\n```diff\n-two\n+TWO\n+four\n```\n');
  });
}
