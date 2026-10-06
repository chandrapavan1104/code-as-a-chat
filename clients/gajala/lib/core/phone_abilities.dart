// What the Mac agent may read from this phone during a chat.
//
// Every ability is OFF until the owner switches it on in Phone abilities, and
// each use is announced in the chat. Requests arrive as `phone_request` frames
// on the live chat stream (see server/phone_bridge.py); the answer goes back to
// /api/phone/result/<id>. Nothing here runs in the background.

import 'package:battery_plus/battery_plus.dart';
import 'package:device_calendar/device_calendar.dart';
import 'package:flutter_contacts/flutter_contacts.dart' hide Event;
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:geolocator/geolocator.dart';
import 'package:image_picker/image_picker.dart';
import 'api.dart';

class Ability {
  final String id; // the phone_request command
  final String title;
  final String detail;
  final String chatNote; // shown in the chat each time it is used
  const Ability(this.id, this.title, this.detail, this.chatNote);
}

const abilities = <Ability>[
  Ability('location.get', 'Location',
      'Your current position, when you ask something location-based.',
      '📍 Shared your location with Gajala'),
  Ability('calendar.events', 'Calendar',
      "Read today's, tomorrow's or this week's events (read-only).",
      '📅 Shared calendar events with Gajala'),
  Ability('contacts.search', 'Contacts',
      'Look up a contact by name (read-only, only matching contacts).',
      '👤 Shared matching contacts with Gajala'),
  Ability('camera.snap', 'Camera',
      'Opens the camera; nothing is sent unless you take the photo.',
      '📷 Sent a photo to Gajala'),
  Ability('device.status', 'Phone status',
      'Battery level and charging state.',
      '🔋 Shared phone status with Gajala'),
];

class PhoneAbilities {
  PhoneAbilities._();
  static final instance = PhoneAbilities._();
  static const _store = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  Future<bool> isEnabled(String id) async =>
      (await _store.read(key: 'ability_$id')) == 'true';

  Future<void> setEnabled(String id, bool on) =>
      _store.write(key: 'ability_$id', value: on.toString());

  Ability? byId(String id) {
    for (final a in abilities) {
      if (a.id == id) return a;
    }
    return null;
  }

  /// Answer one request. Never throws; failures become {ok: false, error}.
  Future<Map<String, dynamic>> handle(
    String command,
    Map<String, dynamic> args,
    GajalaApi api,
  ) async {
    final ability = byId(command);
    if (ability == null) {
      return {'ok': false, 'error': 'Unknown phone ability $command'};
    }
    if (!await isEnabled(command)) {
      return {
        'ok': false,
        'disabled': true,
        'error': '${ability.title} is off. Turn it on in Gajala → menu → '
            'Phone abilities.',
      };
    }
    try {
      final data = switch (command) {
        'location.get' => await _location(),
        'calendar.events' => await _calendar(args['range']?.toString() ?? 'today'),
        'contacts.search' => await _contacts(args['query']?.toString() ?? ''),
        'camera.snap' => await _photo(api),
        'device.status' => await _status(),
        _ => throw StateError('unhandled'),
      };
      return {'ok': true, 'data': data};
    } on _Refused catch (e) {
      return {'ok': false, 'error': e.reason};
    } catch (e) {
      return {'ok': false, 'error': e.toString()};
    }
  }

  Future<Map<String, dynamic>> _location() async {
    if (!await Geolocator.isLocationServiceEnabled()) {
      throw const _Refused('Location services are turned off on the phone.');
    }
    var perm = await Geolocator.checkPermission();
    if (perm == LocationPermission.denied) {
      perm = await Geolocator.requestPermission();
    }
    if (perm == LocationPermission.denied || perm == LocationPermission.deniedForever) {
      throw const _Refused('Location permission was not granted.');
    }
    final p = await Geolocator.getCurrentPosition(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.high,
        timeLimit: Duration(seconds: 25),
      ),
    );
    return {
      'lat': p.latitude,
      'lon': p.longitude,
      'accuracy_m': p.accuracy.round(),
      'maps': 'https://maps.google.com/?q=${p.latitude},${p.longitude}',
      'at': p.timestamp.toIso8601String(),
    };
  }

  Future<Map<String, dynamic>> _calendar(String range) async {
    final plugin = DeviceCalendarPlugin();
    var granted = (await plugin.hasPermissions()).data ?? false;
    if (!granted) granted = (await plugin.requestPermissions()).data ?? false;
    if (!granted) throw const _Refused('Calendar permission was not granted.');

    final today = DateTime.now();
    final start = DateTime(today.year, today.month, today.day)
        .add(Duration(days: range == 'tomorrow' ? 1 : 0));
    final end = start.add(Duration(days: range == 'week' ? 7 : 1));
    final calendars = (await plugin.retrieveCalendars()).data ?? const [];
    final events = <Map<String, dynamic>>[];
    for (final cal in calendars) {
      final id = cal.id;
      if (id == null) continue;
      final res = await plugin.retrieveEvents(
          id, RetrieveEventsParams(startDate: start, endDate: end));
      for (final e in res.data ?? const <Event>[]) {
        events.add({
          'title': e.title ?? '(no title)',
          'start': e.start?.toLocal().toIso8601String(),
          'end': e.end?.toLocal().toIso8601String(),
          'all_day': e.allDay ?? false,
          if ((e.location ?? '').isNotEmpty) 'location': e.location,
          'calendar': cal.name,
        });
      }
    }
    events.sort((a, b) => '${a['start']}'.compareTo('${b['start']}'));
    return {
      'range': range,
      'from': start.toIso8601String(),
      'to': end.toIso8601String(),
      'events': events.take(50).toList(),
    };
  }

  Future<Map<String, dynamic>> _contacts(String query) async {
    if (query.trim().isEmpty) throw const _Refused('No name to search for.');
    final perms = FlutterContacts.permissions;
    if (!await perms.has(PermissionType.read)) {
      final status = await perms.request(PermissionType.read);
      if (status != PermissionStatus.granted && status != PermissionStatus.limited) {
        throw const _Refused('Contacts permission was not granted.');
      }
    }
    final found = await FlutterContacts.getAll(
      properties: {ContactProperty.phone, ContactProperty.email},
      filter: ContactFilter.name(query.trim()),
      limit: 10,
    );
    return {
      'query': query,
      'contacts': [
        for (final c in found)
          {
            'name': c.displayName ?? '',
            'phones': [for (final p in c.phones) p.number],
            'emails': [for (final e in c.emails) e.address],
          },
      ],
    };
  }

  Future<Map<String, dynamic>> _photo(GajalaApi api) async {
    final shot = await ImagePicker().pickImage(
      source: ImageSource.camera,
      maxWidth: 2000,
      imageQuality: 85,
    );
    if (shot == null) throw const _Refused('You cancelled the photo.');
    final path = await api.uploadImage(await shot.readAsBytes(), shot.name);
    return {'path': path};
  }

  Future<Map<String, dynamic>> _status() async {
    final battery = Battery();
    return {
      'battery_pct': await battery.batteryLevel,
      'charging': switch (await battery.batteryState) {
        BatteryState.charging || BatteryState.full => true,
        _ => false,
      },
    };
  }
}

class _Refused implements Exception {
  final String reason;
  const _Refused(this.reason);
}
