import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'api.dart';
import 'models.dart';
import 'storage.dart';

/// App theme mode (system / light / dark), persisted.
class ThemeModeNotifier extends StateNotifier<ThemeMode> {
  ThemeModeNotifier() : super(ThemeMode.system) {
    _load();
  }
  Future<void> _load() async {
    final m = await Storage.themeMode();
    state = _parse(m);
  }
  static ThemeMode _parse(String m) => switch (m) {
        'light' => ThemeMode.light,
        'dark' => ThemeMode.dark,
        _ => ThemeMode.system,
      };
  Future<void> set(ThemeMode mode) async {
    state = mode;
    await Storage.setThemeMode(mode.name);
  }
}

final themeModeProvider =
    StateNotifierProvider<ThemeModeNotifier, ThemeMode>((ref) => ThemeModeNotifier());

/// Connection config — null until loaded / when disconnected.
class ConfigNotifier extends StateNotifier<({String url, String token})?> {
  ConfigNotifier() : super(null) {
    _load();
  }
  Future<void> _load() async => state = await Storage.loadConfig();

  Future<void> connect(String url, String token) async {
    await Storage.saveConfig(url, token);
    state = (url: url.replaceAll(RegExp(r'/+$'), ''), token: token);
  }

  Future<void> disconnect() async {
    await Storage.clearConfig();
    state = null;
  }
}

final configProvider =
    StateNotifierProvider<ConfigNotifier, ({String url, String token})?>(
        (ref) => ConfigNotifier());

/// The API client, rebuilt whenever config changes. Null when disconnected.
final apiProvider = Provider<GajalaApi?>((ref) {
  final c = ref.watch(configProvider);
  return c == null ? null : GajalaApi(c.url, c.token);
});

final skillsProvider = FutureProvider<List<Skill>>((ref) async {
  final api = ref.watch(apiProvider);
  if (api == null) return [];
  return api.skills();
});

final systemProvider = FutureProvider.autoDispose<SystemStats>((ref) async {
  final api = ref.watch(apiProvider);
  if (api == null) throw Exception('not connected');
  return api.system();
});

final notesProvider = FutureProvider.autoDispose<List<Note>>((ref) async {
  final api = ref.watch(apiProvider);
  if (api == null) return [];
  return api.notes();
});

final remindersProvider =
    FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  final api = ref.watch(apiProvider);
  if (api == null) return [];
  return api.reminders();
});

final projectsProvider = FutureProvider.autoDispose<Map<String, dynamic>>((ref) async {
  final api = ref.watch(apiProvider);
  if (api == null) return {};
  return api.projects();
});

final usageProvider = FutureProvider.autoDispose<List<Map<String, dynamic>>>((ref) async {
  final api = ref.watch(apiProvider);
  if (api == null) return [];
  return api.usage();
});

final sessionIdProvider = FutureProvider<String>((ref) => Storage.sessionId());
