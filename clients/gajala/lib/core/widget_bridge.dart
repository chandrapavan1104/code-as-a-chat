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
import 'dart:convert';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:home_widget/home_widget.dart';
import '../screens/chat_screen.dart';
import '../screens/notes_screen.dart';
import '../screens/usage_screen.dart';
import 'push.dart';
import 'storage.dart';

const _actionsWidget = 'GajalaActionsWidget';
const _codaurWidget = 'CodaurWidget';

/// Background entry point — MUST be top-level with this pragma so the headless
/// isolate can find it. Fires when a widget's background tile is tapped.
@pragma('vm:entry-point')
Future<void> widgetCallback(Uri? uri) async {
  await widgetDiagnostic(uri?.host ?? 'unknown', 'callback');
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
  } catch (_) {
    await widgetDiagnostic('registration', 'failed', category: 'plugin_unavailable');
  }
}

/// Route a widget launch tile (Ask / Dump) once the navigator is ready. Handles
/// both a cold launch and taps while the app is already running.
StreamSubscription<Uri?>? _launchSubscription;
Uri? _pendingLaunch;
bool _routingLaunch = false;

void handleWidgetLaunch() {
  HomeWidget.initiallyLaunchedFromHomeWidget().then(_route);
  _launchSubscription ??= HomeWidget.widgetClicked.listen(_route);
}

void _route(Uri? uri) {
  if (uri == null) return;
  _pendingLaunch = uri;
  unawaited(flushWidgetLaunch());
}

Future<void> flushWidgetLaunch() async {
  if (_routingLaunch || _pendingLaunch == null || Push.navigatorKey.currentState == null) return;
  _routingLaunch = true;
  try {
    if (await Storage.loadConfig() == null) return;
    final nav = Push.navigatorKey.currentState;
    if (nav == null || !nav.mounted) return;
    final uri = _pendingLaunch!;
    _pendingLaunch = null;
    switch (uri.host) {
      case 'ask':
        final selected = await Storage.selectedConversation();
        if (!nav.mounted) return;
        nav.push(MaterialPageRoute(builder: (_) => ChatScreen(
          sessionId: selected.sessionId, project: selected.project)));
        break;
      case 'dump':
        nav.push(MaterialPageRoute(builder: (_) => const NotesScreen(openComposer: true)));
        break;
      case 'usage':
        nav.push(MaterialPageRoute(builder: (_) => const UsageScreen()));
        break;
    }
  } finally { _routingLaunch = false; }
}

/// Injectable transport for regression tests; never persisted.
Future<Dio?> Function()? widgetClientOverride;
final _inflight = <String, Future<void>>{};
Future<void> _once(String key, Future<void> Function() operation) =>
  _inflight.putIfAbsent(key, () => operation().whenComplete(() { _inflight.remove(key); }));

String widgetErrorCategory(Object error) {
  if (error is DioException) {
    final code = error.response?.statusCode;
    if (code == 401 || code == 403) { return 'authentication'; }
    if (error.type == DioExceptionType.connectionTimeout ||
        error.type == DioExceptionType.receiveTimeout ||
        error.type == DioExceptionType.sendTimeout) return 'timeout';
    if (error.type == DioExceptionType.connectionError) return 'connectivity';
    if (code != null) return 'server_response';
  }
  if (error is FormatException || error is TypeError) return 'invalid_response';
  return 'callback_failed';
}

String widgetErrorMessage(String category) => switch (category) {
  'authentication' => 'Login rejected — reconnect in Gajala',
  'connectivity' => 'Connection failed — check Mac and Tailscale',
  'timeout' => 'Timed out — outcome uncertain; check before retrying',
  'not_configured' => 'Not connected — open Gajala',
  'invalid_response' => 'Invalid server response — open Gajala',
  _ => 'Widget failed — open Gajala for diagnostics',
};

Future<void> widgetDiagnostic(String action, String stage, {String? category, int? elapsedMs}) async {
  try {
    final raw = await HomeWidget.getWidgetData<String>('widget_diagnostics', defaultValue: '[]');
    final records = List<dynamic>.from(jsonDecode(raw ?? '[]'));
    records.add({'action': action, 'stage': stage, 'at': DateTime.now().toUtc().toIso8601String(),
      if (category != null) 'category': category, if (elapsedMs != null) 'duration_ms': elapsedMs});
    await HomeWidget.saveWidgetData<String>('widget_diagnostics',
      jsonEncode(records.skip(records.length > 32 ? records.length - 32 : 0).toList()));
  } catch (_) { /* Platform failure must not block recovery. */ }
}

