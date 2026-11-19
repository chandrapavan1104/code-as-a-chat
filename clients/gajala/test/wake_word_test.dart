import 'dart:typed_data';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/wake_word.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  test('16-bit little-endian PCM converts to [-1, 1) floats', () {
    final b = ByteData(8)
      ..setInt16(0, 0, Endian.little)
      ..setInt16(2, 16384, Endian.little)
      ..setInt16(4, -32768, Endian.little)
      ..setInt16(6, 32767, Endian.little);
    expect(pcm16ToFloat32(b), [0.0, 0.5, -1.0, closeTo(0.99997, 1e-5)]);
    expect(pcm16ToFloat32(ByteData(3)), hasLength(1));
  });

  test(
    'nested holds pause once and resume only after the last release',
    () async {
      final calls = <String>[];
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(
            const MethodChannel('gajala/wakeword/control'),
            (call) async {
              calls.add(call.method);
              return null;
            },
          );
      await WakeWord.hold(); // voice sheet opens
      await WakeWord.hold(); // speech recognition starts
      await WakeWord.release(); // recognition ends
      expect(calls, ['pause']);
      await WakeWord.release(); // sheet closes
      await WakeWord.release(); // stray release is ignored
      expect(calls, ['pause', 'resume']);
    },
  );

  List<bool> trial(
    int good, {
    bool falseWake = false,
    bool missedFresh = false,
  }) => [
    for (var i = 0; i < 8; i++) i < good,
    falseWake,
    false,
    false,
    false,
    true,
    true,
    !missedFresh,
  ];

  test(
    'calibration chooses the least sensitive profile passing all 15 clips',
    () {
      final chosen = chooseWakeCalibration([
        trial(6),
        trial(7),
        trial(8),
        trial(8, falseWake: true),
      ]);
      expect(chosen?.threshold, 0.25);
      expect(chosen?.boost, 1.6);
    },
  );

  test(
    'calibration rejects false wakes, failed fresh trials and old clip counts',
    () {
      expect(
        chooseWakeCalibration([
          trial(8, falseWake: true),
          trial(8, missedFresh: true),
          trial(6),
          List.filled(8, true),
        ]),
        isNull,
      );
    },
  );
}
