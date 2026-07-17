// Home-screen widgets ↔ app bridge (Android).
//
// Two widgets talk to us:
//   • Gajala actions — Lock (background network), Ask / Dump (deep-links).
//   • Codaur — a usage glance refreshed by a background fetch.
//
// Tapping a "background" tile fires [widgetCallback] in a headless isolate even
// when the app is closed; "launch" tiles open the app and are routed by
// [handleWidgetLaunch]. Widget UI state is exchanged via HomeWidget key/values,
// which the Kotlin providers read in onUpdate().

import 'dart:async';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:home_widget/home_widget.dart';
import '../screens/chat_screen.dart';
import '../screens/notes_screen.dart';
import 'push.dart';
import 'storage.dart';

const _actionsWidget = 'GajalaActionsWidget';
const _codaurWidget = 'CodaurWidget';

/// Background entry point — MUST be top-level with this pragma so the headless
/// isolate can find it. Fires when a widget's background tile is tapped.
@pragma('vm:entry-point')
Future<void> widgetCallback(Uri? uri) async {
  switch (uri?.host) {
    case 'lock':
      await _macAction('lock', busy: 'Locking…', ok: 'Locked ✓');
      break;
    case 'wake':
      await _macAction('wake', busy: 'Waking…', ok: 'Woke ✓');
      break;
    case 'codaur':
      await refreshCodaurWidget();
      break;
  }
}

/// Call once at startup: register the background callback and warm the Codaur
/// glance so it isn't empty before the first tap.
Future<void> initHomeWidgets() async {
  try {
    await HomeWidget.registerInteractivityCallback(widgetCallback);
    unawaited(refreshCodaurWidget());
  } catch (_) {/* not Android / no widgets — ignore */}
}

/// Route a widget launch tile (Ask / Dump) once the navigator is ready. Handles
/// both a cold launch and taps while the app is already running.
void handleWidgetLaunch() {
  HomeWidget.initiallyLaunchedFromHomeWidget().then(_route);
  HomeWidget.widgetClicked.listen(_route);
}

void _route(Uri? uri) {
  final nav = Push.navigatorKey.currentState;
  if (uri == null || nav == null) return;
  switch (uri.host) {
    case 'ask':
      nav.push(MaterialPageRoute(
          builder: (_) => const ChatScreen(command: 'shell', title: 'Gajala')));
      break;
    case 'dump':
      nav.push(MaterialPageRoute(
          builder: (_) => const NotesScreen(openComposer: true)));
      break;
  }
}

/// A short-lived HTTP client built from the saved connection config. Works in
/// the background isolate (secure storage plugins are registered there too).
Future<Dio?> _client() async {
  final cfg = await Storage.loadConfig();
  if (cfg == null) return null;
  return Dio(BaseOptions(
    baseUrl: cfg.url,
    headers: {'X-API-Token': cfg.token},
    connectTimeout: const Duration(seconds: 8),
    receiveTimeout: const Duration(seconds: 45),
  ));
}

Future<void> _setMacStatus(String s) async {
  await HomeWidget.saveWidgetData<String>('mac_status', s);
  await HomeWidget.updateWidget(name: _actionsWidget, androidName: _actionsWidget);
}

/// Fire a mac quick action (lock / wake) in the background and reflect the
/// outcome on the widget's status line.
Future<void> _macAction(String action,
    {required String busy, required String ok}) async {
  await _setMacStatus(busy);
  try {
    final dio = await _client();
    if (dio == null) {
      await _setMacStatus('Not connected — open the app');
      return;
    }
    // No session_id → this quick action stays out of chat history.
    await dio.post('/run', data: {'command': 'mac', 'prompt': action});
    await _setMacStatus('$ok · ${_clock()}');
  } catch (_) {
    await _setMacStatus('Failed — tap to retry');
  }
}

/// Fetch /api/usage and push a compact per-provider glance into the widget.
Future<void> refreshCodaurWidget() async {
  try {
    final dio = await _client();
    if (dio == null) return;
    final r = await dio.get('/api/usage',
        options: Options(headers: {'Cache-Control': 'no-cache'}),
        queryParameters: {'_': DateTime.now().millisecondsSinceEpoch});
    final providers = List<Map<String, dynamic>>.from(r.data['providers'] ?? const []);
    final lines = providers.take(3).map(_codaurLine).toList();
    for (var i = 0; i < 3; i++) {
      await HomeWidget.saveWidgetData<String>(
          'codaur_line${i + 1}', i < lines.length ? lines[i] : '');
    }
    await HomeWidget.saveWidgetData<String>('codaur_updated', 'updated ${_clock()}');
    await HomeWidget.updateWidget(name: _codaurWidget, androidName: _codaurWidget);
  } catch (_) {/* leave the last glance in place */}
}

String _codaurLine(Map<String, dynamic> p) {
  final name = (p['provider'] ?? '?').toString();
  final today = p['today_tokens'];
  final limits = p['limits'] as List?;
  final pct = p['primary_pct'] ??
      (limits != null && limits.isNotEmpty ? limits.first['pct'] : null);
  final parts = <String>[name.padRight(7)];
  if (today != null) {
    parts.add(_humanTokens(today));
  } else if (p['events'] != null) {
    parts.add('${p['events']} steps');
  }
  if (pct != null) parts.add('· ${(pct as num).toStringAsFixed(0)}%');
  return parts.join(' ');
}

String _humanTokens(dynamic n) {
  final v = (n as num).toDouble();
  if (v >= 1e9) return '${(v / 1e9).toStringAsFixed(1)}B';
  if (v >= 1e6) return '${(v / 1e6).toStringAsFixed(1)}M';
  if (v >= 1e3) return '${(v / 1e3).toStringAsFixed(1)}K';
  return v.toStringAsFixed(0);
}

String _clock() {
  final t = DateTime.now();
  return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}
