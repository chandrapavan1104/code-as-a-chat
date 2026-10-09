import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/screens/files_screen.dart';

class FakeFilesApi {
  final paths = <String>[];
  String? shared;

  Future<Map<String, dynamic>> libraryFiles(
    String path,
    String? project,
  ) async {
    paths.add(path);
    if (path == '') {
      return {
        'current_path': '',
        'items': [
          {'name': 'src', 'path': 'src', 'is_dir': true},
          {
            'name': 'README.md',
            'path': 'README.md',
            'is_dir': false,
            'size': 20,
          },
        ],
      };
    }
    return {
      'current_path': path,
      'items': [
        {'name': 'main.dart', 'path': '$path/main.dart', 'is_dir': false},
      ],
    };
  }

  Future<Map<String, dynamic>> shareLibraryFile(
    String path,
    String? project,
  ) async {
    shared = path;
    return {'path': '/shared/$path', 'name': path.split('/').last, 'size': 20};
  }
}

void main() {
  testWidgets('browses folders, shows breadcrumbs, and shares a file', (
    tester,
  ) async {
    final api = FakeFilesApi();
    await tester.pumpWidget(
      MaterialApp(
        home: FilesScreen(api: api, project: '/work/demo'),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('src'), findsOneWidget);
    expect(find.text('README.md'), findsOneWidget);
    await tester.tap(find.text('src'));
    await tester.pumpAndSettle();
    expect(api.paths, ['', 'src']);
    expect(find.text('main.dart'), findsOneWidget);
    expect(find.text('src'), findsOneWidget);

    await tester.tap(find.byTooltip('Parent folder'));
    await tester.pumpAndSettle();
    expect(find.text('README.md'), findsOneWidget);

    await tester.tap(find.byTooltip('Share file').first);
    await tester.pumpAndSettle();
    expect(api.shared, 'README.md');
    expect(find.text('Ready to open on phone'), findsOneWidget);
    expect(find.text('README.md'), findsWidgets);
  });

  testWidgets('shows retry for a folder-listing failure', (tester) async {
    await tester.pumpWidget(
      MaterialApp(home: FilesScreen(api: _BrokenFilesApi())),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('Could not load this folder'), findsOneWidget);
    expect(find.text('Retry'), findsOneWidget);
  });
}

class _BrokenFilesApi {
  Future<Map<String, dynamic>> libraryFiles(
    String path,
    String? project,
  ) async {
    throw StateError('offline');
  }
}
