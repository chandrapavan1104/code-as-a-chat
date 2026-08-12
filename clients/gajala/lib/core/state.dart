import 'dart:async';
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

final notesProvider = FutureProvider.autoDispose.family<List<Note>, String>((ref, status) async {
  final api = ref.watch(apiProvider);
  if (api == null) return [];
  return api.notes(status: status);
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

/// Night Shift queue (Tasks tab) — jobs + current settings.
final queueProvider = FutureProvider.autoDispose<
    ({
      List<QueueJob> jobs,
      Map<String, dynamic> settings,
      Map<String, dynamic> health,
    })>((ref) async {
  final timer = Timer(const Duration(seconds: 20), ref.invalidateSelf);
  ref.onDispose(timer.cancel);
  final api = ref.watch(apiProvider);
  if (api == null) {
    return (
      jobs: <QueueJob>[],
      settings: <String, dynamic>{},
      health: <String, dynamic>{},
    );
  }
  return api.queue();
});

/// Notification inbox (Alerts tab) — items + unread count.
final notificationsProvider = FutureProvider.autoDispose<
    ({List<AppNotification> items, int unread})>((ref) async {
  final api = ref.watch(apiProvider);
  if (api == null) return (items: <AppNotification>[], unread: 0);
  return api.notifications();
});

/// Live unread badge for the Alerts tab. Kept alive (not autoDispose) so the
/// bottom-nav badge stays populated while you're on other tabs; a push or a tab
/// visit invalidates it.
final unreadCountProvider = FutureProvider<int>((ref) async {
  final api = ref.watch(apiProvider);
  if (api == null) return 0;
  final r = await api.notifications();
  return r.unread;
});

/// Skill tiles pinned to the home screen, in display order. Persisted, so the
/// home screen shows what you actually use instead of every skill that exists.
class FavoritesNotifier extends StateNotifier<List<String>> {
  FavoritesNotifier() : super(const []) {
    Storage.favorites().then((v) => state = v);
  }

  /// Seeded once on first run so home isn't empty before you pin anything.
  /// Order matters: the first four become the big cards on home.
  static const defaults = ['usage', 'notes', 'projects', 'ports', 'sysmon', 'sessions'];

  Future<void> _persist(List<String> v) async {
    state = v;
    await Storage.setFavorites(v);
  }

  bool isPinned(String name) => state.contains(name);

  Future<void> toggle(String name) async => _persist(state.contains(name)
      ? (List.of(state)..remove(name))
      : [...state, name]);

  /// [newIndex] is already adjusted for the removed item (onReorderItem).
  Future<void> reorder(int oldIndex, int newIndex) async {
    final v = List.of(state);
    v.insert(newIndex.clamp(0, v.length - 1), v.removeAt(oldIndex));
    await _persist(v);
  }

  /// First run: adopt the defaults that actually exist on this server.
  Future<void> seedIfEmpty(List<String> available) async {
    if (state.isNotEmpty) return;
    final seed = defaults.where(available.contains).toList();
    if (seed.isNotEmpty) await _persist(seed);
  }
}

final favoritesProvider =
    StateNotifierProvider<FavoritesNotifier, List<String>>((ref) => FavoritesNotifier());

