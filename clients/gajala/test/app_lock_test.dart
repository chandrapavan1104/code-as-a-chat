import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/app_lock.dart';
import 'package:gajala/core/theme.dart';

Widget _app() => MaterialApp(
      theme: buildTheme(Brightness.dark),
      home: const Text('secret chat'),
      builder: (context, child) => AppLockGate(child: child!),
    );

void main() {
  testWidgets('lock off: the app shows normally', (tester) async {
    FlutterSecureStorage.setMockInitialValues({});
    await tester.pumpWidget(_app());
    await tester.pump();
    expect(find.text('secret chat'), findsOneWidget);
    expect(find.text('Gajala is locked'), findsNothing);
  });

  testWidgets('lock on: content stays hidden until unlocked', (tester) async {
    FlutterSecureStorage.setMockInitialValues({'app_lock_enabled': 'true'});
    await tester.pumpWidget(_app());
    await tester.pump();
    await tester.pump();
    // No biometric hardware in tests, so authentication fails and it stays locked.
    expect(find.text('Gajala is locked'), findsOneWidget);
    expect(find.text('secret chat'), findsNothing);
  });
}
