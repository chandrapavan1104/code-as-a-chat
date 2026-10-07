import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/phone_actions.dart';
import 'package:gajala/core/voice_logic.dart';

void main() {
  test(
    'number calls require a strict confirmation before invoking native call',
    () async {
      final methods = <String>[];
      final result = await PhoneActions(
        invoke: (method, args) async {
          methods.add(method);
          return {'status': 'called'};
        },
        ask: (_) async => 'maybe',
        isActive: () => true,
        askTimeout: const Duration(milliseconds: 20),
      ).execute(const DialNumber('123456789'));

      expect(result.reply, 'Okay, I will not call.');
      expect(result.handedOff, isFalse);
      expect(methods, isEmpty);
    },
  );

  test('silence times out without invoking native call', () async {
    var invoked = false;
    final result = await PhoneActions(
      invoke: (method, args) async {
        invoked = true;
        return {'status': 'called'};
      },
      ask: (_) => Completer<String>().future,
      isActive: () => true,
      askTimeout: const Duration(milliseconds: 1),
    ).execute(const DialNumber('123456789'));

    expect(result.reply, 'Okay, I will not call.');
    expect(invoked, isFalse);
  });

  test('ambiguous contacts require a numbered selection', () async {
    final methods = <String>[];
    final arguments = <Map<String, dynamic>>[];
    final result = await PhoneActions(
      invoke: (method, args) async {
        methods.add(method);
        arguments.add(args);
        return {
          'contacts': [
            {'name': 'Alex', 'label': 'mobile', 'number': '1111'},
            {'name': 'Alex', 'label': 'work', 'number': '2222'},
          ],
        };
      },
      ask: (prompt) async => 'four',
      isActive: () => true,
    ).execute(const CallContact('Alex'));

    expect(result.reply, 'I did not choose a contact.');
    expect(methods, ['resolveContact']);
    expect(arguments, [
      {'query': 'Alex'},
    ]);
  });

  test(
    'contact choices use labels; final confirmation uses only the name',
    () async {
      final prompts = <String>[];
      final calls = <Map<String, dynamic>>[];
      var answer = 0;
      final result = await PhoneActions(
        invoke: (method, args) async {
          if (method == 'resolveContact') {
            return {
              'contacts': [
                {'name': 'Alex', 'label': 'mobile', 'number': '1111'},
                {'name': 'Alex', 'label': 'work', 'number': '2222'},
              ],
            };
          }
          calls.add(args);
          return {'status': 'called'};
        },
        ask: (prompt) async {
          prompts.add(prompt);
          answer++;
          return answer == 1 ? 'second' : 'yes';
        },
        isActive: () => true,
      ).execute(const CallContact('Alex'));

      expect(prompts.first, contains('2, Alex, work'));
      expect(prompts.last, 'Call Alex? Say yes or no.');
      expect(prompts.last, isNot(contains('2222')));
      expect(calls, [
        {'number': '2222'},
      ]);
      expect(result.reply, 'Calling Alex.');
      expect(result.handedOff, isTrue);
    },
  );

  test(
    'music only hands off after verified playback and uses observed title',
    () async {
      final result = await PhoneActions(
        invoke: (method, args) async => {
          'status': 'playing',
          'title': 'Kun Faya Kun',
        },
        ask: (_) async => 'yes',
        isActive: () => true,
      ).execute(const PlayMusic('rockstar hindi songs'));

      expect(result.reply, 'Playing Kun Faya Kun on YouTube Music.');
      expect(result.handedOff, isTrue);
    },
  );

  test(
    'unsupported music remains spoken and does not suppress the reply',
    () async {
      final result = await PhoneActions(
        invoke: (method, args) async => {'status': 'unsupported'},
        ask: (_) async => 'yes',
        isActive: () => true,
      ).execute(const PlayMusic('rockstar hindi songs'));

      expect(result.handedOff, isFalse);
      expect(result.reply, contains('cannot start playback'));
    },
  );

  test('cancellation while asking prevents a later call', () async {
    var active = true;
    var invoked = false;
    final resultFuture = PhoneActions(
      invoke: (method, args) async {
        invoked = true;
        return {'status': 'called'};
      },
      ask: (_) async {
        active = false;
        return 'yes';
      },
      isActive: () => active,
    ).execute(const DialNumber('123456789'));

    final result = await resultFuture;
    expect(invoked, isFalse);
    expect(
      result.reply,
      anyOf('Voice session ended.', 'Okay, I will not call.'),
    );
  });

  test('permission retry asks for fresh confirmation exactly once', () async {
    final methods = <String>[];
    final answers = <String>['yes', 'yes'];
    final result = await PhoneActions(
      invoke: (method, args) async {
        methods.add(method);
        return {
          'status': methods.length == 1 ? 'permission_granted_retry' : 'called',
        };
      },
      ask: (_) async => answers.removeAt(0),
      isActive: () => true,
    ).execute(const DialNumber('123456789'));

    expect(methods, ['call', 'call']);
    expect(result.reply, 'Calling 1 2 3 4 5 6 7 8 9.');
    expect(result.handedOff, isTrue);
  });

  test('music reports requested without claiming confirmed playback', () async {
    Map<String, dynamic>? received;
    final result = await PhoneActions(
      invoke: (method, args) async {
        received = {'method': method, ...args};
        return {'status': 'requested'};
      },
      ask: (_) async => 'yes',
      isActive: () => true,
    ).execute(const PlayMusic('rockstar hindi songs'));

    expect(received, {
      'method': 'playMusic',
      'query': 'rockstar hindi songs',
      'package': 'com.google.android.apps.youtube.music',
    });
    expect(result.reply, contains('could not confirm'));
    expect(result.handedOff, isFalse);
  });
}
