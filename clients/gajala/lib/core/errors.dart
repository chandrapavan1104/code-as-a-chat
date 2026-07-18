// Reports Gajala crashes/errors to the server (/api/clienterror) so the fix
// agent can see what actually broke. Self-contained (reads the stored config,
// like the widget bridge) and throttled so an error loop can't flood the store.

import 'dart:async';
import 'dart:ui';
import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'storage.dart';

class ErrorReporter {
  static final Map<String, DateTime> _recent = {};
  static const _throttle = Duration(seconds: 30);

  static Future<void> report(String kind, String message,
      {String? stack, Map<String, dynamic>? context}) async {
    if (message.trim().isEmpty) return;
    final key = '$kind|$message';
    final now = DateTime.now();
    final last = _recent[key];
    if (last != null && now.difference(last) < _throttle) return;
    _recent[key] = now;
    try {
      final cfg = await Storage.loadConfig();
      if (cfg == null) return;
      final dio = Dio(BaseOptions(
        baseUrl: cfg.url,
        headers: {'X-API-Token': cfg.token},
        connectTimeout: const Duration(seconds: 6),
        receiveTimeout: const Duration(seconds: 10),
      ));
      await dio.post('/api/clienterror', data: {
        'kind': kind,
        'message': message,
        'stack': stack,
        'context': context ?? {},
      });
    } catch (_) {/* reporting must never throw */}
  }

  /// Install global framework + async error handlers.
  static void install() {
    final prev = FlutterError.onError;
    FlutterError.onError = (details) {
      prev?.call(details);
      report('flutter', details.exceptionAsString(),
          stack: details.stack?.toString(),
          context: {'library': details.library ?? ''});
    };
    PlatformDispatcher.instance.onError = (error, stack) {
      report('async', error.toString(), stack: stack.toString());
      return true;
    };
  }
}
