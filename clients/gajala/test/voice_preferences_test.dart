import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/voice_preferences.dart';

void main() {
  test('filters Indian English voices and preserves explicit gender only', () {
    final voices = VoicePreferences.indianVoicesFrom([
      {'name': 'en-in-x-foo-local', 'locale': 'en-IN', 'gender': 'male'},
      {'name': 'en-US-x-bar', 'locale': 'en-US', 'gender': 'female'},
      {'name': 'en-in-x-baz', 'locale': 'en_IN', 'gender': 'unknown'},
    ]);
    expect(voices, hasLength(2));
    expect(
      voices.map((voice) => voice.label),
      contains('Indian English • en-in-x-foo-local • male'),
    );
    expect(
      voices.where((voice) => voice.name == 'en-in-x-baz').single.label,
      isNot(contains('female')),
    );
  });

  test(
    'saved voice wins, then explicit male metadata, without guessing names',
    () {
      const femaleNamed = VoiceOption(name: 'Asha', locale: 'en-IN');
      const male = VoiceOption(
        name: 'Voice 2',
        locale: 'en-IN',
        gender: 'male',
      );
      final voices = [femaleNamed, male];
      expect(
        VoicePreferences.chooseDefault(
          voices,
          savedName: 'Asha',
          savedLocale: 'en-IN',
        ),
        femaleNamed,
      );
      expect(VoicePreferences.chooseDefault(voices), male);
      expect(
        const VoiceOption(name: 'Asha', locale: 'en-IN').label,
        'Indian English • Asha',
      );
    },
  );
}
