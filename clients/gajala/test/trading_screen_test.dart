import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/push.dart';
import 'package:gajala/core/state.dart';
import 'package:gajala/screens/trading_screen.dart';

Map<String, dynamic> ticket({int id = 7, String mode = 'paper', String status = 'proposed'}) => {
      'id': id, 'mode': mode, 'symbol': 'ABCD', 'side': 'buy', 'notional_usd': 20.0,
      'limit_price': 9.5, 'thesis_ref': 'memos/ABCD.md', 'reason': 'cheap and unloved',
      'status': status, 'gate_notes': '', 'expires_at':
          DateTime.now().add(const Duration(hours: 5)).millisecondsSinceEpoch / 1000,
      'updated_at': DateTime.now().millisecondsSinceEpoch / 1000,
    };

class FakeTradeApi extends GajalaApi {
  FakeTradeApi() : super('http://localhost', 'test');
  bool enabled = true, paused = false, thisDevice = true, paired = true;
  String mode = 'paper';
  List<Map<String, dynamic>> open = [ticket()];
  List<Map<String, dynamic>> watch = [
    {'symbol': 'ABCD', 'status': 'ELIGIBLE', 'memo': 'memos/ABCD.md', 'confirmed': true},
    {'symbol': 'NEWW', 'status': 'ELIGIBLE', 'memo': 'memos/NEWW.md', 'confirmed': false},
    {'symbol': 'WAIT', 'status': 'WATCH', 'memo': '', 'confirmed': false},
  ];
  final calls = <String>[];
  int refreshes = 0;
  String? approvedWithKey;

  @override
  Future<Map<String, dynamic>> tradeOverview({bool refresh = false, String? deviceKey}) async {
    if (refresh) refreshes++;
    return {
      'enabled': enabled, 'mode': mode, 'paused': paused, 'auto_approve': false,
      'mood': 'neutral', 'committed_today': 10.0,
      'limits': {'max_order_usd': 25, 'max_daily_usd': 50, 'max_positions': 5,
                 'max_position_pct': 0.25, 'cash_floor_pct': 0.2, 'ticket_hours': 24},
      'account': {'equity': 100.0, 'cash': 80.0, 'positions': {'ABCD': 20.0},
                  'peak_equity': 100.0, 'drawdown': 0.0},
      'account_as_of': DateTime.now().millisecondsSinceEpoch / 1000, 'account_error': null,
      'open_tickets': open,
      'recent_tickets': [
        {...ticket(id: 3, status: 'rejected'), 'gate_notes': 'ZZZZ is not on the vetted watchlist',
         'symbol': 'ZZZZ'},
      ],
      'watchlist': watch,
      'reports': [{'path': 'reports/2026-11-12-weekly-screen.md',
                   'title': '2026 11 12 weekly screen', 'modified': 1.0}],
      'phone': {'paired': paired, 'this_device': thisDevice},
    };
  }

  @override
  Future<Map<String, dynamic>> tradeTicket(int id) async =>
      {'ticket': open.firstWhere((t) => t['id'] == id), 'history': []};

  @override
  Future<Map<String, dynamic>> tradeApprove(int id, {String? deviceKey}) async {
    calls.add('approve $id');
    approvedWithKey = deviceKey;
    open = [];
    return {...ticket(id: id), 'status': 'filled'};
  }

  @override
  Future<Map<String, dynamic>> tradeReject(int id) async {
    calls.add('reject $id');
    open = [];
    return {...ticket(id: id), 'status': 'rejected'};
  }

  @override
  Future<void> tradeConfirm(String symbol, bool confirmed) async =>
      calls.add('${confirmed ? 'confirm' : 'unconfirm'} $symbol');

  @override
  Future<void> tradePause(bool paused) async {
    calls.add(paused ? 'pause' : 'resume');
    this.paused = paused;
  }

  @override
  Future<Map<String, dynamic>> shellPair(String deviceKey, String label) async {
    calls.add('pair');
    thisDevice = true;
    return {'paired': true};
  }
}

late bool authAsked;
late bool authAnswer;

Future<void> pump(WidgetTester tester, FakeTradeApi api, {Widget? home}) async {
  tester.view.physicalSize = const Size(1080, 3200);
  tester.view.devicePixelRatio = 2.5;
  addTearDown(tester.view.reset);
  await tester.pumpWidget(ProviderScope(
    overrides: [apiProvider.overrideWithValue(api)],
    child: MaterialApp(home: home ?? const TradingScreen()),
  ));
  await tester.pumpAndSettle();
}

