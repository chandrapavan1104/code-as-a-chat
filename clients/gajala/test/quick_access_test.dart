import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/widgets/quick_access.dart';

void main() {
  testWidgets('Quick menu fits narrow screens and preserves draft on dismiss', (tester) async {
    await tester.binding.setSurfaceSize(const Size(320, 640));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    final draft = TextEditingController(text: 'Keep this draft');
    addTearDown(draft.dispose);
    await tester.pumpWidget(MaterialApp(home: MediaQuery(
      data: const MediaQueryData(size: Size(320, 640), textScaler: TextScaler.linear(1.5)),
      child: Scaffold(appBar: AppBar(title: const Text('Chats'), actions: const [QuickAccessButton()]),
        body: TextField(controller: draft)),
    )));
    await tester.tap(find.byTooltip('Quick access'));
    await tester.pumpAndSettle();
    expect(find.text('Terminal'), findsOneWidget);
    expect(find.text('Trading'), findsOneWidget);
    expect(tester.takeException(), isNull);
    Navigator.of(tester.element(find.text('Terminal'))).pop();
    await tester.pumpAndSettle();
    expect(find.text('Keep this draft'), findsOneWidget);
  });
}
