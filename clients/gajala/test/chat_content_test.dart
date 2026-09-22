import 'package:flutter_test/flutter_test.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:gajala/widgets/chat_content.dart';

void main() {
  test('parses selectable text, fenced code, and file markers', () {
    final blocks = parseChatContent(
      'Before\n\n```python\nprint("hi")\n```\n[file: /tmp/report.md]\nAfter',
    );
    expect(blocks, hasLength(5));
    expect((blocks[0] as TextBlock).text, contains('Before'));
    expect((blocks[1] as CodeBlock).language, 'python');
    expect((blocks[1] as CodeBlock).code, contains('print'));
    expect((blocks[2] as TextBlock).text.trim(), isEmpty);
    expect((blocks[3] as FileBlock).filename, 'report.md');
    expect((blocks[4] as TextBlock).text, contains('After'));
  });

  test(
    'file names are safe display labels and invalid markers are ignored',
    () {
      expect(fileDisplayName('/Users/me/My File.txt'), 'My File.txt');
      final blocks = parseChatContent(
        '[file: /tmp/ok.txt] [file: nope] [file: /tmp/x\u0000bad]',
      );
      expect(blocks.whereType<FileBlock>().map((b) => b.path), ['/tmp/ok.txt']);
    },
  );

  testWidgets('code block exposes selectable text and copy action', (
    tester,
  ) async {
    String? copied;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, (call) async {
          if (call.method == 'Clipboard.setData') {
            copied = (call.arguments as Map)['text']?.toString();
          }
          return null;
        });
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(body: ChatContent(text: '```dart\nfinal x = 1;\n```')),
      ),
    );
    expect(find.text('DART'), findsOneWidget);
    expect(find.byTooltip('Copy code'), findsOneWidget);
    expect(find.byType(SelectableText), findsOneWidget);
    await tester.tap(find.byTooltip('Copy code'));
    await tester.pump();
    expect(copied, 'final x = 1;\n');
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(SystemChannels.platform, null);
  });
}
