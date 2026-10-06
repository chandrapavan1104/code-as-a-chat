import 'package:android_intent_plus/android_intent.dart';
import 'package:flutter_tts/flutter_tts.dart';

import 'storage.dart';

/// A TTS voice as reported by the installed Android engine. Gender is only
/// shown when the engine explicitly supplies it; Android commonly omits it.
class VoiceOption {
  final String name;
  final String locale;
  final String? gender;

  const VoiceOption({required this.name, required this.locale, this.gender});

  factory VoiceOption.fromMap(Map<dynamic, dynamic> raw) => VoiceOption(
    name: raw['name']?.toString() ?? raw['voiceName']?.toString() ?? '',
    locale: raw['locale']?.toString() ?? raw['language']?.toString() ?? '',
    gender: _explicitGender(raw['gender']),
  );

  static String? _explicitGender(Object? value) {
    final text = value?.toString().trim().toLowerCase();
    if (text == 'male' || text == 'female' || text == 'neutral') return text;
    return null;
  }

  bool get isIndianEnglish =>
      locale.toLowerCase().replaceAll('_', '-') == 'en-in';

  String get label => gender == 'male'
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
          .where((voice) => voice.name.isNotEmpty && voice.isIndianEnglish)
          .toSet()
          .toList()
        ..sort((a, b) => a.name.toLowerCase().compareTo(b.name.toLowerCase()));

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

  Future<List<VoiceOption>> available() async {
    final raw = await tts.getVoices;
    if (raw is! Iterable) return const [];
    return indianVoicesFrom(raw.cast<Object?>());
  }

  Future<VoiceOption?> selected(List<VoiceOption> voices) async {
    final saved = await Storage.voiceSelection();
    return chooseDefault(
      voices,
      savedName: saved.name,
      savedLocale: saved.locale,
    );
  }

  Future<void> select(VoiceOption voice) async {
    await tts.setLanguage(voice.locale);
    await tts.setVoice({'name': voice.name, 'locale': voice.locale});
    await Storage.setVoiceSelection(name: voice.name, locale: voice.locale);
  }

  Future<void> preview(VoiceOption voice) async {
    await select(voice);
    await tts.stop();
    await tts.awaitSpeakCompletion(true);
    await tts.speak('Hello, I am Gajala.');
  }

  Future<void> openEngineSettings() =>
      const AndroidIntent(action: 'com.android.settings.TTS_SETTINGS').launch();
}
