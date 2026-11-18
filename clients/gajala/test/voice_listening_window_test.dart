import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/voice.dart';

void main() {
  test(
    'an early silent recognizer attempt retries within the same window',
    () async {
      final deadlines = <Duration>[];
      final text = await listenWithinWindow(
        const Duration(seconds: 1),
        active: () => true,
        attempt: (remaining) async {
          deadlines.add(remaining);
          return deadlines.length == 1 ? '' : 'set a timer';
        },
        retryDelay: Duration.zero,
      );
      expect(text, 'set a timer');
      expect(deadlines, hasLength(2));
      expect(deadlines.last, lessThan(deadlines.first));
    },
  );

  test('a result from an invalidated session is discarded', () async {
    var active = true;
    expect(
      await listenWithinWindow(
        const Duration(seconds: 1),
        active: () => active,
        confirmation: true,
        attempt: (_) async {
          active = false;
          return 'yes';
        },
      ),
      isEmpty,
    );
  });

  test('confirmation arriving after its deadline cannot be used', () async {
    expect(
      await listenWithinWindow(
        const Duration(milliseconds: 10),
        active: () => true,
        confirmation: true,
        attempt: (_) async {
          await Future<void>.delayed(const Duration(milliseconds: 30));
          return 'yes';
        },
      ),
      isEmpty,
    );
  });
}
