import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/widgets/music_access_tile.dart';
import 'package:gajala/core/voice_logic.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const channel = MethodChannel('gajala/phone');
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  tearDown(() => messenger.setMockMethodCallHandler(channel, null));
  test('assistant name does not bypass local playback', () {
    expect(
      parseLocalIntent('Gajala, play rockstar songs'),
      const PlayMusic('rockstar songs'),
    );
    expect(
      parseLocalIntent('hey gajala, play rockstar songs'),
      const PlayMusic('rockstar songs'),
    );
  });
  testWidgets(
    'granted access with unreadable sessions never asks to grant again',
    (tester) async {
      final calls = <String>[];
      messenger.setMockMethodCallHandler(channel, (call) async {
        calls.add(call.method);
        return {
          'enabled': true,
          'sessionReadable': false,
          'message':
              'Access enabled; music sessions unavailable. No additional permission is needed.',
        };
      });
      await tester.pumpWidget(
        const MaterialApp(home: Scaffold(body: MusicAccessTile())),
      );
      await tester.pumpAndSettle();
      expect(
        find.text('YouTube Music playback access · Enabled'),
        findsOneWidget,
      );
      expect(find.textContaining('No additional permission'), findsOneWidget);
      expect(calls, ['musicAccess']);
      await tester.tap(find.byTooltip('Recheck playback access'));
      await tester.pumpAndSettle();
      expect(calls, ['musicAccess', 'musicAccess']);
    },
  );
  testWidgets('failed access check is unknown, not denied', (tester) async {
    messenger.setMockMethodCallHandler(
      channel,
      (_) async => throw PlatformException(code: 'unavailable'),
    );
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: MusicAccessTile())),
    );
    await tester.pumpAndSettle();
    expect(
      find.textContaining('Permission may already be enabled'),
      findsOneWidget,
    );
  });
}
