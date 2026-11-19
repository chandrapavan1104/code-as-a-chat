// Speech in and out, phone-native actions, and the Android assistant entry.
//
// One process-wide instance: Android allows a single active recognizer, and the
// chat's dictation mic and the voice sheet must not fight over it.

import 'dart:async';
import 'package:android_intent_plus/android_intent.dart';
import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:speech_to_text/speech_to_text.dart';
import 'device_actions.dart';
import 'storage.dart';
import 'voice_logic.dart';
import 'wake_word.dart';
import 'voice_preferences.dart';

class Voice {
  Voice._();
  static final instance = Voice._();

  final _stt = SpeechToText();
  final _tts = FlutterTts();
  late final preferences = VoicePreferences(_tts);
  String? _localeId;
  bool _sttReady = false;
  bool _ttsReady = false;
  Completer<String>? _pending;
  void Function(String)? _onPartial;
  String _latest = '';
  bool _holdingMic = false;
  bool _requireFinal = false;

  int _listenGeneration = 0;

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
    if (_sttReady) {
      final locales = await _stt.locales();
      for (final locale in locales) {
        if (locale.localeId.replaceAll('_', '-').toLowerCase() == 'en-in') {
          _localeId = locale.localeId;
          break;
        }
      }
    }
    return _sttReady;
  }

  /// The recognizer can end an attempt early. Keep the user's first-speech
  /// window independent of its silence timeout.
  Future<String> listenForCommand(
    Duration window, {
    void Function(String)? onPartial,
    bool confirmation = false,
  }) async {
    final generation = ++_listenGeneration;
    try {
      await SystemSound.play(SystemSoundType.click);
    } on PlatformException {
      // The tone is optional on phones that do not expose system sounds.
    }
    return listenWithinWindow(
      window,
      active: () => generation == _listenGeneration,
      confirmation: confirmation,
      attempt: (remaining) => listen(
        onPartial: onPartial,
        initialSilence: remaining,
        requireFinal: confirmation,
      ),
    );
  }

  /// Listen for one utterance. Completes with the final transcript ('' when
  /// nothing was heard). Throws [VoiceUnavailable] when there is no mic
  /// permission or recognizer.
  Future<String> listen({
    void Function(String partial)? onPartial,
    Duration? initialSilence,
    bool requireFinal = false,
  }) async {
    final generation = _listenGeneration;
    await stopSpeaking();
    if (!await _ensureStt()) {
      throw const VoiceUnavailable(
        'Speech recognition is unavailable. Allow the microphone for Gajala '
        'and make sure a voice-input app (e.g. Google) is installed.',
      );
    }
    if (generation != _listenGeneration) return '';
    _finish();
    final done = Completer<String>();
    _pending = done;
    _holdingMic = true;
    await WakeWord.hold();
    if (generation != _listenGeneration || !identical(_pending, done))
      return '';
    _onPartial = onPartial;
    _requireFinal = requireFinal;
    _latest = '';
    final silenceTimer = initialSilence == null
        ? null
        : Timer(initialSilence, () {
            if (identical(_pending, done) &&
                (requireFinal || _latest.trim().isEmpty)) {
              unawaited(_cancelAttempt());
            }
          });
    try {
      await _stt.listen(
        localeId: _localeId,
        onResult: (r) {
          if (!identical(_pending, done)) return;
          _latest = r.recognizedWords;
          if (!requireFinal && _latest.trim().isNotEmpty)
            silenceTimer?.cancel();
          _onPartial?.call(_latest);
          if (r.finalResult) _finish(finalResult: true);
        },
        listenOptions: SpeechListenOptions(
          partialResults: true,
          cancelOnError: true,
          listenMode: ListenMode.dictation,
          pauseFor: const Duration(seconds: 3),
          listenFor: const Duration(seconds: 60),
        ),
      );
    } catch (_) {
      silenceTimer?.cancel();
      _latest = '';
      _finish();
      rethrow;
    }
    // A plugin/recognizer must not leave a pending confirmation alive forever.
    return done.future
        .timeout(
          const Duration(seconds: 65),
          onTimeout: () {
            if (identical(_pending, done)) unawaited(_cancelAttempt());
            return '';
          },
        )
        .whenComplete(() => silenceTimer?.cancel());
  }

  void _finish({String? error, bool finalResult = false}) {
    if (_holdingMic) {
      _holdingMic = false;
      WakeWord.release();
    }
    final p = _pending;
    if (p == null || p.isCompleted) return;
    _pending = null;
    _onPartial = null;
    // "no match" / speech timeout just mean silence — not worth an error.
    if (error != null &&
        _latest.isEmpty &&
        !error.contains('no_match') &&
        !error.contains('speech_timeout')) {
      p.completeError(VoiceUnavailable('Didn\'t catch that ($error).'));
    } else {
      p.complete(_requireFinal && !finalResult ? '' : _latest.trim());
    }
  }

  /// End listening now and use what was heard so far.
  Future<void> stopListening() async {
    await _stt.stop();
    _finish();
  }

  Future<void> cancelListening() {
    _listenGeneration++;
    return _cancelAttempt();
  }

  Future<void> _cancelAttempt() async {
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
    if (!await preferences.applySaved()) return;
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

/// An early empty attempt is silence, not the end of the user's window.
Future<String> listenWithinWindow(
  Duration window, {
  required bool Function() active,
  required Future<String> Function(Duration remaining) attempt,
  bool confirmation = false,
  Duration retryDelay = const Duration(milliseconds: 220),
}) async {
  final clock = Stopwatch()..start();
  while (active() && clock.elapsed < window) {
    final heard = await attempt(window - clock.elapsed);
    if (!active()) return '';
    if (heard.isNotEmpty) {
      return confirmation && clock.elapsed > window ? '' : heard;
    }
    final remaining = window - clock.elapsed;
    if (remaining > Duration.zero) {
      await Future<void>.delayed(
        remaining < retryDelay ? remaining : retryDelay,
      );
    }
  }
  return '';
}

class EverydayVoice {
  static const channel = MethodChannel('gajala/voice/phone');

  static Future<void> settings() => channel.invokeMethod<void>('setup');

  static Future<bool> canUseUnlocked() async {
    try {
      return await channel.invokeMethod<bool>('unlocked') == true;
    } on MissingPluginException {
      return true;
    } on PlatformException {
      return false;
    }
  }

  static Future<String?> execute(String text) async {
    try {
      return await channel.invokeMethod<String>('execute', text);
    } on MissingPluginException {
      return null;
    }
  }
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
  if (intent is DialNumber || intent is CallContact || intent is PlayMusic) {
    throw const VoiceUnavailable(
      'Use voice mode for confirmed calling and music playback.',
    );
  }
  final action = intent.deviceAction;
  if (action != null) {
    final r = await DeviceActions.instance.run(action.command, action.args);
    if (r['ok'] == true) {
      return (r['data'] as Map?)?['done']?.toString() ?? intent.confirmation;
    }
    throw PlatformException(code: 'device', message: r['error']?.toString());
  }
  const newTask = 0x10000000; // FLAG_ACTIVITY_NEW_TASK
  switch (intent) {
    case CallContact():
    case PlayMusic():
      throw const VoiceUnavailable('Use voice mode for this action.');
    case DialNumber(:final number):
      await AndroidIntent(
        action: 'android.intent.action.DIAL',
        data: 'tel:$number',
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
    // Performed by DeviceActions above.
    case SetAlarm() || SetTimer() || Navigate() || PhoneAction():
      break;
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
