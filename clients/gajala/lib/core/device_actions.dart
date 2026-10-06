// Things Gajala can DO on this phone, Google-Assistant style.
//
// Reached two ways: the `device` skill on the Mac sends `action.*` requests
// over the live chat stream (typed chat, voice, multi-step plans), and voice
// mode calls [DeviceActions.run] directly for instant, offline phrases.
//
// Ordinary actions (alarms, music, apps, flashlight…) need no switch. The three
// sensitive ones — direct SMS, Do Not Disturb, reading notifications — are off
// until enabled in Phone abilities. Messages and calls open pre-filled for the
// owner to send; calendar events are confirmed on screen first.

import 'package:android_intent_plus/android_intent.dart';
import 'package:device_calendar/device_calendar.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_contacts/flutter_contacts.dart' hide Event;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:timezone/timezone.dart' as tz;
import 'push.dart';
import 'voice_logic.dart' show spokenDuration, spokenTime;

/// Sensitive actions the owner must switch on, in the order shown in settings.
const sensitiveActions = <({String id, String title, String detail, String? access})>[
  (
    id: 'action.sms_send',
    title: 'Send SMS directly',
    detail: 'Text someone without showing the message first. Only when you ask.',
    access: null,
  ),
  (
    id: 'action.dnd',
    title: 'Do Not Disturb',
    detail: 'Turn Do Not Disturb on or off. Needs Android "Do Not Disturb access".',
    access: 'dnd',
  ),
  (
    id: 'action.notifications',
    title: 'Read notifications',
    detail: "Read what's in your notification shade when you ask. Needs notification access.",
    access: 'notifications',
  ),
];

const _newTask = 0x10000000; // FLAG_ACTIVITY_NEW_TASK
const _musicApps = {
  'spotify': 'com.spotify.music',
  'youtube_music': 'com.google.android.apps.youtube.music',
};

class DeviceActions {
  DeviceActions._();
  static final instance = DeviceActions._();
  static const _native = MethodChannel('gajala/device');
  static const _store = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  Future<bool> isEnabled(String id) async =>
      (await _store.read(key: 'ability_$id')) == 'true';
  Future<void> setEnabled(String id, bool on) =>
      _store.write(key: 'ability_$id', value: on.toString());

  Future<String?> savedPlace(String name) => _store.read(key: 'place_$name');
  Future<void> setSavedPlace(String name, String address) => address.trim().isEmpty
      ? _store.delete(key: 'place_$name')
      : _store.write(key: 'place_$name', value: address.trim());

  Future<void> openAccessSettings(String which) =>
      _native.invokeMethod('openAccessSettings', {'which': which});

  /// Perform one `action.*` request. Never throws: the answer is
  /// `{ok: true, data: {done: "what happened", ...}}` or `{ok: false, error}`.
  Future<Map<String, dynamic>> run(String command, Map<String, dynamic> args) async {
    for (final s in sensitiveActions) {
      if (s.id == command && !await isEnabled(command)) {
        return {
          'ok': false,
          'disabled': true,
          'error': '${s.title} is off. Turn it on in Gajala → menu → Phone abilities.',
        };
      }
    }
    try {
      final data = await switch (command) {
        'action.alarm' => _alarm(args),
        'action.timer' => _timer(args),
        'action.play' => _play(args),
        'action.media' => _native2('mediaKey', {'key': args['key']},
            done: _mediaDone(args['key'])),
        'action.volume' => _native2('volume', {'change': args['change']},
            done: 'Volume ${args['change']}.'),
        'action.open_app' => _openApp('${args['name'] ?? ''}'),
        'action.compose' => _compose(args),
        'action.sms_send' => _smsSend(args),
        'action.call' => _call('${args['to'] ?? ''}'),
        'action.calendar_add' => _calendarAdd(args),
        'action.flashlight' => _native2('flashlight', {'on': args['on'] == true},
            done: 'Flashlight ${args['on'] == true ? 'on' : 'off'}.'),
        'action.settings' => _native2('settingsPanel', {'panel': args['panel']},
            done: 'Opened ${args['panel']} settings.'),
        'action.navigate' => _navigate('${args['to'] ?? ''}'),
        'action.dnd' => _native2('dnd', {'on': args['on'] == true},
            done: 'Do Not Disturb ${args['on'] == true ? 'on' : 'off'}.'),
        'action.notifications' => _native2('notifications', const {}, done: null),
        'action.camera' => _camera(args['selfie'] == true),
        _ => throw _Refused('Unknown phone action $command'),
      };
      return {'ok': true, 'data': data};
    } on _Refused catch (e) {
      return {'ok': false, 'error': e.reason};
    } on PlatformException catch (e) {
      return {'ok': false, 'error': e.message ?? 'No app on this phone can do that.'};
    } catch (e) {
      return {'ok': false, 'error': e.toString()};
    }
  }

