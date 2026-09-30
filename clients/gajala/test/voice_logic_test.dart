import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/voice_logic.dart';

void main() {
  final evening = DateTime(2026, 9, 29, 22, 0);
  final afternoon = DateTime(2026, 9, 29, 13, 0);

  group('Gajala-domain requests always go to the Mac', () {
    for (final u in [
      "what's in queue",
      'deploy status',
      'remind me at 7 to call the bank',
      'set an alarm to check the build',
      'search my notes for passwords policy',
      'switch to the deaf terminal project',
      'lock the mac',
      'how are you today',
      '',
    ]) {
      test(u.isEmpty ? '(empty)' : u, () => expect(parseLocalIntent(u, now: evening), isNull));
    }
  });

  group('alarms', () {
    test('explicit am/pm', () {
      expect(parseLocalIntent('Set an alarm for 7:30 a.m.', now: evening), const SetAlarm(7, 30));
      expect(parseLocalIntent('wake me up at 6 pm', now: evening), const SetAlarm(18, 0));
      expect(parseLocalIntent('alarm at 12 am', now: evening), const SetAlarm(0, 0));
      expect(parseLocalIntent('alarm at 12 pm', now: evening), const SetAlarm(12, 0));
    });
    test('24-hour and named times', () {
      expect(parseLocalIntent('set alarm for 19:45', now: evening), const SetAlarm(19, 45));
      expect(parseLocalIntent('alarm at noon', now: evening), const SetAlarm(12, 0));
    });
    test('bare hour picks the next occurrence', () {
      expect(parseLocalIntent('set an alarm for 7', now: evening), const SetAlarm(7, 0));
      expect(parseLocalIntent('set an alarm for 5', now: afternoon), const SetAlarm(17, 0));
    });
    test('invalid times are not guessed', () {
      expect(parseLocalIntent('set an alarm for 25:00', now: evening), isNull);
      expect(parseLocalIntent('set an alarm', now: evening), isNull);
    });
  });

  group('timers', () {
    test('units and combinations', () {
      expect(parseLocalIntent('set a timer for 10 minutes'), const SetTimer(600));
      expect(parseLocalIntent('timer 30 seconds'), const SetTimer(30));
      expect(parseLocalIntent('start a 1 hour 15 minute timer'), const SetTimer(4500));
      expect(parseLocalIntent('set a timer for an hour'), const SetTimer(3600));
      expect(parseLocalIntent('set a timer for half an hour'), const SetTimer(1800));
    });
    test('timer without a duration is not guessed', () {
      expect(parseLocalIntent('set a timer'), isNull);
    });
  });

  test('calls: digits dial locally, names go to Google', () {
    expect(parseLocalIntent('call 98765 43210'), const DialNumber('9876543210'));
    expect(parseLocalIntent('dial +1 (415) 555-0100'), const DialNumber('+14155550100'));
    expect(parseLocalIntent('call mom'), const AskGoogle('call mom'));
  });

  test('navigation and search', () {
    expect(parseLocalIntent('Navigate to Charminar'), const Navigate('charminar'));
    expect(parseLocalIntent('how do I get to the airport?'), const Navigate('the airport'));
    expect(parseLocalIntent('search for monsoon forecast'), const WebSearch('monsoon forecast'));
    expect(parseLocalIntent('google cricket score'), const WebSearch('cricket score'));
  });

  test('explicit Google handoff wins even for Gajala words', () {
    expect(parseLocalIntent('ask google what time the mac store closes'),
        const AskGoogle('what time the mac store closes'));
  });

  test('confirmations read naturally', () {
    expect(const SetAlarm(7, 5).confirmation, 'Alarm set for 7:05 AM.');
    expect(const SetAlarm(0, 0).confirmation, 'Alarm set for 12 AM.');
    expect(const SetTimer(3661).confirmation, 'Timer started for 1 hour 1 minute 1 second.');
  });

  group('speakable', () {
    test('strips code, markdown, markers, links and emoji', () {
      final s = speakable(
        '## Done 🔥\n- **Fixed** the `auth` bug\n```dart\nvoid main() {}\n```\n'
        'See [the PR](https://github.com/x/y/pull/1) or https://example.com [[switch:foo]]',
      );
      expect(s, 'Done. Fixed the auth bug. I put the code in the chat. See the PR or a link');
    });
    test('long replies end at a sentence and point at the chat', () {
      final long = List.filled(80, 'This sentence is filler.').join(' ');
      final s = speakable(long, maxChars: 200);
      expect(s.length, lessThan(240));
      expect(s, endsWith('filler. The rest is in the chat.'));
    });
    test('short replies pass through', () {
      expect(speakable('Queue has 3 held tasks.'), 'Queue has 3 held tasks.');
    });
  });
}
