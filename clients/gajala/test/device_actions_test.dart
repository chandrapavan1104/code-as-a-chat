import 'package:flutter/services.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/device_actions.dart';
import 'package:gajala/core/voice_logic.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  late List<MethodCall> device, intents, phone;
  late Map<String, dynamic> Function(MethodCall) deviceReply;

  setUp(() {
    FlutterSecureStorage.setMockInitialValues({});
    device = [];
    intents = [];
    phone = [];
    messenger.setMockMethodCallHandler(const MethodChannel('gajala/phone'), (
      c,
    ) async {
      phone.add(c);
      return deviceReply(c);
    });
    deviceReply = (_) => {'ok': true};
    messenger.setMockMethodCallHandler(const MethodChannel('gajala/device'), (
      c,
    ) async {
      device.add(c);
      return deviceReply(c);
    });
    messenger.setMockMethodCallHandler(
      const MethodChannel('dev.fluttercommunity.plus/android_intent'),
      (c) async {
        intents.add(c);
        return null;
      },
    );
  });

  group('voice phrases become phone actions', () {
    final cases = <String, LocalIntent>{
      'play lofi beats on spotify': const PhoneAction('action.play', {
        'query': 'lofi beats',
        'app': 'spotify',
      }),
      'play arijit singh': const PlayMusic('arijit singh'),
      'pause the music': const PhoneAction('action.media', {'key': 'pause'}),
      'resume': const PhoneAction('action.media', {'key': 'play'}),
      'skip this song': const PhoneAction('action.media', {'key': 'next'}),
      'previous track': const PhoneAction('action.media', {'key': 'previous'}),
      'turn it up': const PhoneAction('action.volume', {'change': 'up'}),
      'mute': const PhoneAction('action.volume', {'change': 'mute'}),
      'turn on the flashlight': const PhoneAction('action.flashlight', {
        'on': true,
      }),
      'torch off': const PhoneAction('action.flashlight', {'on': false}),
      'turn on bluetooth': const PhoneAction('action.settings', {
        'panel': 'bluetooth',
      }),
      'wi-fi settings': const PhoneAction('action.settings', {'panel': 'wifi'}),
      'turn on do not disturb': const PhoneAction('action.dnd', {'on': true}),
      'take a selfie': const PhoneAction('action.camera', {'selfie': true}),
      'open the camera': const PhoneAction('action.camera', {'selfie': false}),
      'open whatsapp': const PhoneAction('action.open_app', {
        'name': 'whatsapp',
      }),
    };
    cases.forEach((said, want) {
      test(said, () => expect(parseLocalIntent(said), want));
    });

    test('Gajala-world requests still go to the Mac', () {
      for (final said in [
        'open notes',
        'play the build log',
        'open the queue',
      ]) {
        expect(parseLocalIntent(said), isNull, reason: said);
      }
    });

    test(
      'alarms and directions use the device action (labels, saved places)',
      () {
        final nav = parseLocalIntent('navigate to home')!.deviceAction!;
        expect(nav.command, 'action.navigate');
        expect(nav.args, {'to': 'home'});
        expect(
          parseLocalIntent('set an alarm for 7:30 am')!.deviceAction!.command,
          'action.alarm',
        );
      },
    );
  });

  test('app matching prefers exact, then prefix, then substring', () {
    const apps = [
      (label: 'WhatsApp', pkg: 'com.whatsapp'),
      (label: 'WhatsApp Business', pkg: 'com.whatsapp.w4b'),
      (label: 'Google Maps', pkg: 'com.google.android.apps.maps'),
    ];
    expect(bestAppMatch('whatsapp', apps)!.pkg, 'com.whatsapp');
    expect(bestAppMatch('maps app', apps)!.pkg, 'com.google.android.apps.maps');
    expect(bestAppMatch('telegram', apps), isNull);
  });

  group('DeviceActions', () {
    final d = DeviceActions.instance;

    test('ordinary actions run and report what happened', () async {
      final r = await d.run('action.flashlight', {'on': true});
      expect(r, {
        'ok': true,
        'data': {'done': 'Flashlight on.'},
      });
      expect(device.single.arguments, {'on': true});
    });

    test('sensitive actions are refused until switched on', () async {
      for (final id in [
        'action.sms_send',
        'action.dnd',
        'action.notifications',
      ]) {
        final r = await d.run(id, {'to': '123456', 'text': 'x', 'on': true});
        expect(r['disabled'], true, reason: id);
      }
      expect(device, isEmpty);
    });

    test('missing Android access opens the grant screen and says so', () async {
      await d.setEnabled('action.dnd', true);
      deviceReply = (c) => c.method == 'dnd'
          ? {
              'ok': false,
              'error': "Gajala doesn't have Do Not Disturb access yet.",
              'needsAccess': 'dnd',
            }
          : {'ok': true};
      final r = await d.run('action.dnd', {'on': true});
      expect(r['ok'], false);
      expect(r['error'], contains('ask again after allowing Gajala'));
      expect(device.map((c) => c.method), ['dnd', 'openAccessSettings']);
    });

    test('open app launches the matched package', () async {
      deviceReply = (c) => c.method == 'apps'
          ? {
              'ok': true,
              'apps': [
                {'label': 'Spotify', 'package': 'com.spotify.music'},
                {'label': 'Settings', 'package': 'com.android.settings'},
              ],
            }
          : {'ok': true};
      final r = await d.run('action.open_app', {'name': 'spotify'});
      expect(r['data'], {'done': 'Opened Spotify.'});
      expect(device.last.arguments, {'package': 'com.spotify.music'});
    });

    test(
      'music handoff stays unverified until native playback is observed',
      () async {
        deviceReply = (c) => c.method == 'playMusic'
            ? {
                'status': 'requested',
                'message': 'Playback requested; verification unavailable.',
              }
            : {'ok': true};
        final r = await d.run('action.play', {
          'query': 'lofi beats',
          'app': 'spotify',
        });
        expect(r['ok'], false);
        expect(r['error'], contains('verification unavailable'));
        expect(device, isEmpty);
        expect(phone.single.method, 'playMusic');
        expect(phone.single.arguments, {
          'query': 'lofi beats',
          'package': 'com.spotify.music',
        });
      },
    );

    test('music success requires the observed title', () async {
      deviceReply = (c) => c.method == 'playMusic'
          ? {'status': 'playing', 'title': 'Kun Faya Kun'}
          : {'ok': true};
      final r = await d.run('action.play', {'query': 'kun faya kun'});
      expect(r, {
        'ok': true,
        'data': {'title': 'Kun Faya Kun', 'done': 'Playing Kun Faya Kun.'},
      });
    });

    test('explicit YouTube requests remain searches', () async {
      final r = await d.run('action.play', {
        'query': 'live concert',
        'app': 'youtube',
      });
      expect(r, {
        'ok': true,
        'data': {'done': 'Searching YouTube for live concert.'},
      });
      expect(
        intents,
        isEmpty,
        reason: 'Android intents are a no-op on host tests',
      );
    });

    test('alarm uses the clock app with the label', () async {
      final r = await d.run('action.alarm', {
        'hour': 6,
        'minute': 30,
        'label': 'gym',
      });
      // The intent plugin only launches on a real Android device.
      expect(r['data'], {'done': 'Alarm set for 6:30 AM (gym).'});
    });

    test('navigate home needs a saved address, then uses it', () async {
      expect(
        (await d.run('action.navigate', {'to': 'home'}))['error'],
        contains('No home address is saved'),
      );
      await d.setSavedPlace('home', 'Banjara Hills, Hyderabad');
      final r = await d.run('action.navigate', {'to': 'home'});
      expect(r['data'], {'done': 'Directions to home.'});
    });
  });
}
