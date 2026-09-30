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

  test('nested holds pause once and resume only after the last release', () async {
    final calls = <String>[];
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(
      const MethodChannel('gajala/wakeword/control'),
      (call) async {
        calls.add(call.method);
        return null;
      },
    );
    await WakeWord.hold();     // voice sheet opens
    await WakeWord.hold();     // speech recognition starts
    await WakeWord.release();  // recognition ends
    expect(calls, ['pause']);
    await WakeWord.release();  // sheet closes
    await WakeWord.release();  // stray release is ignored
    expect(calls, ['pause', 'resume']);
  });
}