Future<String> widgetDiagnosticsReport() async {
  final raw = await HomeWidget.getWidgetData<String>('widget_diagnostics', defaultValue: '[]');
  final native = await HomeWidget.getWidgetData<String>('widget_native_error', defaultValue: 'None');
  final nativeAt = await HomeWidget.getWidgetData<int>('widget_native_error_at', defaultValue: 0);
  return 'Gajala widget diagnostics\nNative worker: $native at $nativeAt\n${raw ?? "[]"}';
}

/// A short-lived HTTP client built from the saved connection config. Works in
/// the background isolate (secure storage plugins are registered there too).
Future<Dio?> _client() async {
  if (widgetClientOverride != null) return widgetClientOverride!();
  final cfg = await Storage.loadConfig();
  if (cfg == null) return null;
  return Dio(
    BaseOptions(
      baseUrl: cfg.url,
      headers: {'X-API-Token': cfg.token},
      connectTimeout: const Duration(seconds: 8),
      receiveTimeout: const Duration(seconds: 45),
    ),
  );
}

Future<void> _setMacStatus(String s) async {
  await HomeWidget.saveWidgetData<String>('mac_status', s);
  await HomeWidget.saveWidgetData<int>('mac_busy_until', s.endsWith('…') ? DateTime.now().millisecondsSinceEpoch + 70000 : 0);
  await HomeWidget.updateWidget(
    name: _actionsWidget,
    androidName: _actionsWidget,
  );
}

/// `/run` returns tool failures as a normal JSON response so chat clients can
/// display the explanation. A widget has no reply bubble, so it must inspect
/// that result instead of treating every HTTP 200 as success.
String? macActionFailure(Object? data) {
  if (data is! Map) return 'Invalid response from Mac';
  final result = data['result']?.toString().trim() ?? '';
  if (result.isEmpty) return 'No response from Mac';
  final completion = data['completion'];
  if (completion is Map && completion['status'] != 'completed') return 'Mac action not confirmed';
  if (result.startsWith('[mac]')) return result;
  if (result.startsWith('Mac locked.') || result.startsWith('Mac display woken')) return null;
  return 'Mac action not confirmed';
}

/// Fire a mac quick action (lock / wake) in the background and reflect the
/// outcome on the widget's status line.
Future<void> _macAction(
  String action, {
  required String busy,
  required String ok,
}) => _once(action, () async {
  final started = DateTime.now();
  Dio? dio;
  await _setMacStatus(busy);
  await widgetDiagnostic(action, 'started');
  try {
    dio = await _client();
    if (dio == null) {
      await widgetDiagnostic(action, 'failed', category: 'not_configured');
      await _setMacStatus(widgetErrorMessage('not_configured'));
      return;
    }
    // No session_id → this quick action stays out of chat history.
    final response = await dio.post(
      '/run',
      data: {'command': 'mac', 'prompt': action},
    );
    final failure = macActionFailure(response.data);
    if (failure != null) {
      await _setMacStatus('Failed — ${failure.replaceFirst('[mac] ', '')}');
      return;
    }
    await _setMacStatus('$ok · ${_clock()}');
    await widgetDiagnostic(action, 'completed', elapsedMs: DateTime.now().difference(started).inMilliseconds);
  } catch (error) {
    final category = widgetErrorCategory(error);
    await widgetDiagnostic(action, 'failed', category: category,
      elapsedMs: DateTime.now().difference(started).inMilliseconds);
    await _setMacStatus(widgetErrorMessage(category));
  } finally { dio?.close(); }
});

/// Fetch /api/usage and push structured provider data into the native widget.
Future<void> refreshCodaurWidget() => _once('codaur', _refreshCodaurWidget);

