import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/widgets/chat_composer.dart';

void main() {
  for (final width in [320.0, 360.0, 412.0]) {
    testWidgets('composer fits ${width.toInt()}dp with draft, reply and tray', (
      tester,
    ) async {
      tester.view.devicePixelRatio = 1;
      tester.view.physicalSize = Size(width, 800);
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);

      final controller = TextEditingController(
        text: List.filled(80, 'A long draft still stays editable').join(' '),
      );
      final focus = FocusNode();
      addTearDown(controller.dispose);
      addTearDown(focus.dispose);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: Column(
              children: [
                const Expanded(child: ColoredBox(color: Colors.black12)),
                ChatComposer(
                  controller: controller,
                  focusNode: focus,
                  sending: false,
                  dictating: false,
                  replyPreview:
                      'Replying to Gajala: ${List.filled(20, 'quoted text').join(' ')}',
                  attachmentPreview: const SizedBox(
                    width: 72,
                    height: 72,
                    child: ColoredBox(color: Colors.blueGrey),
                  ),
                  onCancelReply: () {},
                  onAddPhoto: () {},
                  onDictate: () {},
                  onSend: () {},
                ),
              ],
            ),
          ),
        ),
      );

      expect(find.byType(TextField), findsOneWidget);
      expect(tester.widget<TextField>(find.byType(TextField)).maxLines, 4);
      expect(find.byTooltip('Send'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('large text and keyboard inset keep composer actions reachable', (
    tester,
  ) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(320, 800);
    tester.view.viewInsets = const FakeViewPadding(bottom: 310);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetViewInsets);

    final controller = TextEditingController(
      text: 'Draft with several lines\n\nand a reply target',
    );
    final focus = FocusNode();
    addTearDown(controller.dispose);
    addTearDown(focus.dispose);
    await tester.pumpWidget(
      MaterialApp(
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(
            context,
          ).copyWith(textScaler: const TextScaler.linear(1.5)),
          child: child!,
        ),
        home: Scaffold(
          body: Column(
            children: [
              const Expanded(child: ColoredBox(color: Colors.black12)),
              ChatComposer(
                controller: controller,
                focusNode: focus,
                sending: true,
                dictating: false,
                replyPreview: 'Replying to you: Previous question',
                onCancelReply: () {},
                onDictate: () {},
                onSend: () {},
              ),
            ],
          ),
        ),
      ),
    );
    await tester.pump();

    expect(find.byTooltip('Send follow-up'), findsOneWidget);
    expect(find.text('Follow-up message…'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
