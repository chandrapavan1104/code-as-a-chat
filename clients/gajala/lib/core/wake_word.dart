// "Hey Gajala" wake word.
//
// Detection runs in WakeWordService (Android), which owns the microphone as a
// foreground service and a small headless Flutter engine that executes
// [wakeWordMain]. The app's own engine dies when the app is swiped away; this
// one lives as long as the service, so hands-free keeps working.
//
// Audio never leaves the phone and is never stored: 100 ms PCM frames go from
// the service straight into the on-device keyword spotter and are dropped.

import 'dart:typed_data';
import 'package:flutter/services.dart';
import 'package:flutter/widgets.dart';
import 'package:sherpa_onnx/sherpa_onnx.dart' as sherpa;

/// Little-endian 16-bit PCM → floats in [-1, 1), as the spotter expects.
Float32List pcm16ToFloat32(ByteData bytes) {
  final n = bytes.lengthInBytes ~/ 2;
  final out = Float32List(n);
  for (var i = 0; i < n; i++) {
    out[i] = bytes.getInt16(i * 2, Endian.little) / 32768.0;
  }
  return out;
}

// Tuned with synthetic voices; see android/app/src/main/assets/wakeword/README.md.
const _threshold = 0.3;
const _boost = 1.5;
const _trailingBlanks = 3;

/// Entry point of the service's headless engine. Not called by the app.
@pragma('vm:entry-point')
void wakeWordMain() {
  WidgetsFlutterBinding.ensureInitialized();
  const control = MethodChannel('gajala/wakeword/engine');
  const audio = BasicMessageChannel<ByteData?>('gajala/wakeword/audio', BinaryCodec());
  sherpa.KeywordSpotter? spotter;
  sherpa.OnlineStream? stream;

  control.setMethodCallHandler((call) async {
    if (call.method != 'configure') return null;
    final dir = (call.arguments as Map)['dir'] as String;
    sherpa.initBindings();
    spotter = sherpa.KeywordSpotter(sherpa.KeywordSpotterConfig(
      model: sherpa.OnlineModelConfig(
        transducer: sherpa.OnlineTransducerModelConfig(
          encoder: '$dir/encoder.int8.onnx',
          decoder: '$dir/decoder.onnx',
          joiner: '$dir/joiner.int8.onnx',
        ),
        tokens: '$dir/tokens.txt',
        numThreads: 1,
        debug: false,
      ),
      keywordsFile: '$dir/keywords.txt',
      keywordsThreshold: _threshold,
      keywordsScore: _boost,
      numTrailingBlanks: _trailingBlanks,
    ));
    stream = spotter!.createStream();
    return true;
  });

  audio.setMessageHandler((frame) async {
    final kws = spotter, s = stream;
    if (frame == null || kws == null || s == null) return null;
    s.acceptWaveform(samples: pcm16ToFloat32(frame), sampleRate: 16000);
    while (kws.isReady(s)) {
      kws.decode(s);
      if (kws.getResult(s).keyword.isNotEmpty) {
        kws.reset(s);
        await control.invokeMethod('detected');
      }
    }
    return null;
  });

  control.invokeMethod('ready');
}

enum WakeWordState { unsupported, off, listening, paused }

/// App-side switch for the wake word, backed by MainActivity.
class WakeWord {
  static const _channel = MethodChannel('gajala/wakeword/control');
  static int _holds = 0;

  static Future<WakeWordState> state() async {
    try {
      final s = await _channel.invokeMethod<String>('state');
      return WakeWordState.values.firstWhere((e) => e.name == s,
          orElse: () => WakeWordState.off);
    } on MissingPluginException {
      return WakeWordState.unsupported;
    }
  }

  /// Turns hands-free on (asking for mic + notification permission first).
  /// Returns null on success, or why it could not start.
  static Future<String?> enable() async {
    try {
      return await _channel.invokeMethod<String>('enable');
    } on PlatformException catch (e) {
      return e.message ?? 'Could not start listening.';
    }
  }

  static Future<void> disable() async {
    try {
      await _channel.invokeMethod('disable');
    } on MissingPluginException {/* not Android */}
  }

  /// Release the mic while Gajala itself listens or speaks, so it neither
  /// competes with speech recognition nor hears its own voice. Every [hold]
  /// must be paired with a [release].
  static Future<void> hold() async {
    if (_holds++ == 0) await _call('pause');
  }

  static Future<void> release() async {
    if (_holds == 0) return;
    if (--_holds == 0) await _call('resume');
  }

  static Future<void> _call(String method) async {
    try {
      await _channel.invokeMethod(method);
    } on MissingPluginException {/* not Android */}
  }
}