Future<void> _refreshCodaurWidget() async {
  final started = DateTime.now();
  Dio? dio;
  await widgetDiagnostic('codaur', 'started');
  try {
    dio = await _client();
    if (dio == null) throw const _WidgetNotConfigured();
    final r = await dio.get(
      '/api/usage',
      options: Options(headers: {'Cache-Control': 'no-cache'}),
      queryParameters: {'_': DateTime.now().millisecondsSinceEpoch},
    );
    if (r.data is! Map || r.data['providers'] is! List || (r.data['providers'] as List).isEmpty) {
      throw const FormatException('usage providers missing');
    }
    final providers = List<Map<String, dynamic>>.from(r.data['providers']);
    const order = ['codex', 'claude', 'gemini', 'qwen'];
    providers.sort(
      (a, b) => order
          .indexOf((a['provider'] ?? '').toString())
          .compareTo(order.indexOf((b['provider'] ?? '').toString())),
    );
    final visible = [
      for (final provider in order)
        providers.where((p) => p['provider'] == provider).firstOrNull ??
            {'provider': provider},
    ];
    var todayTotal = 0.0;
    for (var i = 0; i < 4; i++) {
      final p = i < visible.length ? visible[i] : <String, dynamic>{};
      final today = p['today_tokens'];
      if (today is num) todayTotal += today.toDouble();
      final limits = p['limits'] as List?;
      final pct =
          p['primary_pct'] ??
          (limits != null && limits.isNotEmpty ? limits.first['pct'] : null);
      final provider = (p['provider'] ?? '').toString();
      final primary = p['primary_pct'] ?? (provider == 'gemini' ? pct : null);
      await HomeWidget.saveWidgetData<String>(
        'codaur_primary_label${i + 1}',
        provider == 'gemini' && p['primary_pct'] == null ? 'Day' : '5h',
      );
      final secondary = p['secondary_pct'];
      final stale = p['quota_stale'] == true;
      await HomeWidget.saveWidgetData<int>(
        'codaur_primary_pct${i + 1}',
        !stale && primary is num ? primary.clamp(0, 100).round() : 0,
      );
      await HomeWidget.saveWidgetData<int>(
        'codaur_secondary_pct${i + 1}',
        !stale && secondary is num ? secondary.clamp(0, 100).round() : 0,
      );
      await HomeWidget.saveWidgetData<bool>(
        'codaur_has_primary${i + 1}',
        !stale && primary is num,
      );
      await HomeWidget.saveWidgetData<bool>(
        'codaur_has_secondary${i + 1}',
        !stale && secondary is num,
      );
      await HomeWidget.saveWidgetData<String>(
        'codaur_name${i + 1}',
        provider.isEmpty ? '—' : _providerLabel(provider),
      );
      await HomeWidget.saveWidgetData<String>(
        'codaur_tokens${i + 1}',
        today is num ? '${_humanTokens(today)} today' : '— today',
      );
      await HomeWidget.saveWidgetData<String>(
        'codaur_limit${i + 1}',
        widgetQuotaLabel(p),
      );
      await HomeWidget.saveWidgetData<int>(
        'codaur_progress${i + 1}',
        pct is num ? pct.clamp(0, 100).round() : 0,
      );
      await HomeWidget.saveWidgetData<bool>(
        'codaur_has_limit${i + 1}',
        pct is num,
      );
    }
    await HomeWidget.saveWidgetData<String>(
      'codaur_summary',
      '${visible.length} MODELS  ·  ${_humanTokens(todayTotal)} TOKENS TODAY',
    );
    await HomeWidget.saveWidgetData<String>(
      'codaur_updated',
      'Updated ${_clock()}',
    );
    await HomeWidget.saveWidgetData<int>(
      'codaur_updated_epoch',
      DateTime.now().millisecondsSinceEpoch,
    );
    await HomeWidget.saveWidgetData<bool>('codaur_stale', false);
    await HomeWidget.saveWidgetData<String>('codaur_error', '');
    await HomeWidget.updateWidget(
      name: _codaurWidget,
      androidName: _codaurWidget,
    );
    await widgetDiagnostic('codaur', 'completed', elapsedMs: DateTime.now().difference(started).inMilliseconds);
  } catch (error) {
    final category = error is _WidgetNotConfigured ? 'not_configured' : widgetErrorCategory(error);
    await widgetDiagnostic('codaur', 'failed', category: category,
      elapsedMs: DateTime.now().difference(started).inMilliseconds);
    await HomeWidget.saveWidgetData<bool>('codaur_stale', true);
    await HomeWidget.saveWidgetData<String>(
      'codaur_error',
      widgetErrorMessage(category),
    );
    await HomeWidget.updateWidget(
      name: _codaurWidget,
      androidName: _codaurWidget,
    );
  } finally { dio?.close(); }
}

class _WidgetNotConfigured { const _WidgetNotConfigured(); }

String _providerLabel(String name) =>
    name.isEmpty ? name : '${name[0].toUpperCase()}${name.substring(1)}';

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

/// Keep missing quota distinct from zero and local token activity.
String widgetQuotaLabel(Map<String, dynamic> provider) {
  if (provider['provider'] == 'qwen') return 'Local · no quota';
  if (provider['quota_stale'] == true) return 'Quota unavailable · retry';
  final primary = provider['primary_pct'];
  final secondary = provider['secondary_pct'];
  if (primary is num || secondary is num) {
    return '5h ${primary is num ? '${primary.round()}%' : '—'} · Week ${secondary is num ? '${secondary.round()}%' : '—'}';
  }
  final limits = provider['limits'];
  if (limits is List && limits.isNotEmpty && limits.first['pct'] is num) {
    return '${limits.first['label']} ${(limits.first['pct'] as num).round()}%';
  }
  return 'Quota unavailable';
}
