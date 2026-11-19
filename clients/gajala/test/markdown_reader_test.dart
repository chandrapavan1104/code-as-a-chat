import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/widgets/chat_content.dart';
import 'package:gajala/widgets/markdown_reader.dart';

class _FileApi extends GajalaApi {
  _FileApi(this.content) : super('http://localhost', 'test');
  final String content;
  String? downloadUrl;
  String? savedPath;

  @override
  Future<void> downloadFile(
    String url,
    String path,
    void Function(double) onProgress,
  ) async {
    downloadUrl = url;
    savedPath = path;
    File(path).writeAsStringSync(content);
    onProgress(1);
  }
}

void main() {
  testWidgets(
    'reading view scrolls wide blocks on a small dark screen and copies exact source',
    (tester) async {
      await tester.binding.setSurfaceSize(const Size(320, 600));
      addTearDown(() => tester.binding.setSurfaceSize(null));
      final long = 'long_column_' * 20;
      final source =
          '# Worker report\n\n| Result | Target | Status |\n'
          '| --- | --- | --- |\n| $long | $long | Ready |\n\n'
          '~~~dart\n$long\n~~~\n\n![Example chart](https://example.invalid/chart.png)';
      String? copied;
      final messenger = tester.binding.defaultBinaryMessenger;
      messenger.setMockMethodCallHandler(SystemChannels.platform, (call) async {
        if (call.method == 'Clipboard.setData') {
          copied = (call.arguments as Map)['text'] as String;
        }
        return null;
      });
      addTearDown(
        () => messenger.setMockMethodCallHandler(SystemChannels.platform, null),
      );

      await tester.pumpWidget(
        MaterialApp(
          theme: ThemeData.dark(),
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(
              context,
            ).copyWith(textScaler: const TextScaler.linear(1.5)),
            child: child!,
          ),
          home: Scaffold(
            body: ListView(children: [MarkdownReader(source: source)]),
          ),
        ),
      );
      final horizontal = find.byWidgetPredicate(
        (widget) =>
            widget is SingleChildScrollView &&
            widget.scrollDirection == Axis.horizontal,
      );
      expect(horizontal, findsNWidgets(2));
      expect(find.text('Example chart', findRichText: true), findsOneWidget);
      expect(find.byType(Image), findsNothing);
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('Copy Markdown'));
      await tester.pump();
      expect(copied, source);
      await tester.tap(find.text('View source'));
      await tester.pump();
      expect(find.text(source), findsOneWidget);
    },
  );

  testWidgets(
    'downloaded Markdown opens in reading view with a separate truncation notice',
    (tester) async {
      final directory = Directory.systemTemp.createTempSync(
        'gajala-markdown-test',
      );
      addTearDown(() => directory.deleteSync(recursive: true));
      const channel = MethodChannel('plugins.flutter.io/path_provider');
      final messenger = tester.binding.defaultBinaryMessenger;
      messenger.setMockMethodCallHandler(channel, (_) async => directory.path);
      addTearDown(() => messenger.setMockMethodCallHandler(channel, null));
      final api = _FileApi(
        '# Downloaded memo\n\n~~~text\n${'x' * (128 * 1024)}',
      );
      const path = '/uploads/sample.MARKDOWN';
      await tester.pumpWidget(
        MaterialApp(
          home: FileAttachmentPreview(path: path, api: api),
        ),
      );
      await tester.runAsync(() async {
        await tester.tap(find.text('Download'));
        for (var i = 0; i < 25; i++) {
          await tester.pump();
          if (find.byType(MarkdownReader).evaluate().isNotEmpty) break;
          await Future<void>.delayed(const Duration(milliseconds: 10));
        }
      });
      await tester.pumpAndSettle();
      expect(api.downloadUrl, api.fileUrl(path));
      expect(File(api.savedPath!).readAsStringSync(), api.content);
      expect(find.text('Downloaded memo', findRichText: true), findsOneWidget);
      expect(find.textContaining('Preview limited to 128 KB'), findsOneWidget);
      expect(find.text('Open in another app'), findsOneWidget);
      expect(find.text('Copy preview Markdown'), findsOneWidget);
    },
  );
}