void main() {
  setUp(() {
    authAsked = false;
    authAnswer = true;
    tradeAuthenticate = (reason) async {
      authAsked = true;
      return authAnswer;
    };
    tradeDeviceKey = () async => 'k' * 43;
  });

  testWidgets('paper ticket: approve confirms in plain words, needs no fingerprint', (tester) async {
    final api = FakeTradeApi();
    await pump(tester, api);
    expect(find.text('Needs your decision (1)'), findsOneWidget);
    expect(find.text('BUY ABCD'), findsOneWidget);
    expect(find.text('cheap and unloved'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('approve-7')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Buy 2 shares of ABCD at up to \$9.50'), findsWidgets);
    expect(find.textContaining('simulated'), findsWidgets);
    await tester.tap(find.byKey(const ValueKey('confirm-approve')));
    await tester.pumpAndSettle();
    expect(api.calls, ['approve 7']);
    expect(authAsked, isFalse);
    expect(find.textContaining('Filled'), findsOneWidget);
    expect(find.text('Nothing waiting for you'), findsOneWidget);   // reloaded
    expect(api.refreshes, greaterThan(0));
  });

  testWidgets('live ticket: fingerprint first, and the phone key travels', (tester) async {
    final api = FakeTradeApi()
      ..mode = 'live'
      ..open = [ticket(mode: 'live')];
    authAnswer = false;
    await pump(tester, api);
    await tester.tap(find.byKey(const ValueKey('approve-7')));
    await tester.pumpAndSettle();
    expect(find.text('Approve real order'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('confirm-approve')));
    await tester.pumpAndSettle();
    expect(authAsked, isTrue);
    expect(api.calls, isEmpty);                                     // fingerprint refused
    authAnswer = true;
    await tester.tap(find.byKey(const ValueKey('approve-7')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('confirm-approve')));
    await tester.pumpAndSettle();
    expect(api.calls, ['approve 7']);
    expect(api.approvedWithKey, 'k' * 43);
  });

  testWidgets('reject is one tap', (tester) async {
    final api = FakeTradeApi();
    await pump(tester, api);
    await tester.tap(find.byKey(const ValueKey('reject-7')));
    await tester.pumpAndSettle();
    expect(api.calls, ['reject 7']);
    expect(find.text('Rejected ABCD.'), findsOneWidget);
  });

  testWidgets('watchlist shows what needs the owner and confirms it', (tester) async {
    final api = FakeTradeApi();
    await pump(tester, api);
    expect(find.text('Needs your OK'), findsOneWidget);
    expect(find.text('Buyable'), findsOneWidget);
    expect(find.text('Watching'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Confirm'));
    await tester.pumpAndSettle();
    expect(find.text('Allow buying NEWW?'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Confirm').last);
    await tester.pumpAndSettle();
    expect(api.calls, ['confirm NEWW']);
  });

  testWidgets('the switch pauses at once and asks before resuming', (tester) async {
    final api = FakeTradeApi();
    await pump(tester, api);
    await tester.tap(find.byKey(const ValueKey('trading-switch')));
    await tester.pumpAndSettle();
    expect(api.calls, ['pause']);
    expect(find.text('Paused'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('trading-switch')));
    await tester.pumpAndSettle();
    expect(find.text('Turn trading back on?'), findsOneWidget);
    await tester.tap(find.text('Turn on'));
    await tester.pumpAndSettle();
    expect(api.calls, ['pause', 'resume']);
  });

  testWidgets('live mode on an unpaired phone offers pairing', (tester) async {
    final api = FakeTradeApi()
      ..mode = 'live'
      ..paired = false
      ..thisDevice = false;
    await pump(tester, api);
    expect(find.text('This phone can\'t approve real orders yet'), findsOneWidget);
    await tester.tap(find.text('Pair this phone'));
    await tester.pumpAndSettle();
    expect(api.calls, ['pair']);
    expect(find.text('This phone can\'t approve real orders yet'), findsNothing);
  });

  testWidgets('off: explains how to start, research stays readable', (tester) async {
    final api = FakeTradeApi()
      ..enabled = false
      ..open = [];
    await pump(tester, api);
    expect(find.text('Trading is off'), findsOneWidget);
    expect(find.textContaining('TRADING_ENABLED=true'), findsOneWidget);
    expect(find.text('Watchlist'), findsOneWidget);
    expect(find.text('Research reports'), findsOneWidget);
    expect(find.text('Holdings'), findsNothing);
  });

  testWidgets('recent activity says why the rules stopped a ticket', (tester) async {
    await pump(tester, FakeTradeApi());
    expect(find.textContaining('Stopped by the rules: ZZZZ is not on the vetted watchlist'),
        findsOneWidget);
  });

  testWidgets('a trade alert opens the ticket with its buttons', (tester) async {
    final api = FakeTradeApi();
    await pump(tester, api, home: Consumer(builder: (context, ref, _) => Scaffold(
      body: TextButton(onPressed: () => showTicketSheet(context, ref, 7),
          child: const Text('open')))));
    await tester.tap(find.text('open'));
    await tester.pumpAndSettle();
    expect(find.text('BUY ABCD'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('approve-7')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('confirm-approve')));
    await tester.pumpAndSettle();
    expect(api.calls, ['approve 7']);
    expect(find.text('BUY ABCD'), findsNothing);                     // sheet closed
  });

  test('push taps on trade alerts open Trading, focused on the ticket', () {
    int? opened = -1;
    Push.onOpenTrading = (id) => opened = id;
    Push.routeData({'type': 'trade_ticket', 'ref_id': '7'});
    expect(opened, 7);
    Push.routeData({'type': 'trade_status', 'ref_id': '9'});
    expect(opened, isNull);
    Push.onOpenTrading = null;
  });

  testWidgets('fits a small phone with large text', (tester) async {
    tester.view.physicalSize = const Size(1080, 2400);
    tester.view.devicePixelRatio = 3.0;                       // 360 x 800 points
    addTearDown(tester.view.reset);
    final api = FakeTradeApi()
      ..mode = 'live'
      ..open = [ticket(mode: 'live'), {...ticket(id: 8, mode: 'live'), 'symbol': 'GOOGL'}];
    await tester.pumpWidget(ProviderScope(
      overrides: [apiProvider.overrideWithValue(api)],
      child: MaterialApp(
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context).copyWith(textScaler: const TextScaler.linear(1.3)),
          child: child!),
        home: const TradingScreen()),
    ));
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.text('Needs your decision (2)'), findsOneWidget);
  });

  test('order summary is plain and honest about whole shares', () {
    expect(orderSummary(ticket()), 'Buy 2 shares of ABCD at up to \$9.50 each (at most \$19.00)');
    expect(maxCost(ticket()), 19.0);
  });
}