  // ── native helpers ─────────────────────────────────────────────────────────
  Future<Map<String, dynamic>> _native2(String method, Map<String, dynamic> args,
      {required String? done}) async {
    final r = Map<String, dynamic>.from(
        await _native.invokeMethod<Map>(method, args) ?? const {});
    if (r['ok'] != true) {
      final access = r['needsAccess']?.toString();
      if (access != null) {
        await openAccessSettings(access);
        throw _Refused('${r['error']} I opened the Android screen to grant it; '
            'ask again after allowing Gajala.');
      }
      throw _Refused(r['error']?.toString() ?? 'The phone could not do that.');
    }
    r.remove('ok');
    if (done != null) r['done'] = done;
    return r;
  }

  String _mediaDone(dynamic key) => switch ('$key') {
    'pause' => 'Paused.',
    'play' => 'Playing.',
    'next' => 'Skipped to the next track.',
    'previous' => 'Back to the previous track.',
    _ => 'Toggled play/pause.',
  };

  Future<void> _launch(AndroidIntent intent) => intent.launch();

  // ── alarms & timers ────────────────────────────────────────────────────────
  Future<Map<String, dynamic>> _alarm(Map<String, dynamic> a) async {
    final h = (a['hour'] as num).toInt(), m = (a['minute'] as num).toInt();
    final label = '${a['label'] ?? ''}'.trim();
    await _launch(AndroidIntent(
      action: 'android.intent.action.SET_ALARM',
      arguments: {
        'android.intent.extra.alarm.HOUR': h,
        'android.intent.extra.alarm.MINUTES': m,
        'android.intent.extra.alarm.SKIP_UI': true,
        'android.intent.extra.alarm.MESSAGE': label.isEmpty ? 'Gajala' : label,
      },
      flags: const [_newTask],
    ));
    return {'done': 'Alarm set for ${spokenTime(h, m)}${label.isEmpty ? '' : ' ($label)'}.'};
  }

  Future<Map<String, dynamic>> _timer(Map<String, dynamic> a) async {
    final s = (a['seconds'] as num).toInt();
    final label = '${a['label'] ?? ''}'.trim();
    await _launch(AndroidIntent(
      action: 'android.intent.action.SET_TIMER',
      arguments: {
        'android.intent.extra.alarm.LENGTH': s,
        'android.intent.extra.alarm.SKIP_UI': true,
        'android.intent.extra.alarm.MESSAGE': label.isEmpty ? 'Gajala' : label,
      },
      flags: const [_newTask],
    ));
    return {'done': 'Timer started for ${spokenDuration(s)}.'};
  }

  // ── music ──────────────────────────────────────────────────────────────────
  Future<Map<String, dynamic>> _play(Map<String, dynamic> a) async {
    final query = '${a['query'] ?? ''}';
    final app = a['app']?.toString();
    if (app == 'youtube') {
      await _launch(AndroidIntent(
        action: 'android.intent.action.SEARCH',
        package: 'com.google.android.youtube',
        arguments: {'query': query},
        flags: const [_newTask],
      ));
      return {'done': 'Searching YouTube for $query.'};
    }
    final r = await _native2('playFromSearch',
        {'query': query, 'package': _musicApps[app]}, done: 'Playing $query.');
    if (r['fallback'] == true) r['done'] = 'That app is not installed; asked your music app to play $query.';
    return r;
  }

  // ── apps ───────────────────────────────────────────────────────────────────
  Future<Map<String, dynamic>> _openApp(String name) async {
    final r = await _native2('apps', const {}, done: null);
    final apps = [
      for (final a in (r['apps'] as List? ?? const []))
        (label: '${a['label']}', pkg: '${a['package']}'),
    ];
    final match = bestAppMatch(name, apps);
    if (match == null) throw _Refused('No app called "$name" is installed.');
    await _native2('launch', {'package': match.pkg}, done: null);
    return {'done': 'Opened ${match.label}.'};
  }

