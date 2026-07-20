import 'dart:math';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// Persists the connection config (server URL + API token) encrypted, plus a
/// stable per-install session id so the server keeps conversation memory.
class Storage {
  static const _s = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );

  static const _kUrl = 'server_url';
  static const _kToken = 'api_token';
  static const _kSession = 'session_id';
  static const _kTheme = 'theme_mode';
  static const _kFavs = 'fav_skills';

  /// Skill tiles pinned to the home screen, in display order.
  /// Empty list = never set, so the dashboard seeds a sensible default.
  static Future<List<String>> favorites() async {
    final raw = await _s.read(key: _kFavs);
    if (raw == null || raw.isEmpty) return const [];
    return raw.split(',').where((s) => s.isNotEmpty).toList();
  }

  static Future<void> setFavorites(List<String> names) async =>
      _s.write(key: _kFavs, value: names.join(','));

  static Future<String> themeMode() async =>
      await _s.read(key: _kTheme) ?? 'system';
  static Future<void> setThemeMode(String mode) async =>
      _s.write(key: _kTheme, value: mode);

  static Future<({String url, String token})?> loadConfig() async {
    final url = await _s.read(key: _kUrl);
    final token = await _s.read(key: _kToken);
    if (url == null || token == null || url.isEmpty || token.isEmpty) return null;
    return (url: url, token: token);
  }

  static Future<void> saveConfig(String url, String token) async {
    await _s.write(key: _kUrl, value: url.replaceAll(RegExp(r'/+$'), ''));
    await _s.write(key: _kToken, value: token);
  }

  static Future<void> clearConfig() async {
    await _s.delete(key: _kUrl);
    await _s.delete(key: _kToken);
  }

  static Future<String> sessionId() async {
    var sid = await _s.read(key: _kSession);
    if (sid == null) {
      final r = Random();
      sid = 'app:${List.generate(8, (_) => 'abcdefghijklmnopqrstuvwxyz0123456789'[r.nextInt(36)]).join()}';
      await _s.write(key: _kSession, value: sid);
    }
    return sid;
  }
}
