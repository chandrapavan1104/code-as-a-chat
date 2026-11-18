import 'package:android_intent_plus/android_intent.dart';
import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';

import 'storage.dart';
import 'wake_word.dart';

/// A TTS voice as reported by the installed Android engine. Gender is only
/// shown when the engine explicitly supplies it; Android commonly omits it.
class VoiceOption {
  final String name;
  final String locale;
  final String? gender;
  final int quality;
  final bool offline;

  const VoiceOption({
    required this.name,
    required this.locale,
    this.gender,
    this.quality = 0,
    this.offline = true,
  });

  factory VoiceOption.fromMap(Map<dynamic, dynamic> raw) => VoiceOption(
    name: raw['name']?.toString() ?? raw['voiceName']?.toString() ?? '',
    locale: raw['locale']?.toString() ?? raw['language']?.toString() ?? '',
    gender: _explicitGender(raw['gender']),
    quality: const [
      'very low',
      'low',
      'normal',
      'high',
      'very high',
    ].indexOf(raw['quality']?.toString().toLowerCase() ?? ''),
    offline:
        !{
          '1',
          'true',
        }.contains(raw['network_required']?.toString().toLowerCase()) &&
        !(raw['features']?.toString().contains('notInstalled') ?? false),
  );

  static String? _explicitGender(Object? value) {
    final text = value?.toString().trim().toLowerCase();
    if (text == 'male' || text == 'female' || text == 'neutral') return text;
    return null;
  }

  bool get isIndianEnglish =>
      locale.toLowerCase().replaceAll('_', '-') == 'en-in';

  String get label => !isIndianEnglish
      ? '$locale • $name'
      : gender == 'male'
      ? 'Indian English • $name • male'
      : 'Indian English • $name';

  @override
  bool operator ==(Object other) =>
      other is VoiceOption && other.name == name && other.locale == locale;
  @override
  int get hashCode => Object.hash(name, locale);
}

class VoicePreferences {
  final FlutterTts tts;
  const VoicePreferences(this.tts);

  static List<VoiceOption> indianVoicesFrom(Iterable<Object?> raw) =>
      raw
          .whereType<Map>()
          .map(VoiceOption.fromMap)
          .where(
            (voice) =>
                voice.name.isNotEmpty && voice.isIndianEnglish && voice.offline,
          )
          .toSet()
          .toList()
        ..sort((a, b) {
          final quality = b.quality.compareTo(a.quality);
          return quality == 0 ? a.name.compareTo(b.name) : quality;
        });

  /// Prefer an explicitly labelled male voice, otherwise preserve the
  /// engine's first stable Indian English voice for the owner to audition.
  static VoiceOption? chooseDefault(
    List<VoiceOption> voices, {
    String? savedName,
    String? savedLocale,
  }) {
    for (final voice in voices) {
      if (savedName == voice.name && savedLocale == voice.locale) return voice;
    }
    for (final voice in voices) {
      if (voice.gender == 'male') return voice;
    }
    return voices.isEmpty ? null : voices.first;
  }

  Future<List<VoiceOption>> _all() async {
    final raw = await tts.getVoices;
    if (raw is! Iterable) return const [];
    return raw
        .whereType<Map>()
        .map(VoiceOption.fromMap)
        .where((voice) => voice.offline && voice.name.isNotEmpty)
        .toSet()
        .toList();
  }

  Future<List<VoiceOption>> available() async {
    final all = await _all();
    final indian = all.where((voice) => voice.isIndianEnglish).toList()
      ..sort((a, b) => b.quality.compareTo(a.quality));
    return indian.isNotEmpty
          ? indian
          : all
                .where((voice) => voice.locale.toLowerCase().startsWith('en'))
                .toList()
      ..sort((a, b) => b.quality.compareTo(a.quality));
  }

  Future<VoiceOption?> selected(List<VoiceOption> voices) async {
    final saved = await Storage.voiceSelection();
    return chooseDefault(
      voices,
      savedName: saved.name,
      savedLocale: saved.locale,
    );
  }

  Future<void> apply(VoiceOption voice) async {
    await tts.setLanguage(voice.locale);
    final result = await tts.setVoice({
      'name': voice.name,
      'locale': voice.locale,
    });
    if (result != 1)
      throw StateError('Android could not select this offline voice.');
    await tts.setSpeechRate(0.47);
  }

  Future<bool> applySaved() async {
    final saved = await selected(await available());
    if (saved == null) return false;
    await apply(saved);
    return true;
  }

  Future<void> select(VoiceOption voice) async {
    await apply(voice);
    await Storage.setVoiceSelection(name: voice.name, locale: voice.locale);
    try {
      await const MethodChannel(
        'gajala/voice/phone',
      ).invokeMethod<void>('saveVoice', voice.name);
    } on MissingPluginException {
      // The lock screen uses Android only.
    }
  }

  Future<void> preview(VoiceOption voice) async {
    await tts.stop();
    await apply(voice);
    await tts.awaitSpeakCompletion(true);
    await WakeWord.hold();
    try {
      await tts.speak(
        'Hi, I’m Gajala. Your tea timer has two minutes left. Should I call Priya?',
      );
    } finally {
      try {
        await applySaved();
      } finally {
        await WakeWord.release();
      }
    }
  }

  Future<void> openEngineSettings() =>
      const AndroidIntent(action: 'com.android.settings.TTS_SETTINGS').launch();
}
