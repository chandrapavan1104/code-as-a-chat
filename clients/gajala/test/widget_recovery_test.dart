import 'dart:async';
import 'dart:convert';
import 'package:dio/dio.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/widget_bridge.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  final saved = <String, dynamic>{};
  const channel = MethodChannel('home_widget');
  setUp(() {
    saved.clear();
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger.setMockMethodCallHandler(channel, (call) async {
      final args = call.arguments as Map;
      if (call.method == 'saveWidgetData') { saved[args['id'] as String] = args['data']; return true; }
      if (call.method == 'getWidgetData') { return saved[args['id']] ?? args['defaultValue']; }
      if (call.method == 'updateWidget') { return true; }
      return null;
    });
  });
  tearDown(() { widgetClientOverride = null; });

  Dio client(FutureOr<Object> Function(RequestOptions) result, {int status = 200}) {
    final dio = Dio(BaseOptions(baseUrl: 'http://test.invalid'));
    dio.interceptors.add(InterceptorsWrapper(onRequest: (options, handler) async {
      try { handler.resolve(Response(requestOptions: options, data: await result(options), statusCode: status)); }
      catch (e) { handler.reject(e as DioException); }
    }));
    return dio;
  }

  test('Refresh coalesces taps and recovers after a failed refresh', () async {
    final pending = Completer<Object>();
    var calls = 0;
    final started = Completer<void>();
    widgetClientOverride = () async => client((_) { calls++; if (!started.isCompleted) started.complete(); return pending.future; });
    final one = refreshCodaurWidget();
    final two = refreshCodaurWidget();
    await started.future.timeout(const Duration(seconds: 5));
    expect(calls, 1);
    pending.complete({'providers': [{'provider': 'claude', 'primary_pct': 0, 'secondary_pct': 12}]});
    await Future.wait([one, two]);
    final captured = saved['codaur_updated_epoch'];
    expect(saved['codaur_stale'], false);
    expect(saved['codaur_primary_pct2'], 0);
    widgetClientOverride = () async => client((o) => throw DioException(requestOptions: o, type: DioExceptionType.connectionError));
    await refreshCodaurWidget();
    expect(saved['codaur_stale'], true);
    expect(saved['codaur_updated_epoch'], captured);
    expect(saved['codaur_error'], contains('Connection failed'));
    widgetClientOverride = () async => client((_) => {'providers': [{'provider': 'claude', 'primary_pct': 7}]});
    await refreshCodaurWidget();
    expect(saved['codaur_stale'], false);
    expect(saved['codaur_primary_pct2'], 7);
  });

  test('Missing config and malformed usage never produce fresh zero quotas', () async {
    widgetClientOverride = () async => null;
    await refreshCodaurWidget();
    expect(saved['codaur_stale'], true);
    expect(saved['codaur_error'], contains('Not connected'));
    widgetClientOverride = () async => client((_) => {'wrong': []});
    await refreshCodaurWidget();
    expect(saved['codaur_error'], contains('Invalid server response'));
    expect(saved['codaur_updated_epoch'], isNull);
  });

  test('Mac timeout is not replayed and clears busy lease', () async {
    var calls = 0;
    widgetClientOverride = () async => client((o) {
      calls++;
      throw DioException(requestOptions: o, type: DioExceptionType.receiveTimeout);
    });
    await widgetCallback(Uri.parse('gajala://lock'));
    expect(calls, 1);
    expect(saved['mac_status'], contains('outcome uncertain'));
    expect(saved['mac_busy_until'], 0);
  });

  test('Structured unsuccessful response cannot display locked success', () {
    expect(macActionFailure({'result': 'Mac locked.', 'completion': {'status': 'failed'}}), isNotNull);
    expect(macActionFailure({'result': 'Something happened'}), isNotNull);
    expect(macActionFailure({'result': 'Mac display woken and kept awake'}), isNull);
  });

  test('Diagnostics are bounded and exclude transport credentials', () async {
    for (var i = 0; i < 40; i++) { await widgetDiagnostic('codaur', 'failed', category: 'authentication'); }
    final records = jsonDecode(saved['widget_diagnostics'] as String) as List;
    expect(records.length, 32);
    final report = await widgetDiagnosticsReport();
    expect(report, contains('authentication'));
    expect(report, isNot(contains('X-API-Token')));
  });
}
