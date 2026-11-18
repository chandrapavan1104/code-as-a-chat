import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/screens/personal_wake_screen.dart';

void main() {
  const personal = MethodChannel('gajala/wakeword/personal');
  const control = MethodChannel('gajala/wakeword/control');
  final sample = List<double>.filled(480, 0.08);

  testWidgets('three usable recordings save, with only a bad clip repeated', (
    tester,
  ) async {
    var captures = 0;
    List<Object?>? saved;
    final messaging = tester.binding.defaultBinaryMessenger;
    messaging.setMockMethodCallHandler(control, (_) async => null);
    messaging.setMockMethodCallHandler(personal, (call) async {
      switch (call.method) {
        case 'examples':
          return [];
        case 'capture':
          if (++captures == 2)
            throw PlatformException(
              code: 'sample',
              message: 'Try this clip again.',
            );
          return sample;
        case 'save':
          saved = List<Object?>.from(call.arguments as List);
          return true;
        case 'match':
          return true;
      }
      fail('Unexpected method: ${call.method}');
    });
    await tester.pumpWidget(const MaterialApp(home: PersonalWakeScreen()));
    await tester.pumpAndSettle();
    for (var i = 0; i < 4; i++) {
      await tester.tap(find.text('Record “Gajala”'));
      await tester.pumpAndSettle();
      if (i == 1) {
        expect(find.text('Try this clip again.'), findsOneWidget);
        expect(find.text('Recording 2 of 3'), findsOneWidget);
      }
      if (i < 3) expect(saved, isNull);
    }
    expect(saved, hasLength(3));
    await tester.ensureVisible(find.text('Try it now'));
    await tester.tap(find.text('Try it now'));
    await tester.pumpAndSettle();
    expect(find.text('Heard “Gajala.”'), findsOneWidget);
    expect(saved, hasLength(3));
  });

  testWidgets('cancelled retraining keeps the saved profile', (tester) async {
    var saves = 0;
    final messaging = tester.binding.defaultBinaryMessenger;
    messaging.setMockMethodCallHandler(control, (_) async => null);
    messaging.setMockMethodCallHandler(personal, (call) async {
      switch (call.method) {
        case 'examples':
          return [sample, sample, sample];
        case 'capture':
          return sample;
        case 'save':
          saves++;
          return true;
      }
      fail('Unexpected method: ${call.method}');
    });
    await tester.pumpWidget(const MaterialApp(home: PersonalWakeScreen()));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Retrain'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Record “Gajala”'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Cancel retraining'));
    await tester.pumpAndSettle();
    expect(saves, 0);
    expect(find.text('Previous profile kept.'), findsOneWidget);
    expect(find.text('Add an example'), findsOneWidget);
  });
}
