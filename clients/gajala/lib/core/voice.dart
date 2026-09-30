// Speech in and out, phone-native actions, and the Android assistant entry.
//
// One process-wide instance: Android allows a single active recognizer, and the
// chat's dictation mic and the voice sheet must not fight over it.

import 'dart:async';
import 'package:android_intent_plus/android_intent.dart';
import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:speech_to_text/speech_to_text.dart';
import 'storage.dart';
import 'voice_logic.dart';
import 'wake_word.dart';

class Voice {
  Voice._();
  static final instance = Voice._();

  final _stt = SpeechToText();
  final _tts = FlutterTts();
  bool _sttReady = false;
  bool _ttsReady = false;
  Completer<String>? _pending;
  void Function(String)? _onPartial;
  String _latest = '';
  bool _holdingMic = false;

  bool get isListening => _stt.isListening;

  Future<bool> _ensureStt() async {
    if (_sttReady) return true;
    _sttReady = await _stt.initialize(
      onError: (e) => _finish(error: e.errorMsg),
      // Not notListeningStatus: Android reports that at end-of-speech, before
      // the final transcript arrives, which would cut off the last words.
      onStatus: (s) {
        if (s == SpeechToText.doneStatus) _finish();
      },
    );
    return _sttReady;
  }

  /// Listen for one utterance. Completes with the final transcript ('' when
  /// nothing was heard). Throws [VoiceUnavailable] when there is no mic
  /// permission or recognizer.
  Future<String> listen({void Function(String partial)? onPartial}) async {
    await stopSpeaking();
    if (!await _ensureStt()) {
      throw const VoiceUnavailable(
        'Speech recognition is unavailable. Allow the microphone for Gajala '
        'and make sure a voice-input app (e.g. Google) is installed.',
      );
    }
    _finish();
    final done = Completer<String>();
    _pending = done;
    _holdingMic = true;
    await WakeWord.hold();
    _onPartial = onPartial;
    _latest = '';
    await _stt.listen(
      onResult: (r) {
        _latest = r.recognizedWords;
        _onPartial?.call(_latest);
        if (r.finalResult) _finish();
      },
      listenOptions: SpeechListenOptions(
        partialResults: true,
        cancelOnError: true,
        listenMode: ListenMode.dictation,
        pauseFor: const Duration(seconds: 3),
        listenFor: const Duration(seconds: 60),
      ),
    );
    return done.future;
  }

  void _finish({String? error}) {
    if (_holdingMic) {
      _holdingMic = false;
      WakeWord.release();
    }
    final p = _pending;
    if (p == null || p.isCompleted) return;
    _pending = null;
    _onPartial = null;
    // "no match" / speech timeout just mean silence — not worth an error.
    if (error != null && _latest.isEmpty &&
        !error.contains('no_match') && !error.contains('speech_timeout')) {
      p.completeError(VoiceUnavailable('Didn\'t catch that ($error).'));
    } else {
      p.complete(_latest.trim());
    }
  }

  /// End listening now and use what was heard so far.
  Future<void> stopListening() async {
    await _stt.stop();
    _finish();
  }

  Future<void> cancelListening() async {
    _latest = '';
    await _stt.cancel();
    _finish();
  }

  Future<void> speak(String reply) async {
    final text = speakable(reply);
    if (text.isEmpty) return;
    if (!_ttsReady) {
      await _tts.awaitSpeakCompletion(true);
      _ttsReady = true;
    }
    await _tts.stop();
    // Don't let the wake word hear Gajala saying its own name.
    await WakeWord.hold();
    try {
      await _tts.speak(text);
    } finally {
      await WakeWord.release();
    }
  }

  Future<void> stopSpeaking() async {
    if (_ttsReady) await _tts.stop();
  }

  Future<bool> speakReplies() => Storage.speakReplies();
  Future<void> setSpeakReplies(bool on) => Storage.setSpeakReplies(on);
}

class VoiceUnavailable implements Exception {
  final String message;
  const VoiceUnavailable(this.message);
  @override
  String toString() => message;
}

/// Run a phone-native request as an Android intent. Returns what to say back,
/// or throws when no app on the phone can handle it.
Future<String> runLocalIntent(LocalIntent intent) async {
  const newTask = 0x10000000; // FLAG_ACTIVITY_NEW_TASK
  switch (intent) {
    case SetAlarm(:final hour, :final minute):
      await AndroidIntent(
        action: 'android.intent.action.SET_ALARM',
        arguments: {
          'android.intent.extra.alarm.HOUR': hour,
          'android.intent.extra.alarm.MINUTES': minute,
          'android.intent.extra.alarm.SKIP_UI': true,
          'android.intent.extra.alarm.MESSAGE': 'Gajala',
        },
        flags: const [newTask],
      ).launch();
    case SetTimer(:final seconds):
      await AndroidIntent(
        action: 'android.intent.action.SET_TIMER',
        arguments: {
          'android.intent.extra.alarm.LENGTH': seconds,
          'android.intent.extra.alarm.SKIP_UI': true,
          'android.intent.extra.alarm.MESSAGE': 'Gajala',
        },
        flags: const [newTask],
      ).launch();
    case DialNumber(:final number):
      await AndroidIntent(
        action: 'android.intent.action.DIAL',
        data: 'tel:$number',
        flags: const [newTask],
      ).launch();
    case Navigate(:final destination):
      await AndroidIntent(
        action: 'android.intent.action.VIEW',
        data: 'geo:0,0?q=${Uri.encodeComponent(destination)}',
        flags: const [newTask],
      ).launch();
    case WebSearch(:final query):
      await AndroidIntent(
        action: 'android.intent.action.WEB_SEARCH',
        arguments: {'query': query},
        flags: const [newTask],
      ).launch();
    case AskGoogle(:final query):
      await _askGoogle(query);
  }
  return intent.confirmation;
}

/// Prefer the Google app (it executes commands like "call mom"); fall back to
/// any web-search handler if it's missing.
Future<void> _askGoogle(String query) async {
  const newTask = 0x10000000;
  try {
    await AndroidIntent(
      action: 'android.intent.action.WEB_SEARCH',
      package: 'com.google.android.googlequicksearchbox',
      arguments: {'query': query},
      flags: const [newTask],
    ).launch();
  } on PlatformException {
    await AndroidIntent(
      action: 'android.intent.action.WEB_SEARCH',
      arguments: {'query': query},
      flags: const [newTask],
    ).launch();
  }
}

/// Opens Android's default-apps screen so the user can pick Gajala as the
/// digital assistant. Android does not let an app claim that role itself.
Future<void> openAssistantSettings() async {
  const newTask = 0x10000000;
  try {
    await const AndroidIntent(
      action: 'android.settings.MANAGE_DEFAULT_APPS_SETTINGS',
      flags: [newTask],
    ).launch();
  } on PlatformException {
    await const AndroidIntent(
      action: 'android.settings.VOICE_INPUT_SETTINGS',
      flags: [newTask],
    ).launch();
  }
}

/// Bridge to MainActivity: was the app opened by the assistant gesture
/// (long-press home / assistant button), and later re-opened that way?
class AssistLaunch {
  static const _channel = MethodChannel('gajala/assist');

  static Future<bool> consumeInitial() async {
    try {
      return await _channel.invokeMethod<bool>('consumeLaunch') ?? false;
    } on MissingPluginException {
      return false;
    }
  }

  static void listen(void Function() onAssist) {
    _channel.setMethodCallHandler((call) async {
      if (call.method == 'assist') onAssist();
    });
  }
}
