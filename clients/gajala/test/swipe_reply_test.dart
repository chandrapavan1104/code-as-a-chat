import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/widgets/swipe_reply.dart';

void main() {
  testWidgets('right swipe crosses threshold, replies, and springs back', (
    tester,
  ) async {
    var replies = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SwipeReply(
            key: const ValueKey('message'),
            onReply: () => replies++,
            child: const SizedBox(
              width: 300,
              height: 80,
              child: Text('message body'),
            ),
          ),
        ),
      ),
    );

    await tester.drag(
      find.byKey(const ValueKey('message')),
      const Offset(88, 0),
    );
    expect(replies, 1);
    await tester.pumpAndSettle();
    expect(tester.getTopLeft(find.text('message body')).dx, 0);
  });

  testWidgets('short right drags and left drags do not reply', (tester) async {
    var replies = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SwipeReply(
            onReply: () => replies++,
            child: const SizedBox(width: 300, height: 80),
          ),
        ),
      ),
    );

    await tester.drag(find.byType(SwipeReply), const Offset(30, 0));
    await tester.drag(find.byType(SwipeReply), const Offset(-100, 0));
    expect(replies, 0);
  });

  testWidgets('long press remains available to SelectableText', (tester) async {
    var replies = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SwipeReply(
            onReply: () => replies++,
            child: const SelectableText('select this message text'),
          ),
        ),
      ),
    );

    await tester.longPress(find.text('select this message text'));
    expect(replies, 0);
    expect(find.byType(SelectableText), findsOneWidget);
  });

  testWidgets('vertical gesture remains available to the chat list', (
    tester,
  ) async {
    var replies = 0;
    final scroll = ScrollController();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ListView(
            controller: scroll,
            children: [
              for (var i = 0; i < 8; i++)
                SwipeReply(
                  onReply: () => replies++,
                  child: SizedBox(
                    key: ValueKey('row-$i'),
                    height: 100,
                    child: Text('message $i'),
                  ),
                ),
            ],
          ),
        ),
      ),
    );

    await tester.drag(find.text('message 3'), const Offset(0, -160));
    await tester.pumpAndSettle();
    expect(scroll.offset, greaterThan(0));
    expect(replies, 0);
  });

  testWidgets('exposes Reply as a semantic custom action', (tester) async {
    final semantics = tester.ensureSemantics();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SwipeReply(
            key: const ValueKey('semantic-message'),
            onReply: () {},
            child: const Text('message'),
          ),
        ),
      ),
    );

    expect(
      tester.getSemantics(find.byKey(const ValueKey('semantic-message'))),
      matchesSemantics(
        label: 'message',
        customActions: const [CustomSemanticsAction(label: 'Reply')],
      ),
    );
    semantics.dispose();
  });

  testWidgets('excluded code surface keeps horizontal scrolling', (
    tester,
  ) async {
    var replies = 0;
    final horizontal = ScrollController();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SwipeReply(
            swipeEnabled: false,
            onReply: () => replies++,
            child: SizedBox(
              width: 180,
              height: 70,
              child: SingleChildScrollView(
                controller: horizontal,
                scrollDirection: Axis.horizontal,
                child: const SizedBox(
                  width: 600,
                  child: Text('wide code line'),
                ),
              ),
            ),
          ),
        ),
      ),
    );

    final viewport = find.byType(SingleChildScrollView);
    await tester.dragFrom(tester.getCenter(viewport), const Offset(-120, 0));
    await tester.pumpAndSettle();
    expect(horizontal.offset, greaterThan(0));
    expect(replies, 0);
  });
}