  // ── people: messages & calls ───────────────────────────────────────────────
  /// A name or number → one phone number, resolved on the phone (contact data
  /// is not sent to the Mac for this).
  Future<({String number, String who})?> _resolve(String to) async {
    final digits = to.replaceAll(RegExp(r'[\s\-().]'), '');
    if (RegExp(r'^\+?\d{3,15}$').hasMatch(digits)) return (number: digits, who: digits);
    final perms = FlutterContacts.permissions;
    if (!await perms.has(PermissionType.read)) {
      final s = await perms.request(PermissionType.read);
      if (s != PermissionStatus.granted && s != PermissionStatus.limited) {
        throw const _Refused('Contacts permission is needed to find who that is.');
      }
    }
    final found = await FlutterContacts.getAll(
      properties: {ContactProperty.phone},
      filter: ContactFilter.name(to.trim()),
      limit: 5,
    );
    final withPhone = found.where((c) => c.phones.isNotEmpty).toList();
    if (withPhone.isEmpty) return null;
    final exact = withPhone.where((c) => (c.displayName ?? '').toLowerCase() == to.trim().toLowerCase());
    if (exact.isEmpty && withPhone.length > 1) {
      throw _Refused('More than one contact matches "$to": '
          '${withPhone.map((c) => c.displayName).join(', ')}. Say the full name.');
    }
    final c = exact.isNotEmpty ? exact.first : withPhone.first;
    return (number: c.phones.first.number.replaceAll(RegExp(r'[\s\-()]'), ''),
        who: c.displayName ?? to);
  }

  Future<Map<String, dynamic>> _compose(Map<String, dynamic> a) async {
    final text = '${a['text'] ?? ''}';
    final to = '${a['to'] ?? ''}';
    final via = '${a['via'] ?? 'sms'}';
    final who = await _resolve(to);
    if (via == 'whatsapp') {
      if (who == null) {
        await _launch(AndroidIntent(
          action: 'android.intent.action.SEND',
          package: 'com.whatsapp',
          type: 'text/plain',
          arguments: {'android.intent.extra.TEXT': text},
          flags: const [_newTask],
        ));
        return {'done': 'Opened WhatsApp with your message; pick the chat and tap send.'};
      }
      final digits = who.number.replaceAll('+', '');
      await _launch(AndroidIntent(
        action: 'android.intent.action.VIEW',
        data: 'https://wa.me/$digits?text=${Uri.encodeComponent(text)}',
        package: 'com.whatsapp',
        flags: const [_newTask],
      ));
      return {'done': 'Opened WhatsApp to ${who.who} with your message; tap send.'};
    }
    if (who == null) throw _Refused('No contact with a number matches "$to".');
    await _launch(AndroidIntent(
      action: 'android.intent.action.SENDTO',
      data: 'smsto:${who.number}',
      arguments: {'sms_body': text},
      flags: const [_newTask],
    ));
    return {'done': 'Opened a text to ${who.who}; tap send.'};
  }

  Future<Map<String, dynamic>> _smsSend(Map<String, dynamic> a) async {
    final to = '${a['to'] ?? ''}';
    final who = await _resolve(to);
    if (who == null) throw _Refused('No contact with a number matches "$to".');
    await _native2('smsSend', {'number': who.number, 'text': '${a['text'] ?? ''}'}, done: null);
    return {'done': 'Sent an SMS to ${who.who} (${who.number}).'};
  }

  Future<Map<String, dynamic>> _call(String to) async {
    final who = await _resolve(to);
    if (who == null) throw _Refused('No contact with a number matches "$to".');
    await _launch(AndroidIntent(
      action: 'android.intent.action.DIAL',
      data: 'tel:${who.number}',
      flags: const [_newTask],
    ));
    return {'done': 'Ready to call ${who.who}; tap the call button.'};
  }

  // ── calendar (confirmed on screen) ─────────────────────────────────────────
  Future<Map<String, dynamic>> _calendarAdd(Map<String, dynamic> a) async {
    final title = '${a['title'] ?? ''}';
    final start = DateTime.parse('${a['start']}');
    final end = a['end'] != null
        ? DateTime.parse('${a['end']}')
        : start.add(const Duration(hours: 1));
    final location = a['location']?.toString();
    final ctx = Push.navigatorKey.currentContext;
    if (ctx == null) throw const _Refused('Open Gajala to confirm the event.');
    final ok = await showDialog<bool>(
      context: ctx,
      builder: (c) => AlertDialog(
        title: const Text('Add to calendar?'),
        content: Text('$title\n${_when(start, end)}'
            '${location == null ? '' : '\n$location'}'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Add')),
        ],
      ),
    );
    if (ok != true) throw const _Refused('You cancelled adding the event.');

    final plugin = DeviceCalendarPlugin();
    var granted = (await plugin.hasPermissions()).data ?? false;
    if (!granted) granted = (await plugin.requestPermissions()).data ?? false;
    if (!granted) throw const _Refused('Calendar permission was not granted.');
    final calendars = (await plugin.retrieveCalendars()).data ?? const [];
    final writable = calendars.where((c) => c.isReadOnly != true && c.id != null).toList()
      ..sort((x, y) => (y.isDefault == true ? 1 : 0) - (x.isDefault == true ? 1 : 0));
    if (writable.isEmpty) throw const _Refused('No calendar on this phone accepts new events.');
    final res = await plugin.createOrUpdateEvent(Event(
      writable.first.id,
      title: title,
      start: tz.TZDateTime.from(start.toUtc(), tz.UTC),
      end: tz.TZDateTime.from(end.toUtc(), tz.UTC),
      location: location,
    ));
    if (res == null || !res.isSuccess) {
      throw _Refused('The calendar refused the event: '
          '${res?.errors.map((e) => e.errorMessage).join(', ') ?? 'unknown error'}');
    }
    return {'done': 'Added "$title" to ${writable.first.name ?? 'your calendar'}, ${_when(start, end)}.'};
  }

  String _when(DateTime s, DateTime e) {
    String two(int n) => n.toString().padLeft(2, '0');
    final day = '${s.year}-${two(s.month)}-${two(s.day)}';
    return '$day ${spokenTime(s.hour, s.minute)}–${spokenTime(e.hour, e.minute)}';
  }

  // ── places & camera ────────────────────────────────────────────────────────
  Future<Map<String, dynamic>> _navigate(String to) async {
    var dest = to.trim();
    final key = dest.toLowerCase();
    if (key == 'home' || key == 'work') {
      final saved = await savedPlace(key);
      if (saved == null) {
        throw _Refused('No $key address is saved. Add it in Gajala → menu → Phone abilities.');
      }
      dest = saved;
    }
    try {
      await _launch(AndroidIntent(
        action: 'android.intent.action.VIEW',
        data: 'google.navigation:q=${Uri.encodeComponent(dest)}',
        flags: const [_newTask],
      ));
    } on PlatformException {
      await _launch(AndroidIntent(
        action: 'android.intent.action.VIEW',
        data: 'geo:0,0?q=${Uri.encodeComponent(dest)}',
        flags: const [_newTask],
      ));
    }
    return {'done': 'Directions to ${key == 'home' || key == 'work' ? key : dest}.'};
  }

  Future<Map<String, dynamic>> _camera(bool selfie) async {
    await _launch(AndroidIntent(
      action: 'android.media.action.STILL_IMAGE_CAMERA',
      arguments: selfie
          ? {
              'android.intent.extras.CAMERA_FACING': 1,
              'android.intent.extras.LENS_FACING_FRONT': 1,
              'android.intent.extra.USE_FRONT_CAMERA': true,
            }
          : const {},
      flags: const [_newTask],
    ));
    return {'done': selfie ? 'Opened the front camera.' : 'Opened the camera.'};
  }
}

/// Best launcher app for a spoken name: exact label, then prefix, then
/// substring; null when nothing plausible is installed.
({String label, String pkg})? bestAppMatch(
    String name, List<({String label, String pkg})> apps) {
  final want = name.toLowerCase().replaceAll(RegExp(r'\s+app$'), '').trim();
  if (want.isEmpty) return null;
  String norm(String s) => s.toLowerCase().trim();
  for (final test in <bool Function(String)>[
    (l) => l == want,
    (l) => l.startsWith(want),
    (l) => l.contains(want),
    (l) => l.replaceAll(' ', '') == want.replaceAll(' ', ''),
  ]) {
    final hits = apps.where((a) => test(norm(a.label))).toList()
      ..sort((a, b) => a.label.length.compareTo(b.label.length));
    if (hits.isNotEmpty) return hits.first;
  }
  return null;
}

class _Refused implements Exception {
  final String reason;
  const _Refused(this.reason);
}
