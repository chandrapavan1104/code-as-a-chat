import 'dart:async';
import 'dart:math';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api.dart';
import '../core/app_lock.dart';
import '../core/state.dart';
import '../core/storage.dart';
import '../core/theme.dart';

/// Library → Trading: the owner's view of the trading desk on the Mac.
///
/// One screen, top to bottom: what state the desk is in, the tickets waiting for
/// a decision (Approve / Reject), what is held, the watchlist (where the owner
/// confirms each name research wants to make buyable), research reports, recent
/// activity, and the safety rules. Approving a live, real-money order asks for
/// the fingerprint / screen lock and sends the paired phone's key, which the Mac
/// requires; paper approvals need neither.
class TradingScreen extends ConsumerStatefulWidget {
  final int? focusTicketId;
  const TradingScreen({super.key, this.focusTicketId});
  @override
  ConsumerState<TradingScreen> createState() => _TradingScreenState();
}

/// Overridable in tests.
Future<bool> Function(String reason) tradeAuthenticate = AppLock.authenticate;
Future<String> Function() tradeDeviceKey = Storage.shellDeviceKey;

String money(num? v) => v == null ? '—' : '\$${v.toStringAsFixed(2)}';

String detailOf(Object e) {
  if (e is DioException) {
    final data = e.response?.data;
    if (data is Map && data['detail'] != null) return data['detail'].toString();
  }
  return friendlyError(e);
}

String _ago(num? ts) {
  if (ts == null) return '';
  final d = DateTime.now().difference(
      DateTime.fromMillisecondsSinceEpoch((ts * 1000).round()));
  if (d.inMinutes < 1) return 'just now';
  if (d.inHours < 1) return '${d.inMinutes} min ago';
  if (d.inDays < 1) return '${d.inHours} h ago';
  return '${d.inDays} d ago';
}

String _expiresIn(num? ts) {
  if (ts == null) return '';
  final d = DateTime.fromMillisecondsSinceEpoch((ts * 1000).round())
      .difference(DateTime.now());
  if (d.isNegative) return 'expired';
  if (d.inHours >= 1) return 'expires in ${d.inHours} h';
  return 'expires in ${max(1, d.inMinutes)} min';
}

/// The most the order can cost (buys are whole shares at the limit price).
double maxCost(Map t) {
  final notional = (t['notional_usd'] as num).toDouble();
  final limit = (t['limit_price'] as num).toDouble();
  if (t['side'] == 'sell' || limit <= 0) return notional;
  return (notional / limit).floor() * limit;
}

/// What the order means in plain words: whole shares only.
String orderSummary(Map t) {
  final notional = (t['notional_usd'] as num).toDouble();
  final limit = (t['limit_price'] as num).toDouble();
  final side = t['side'] == 'sell' ? 'Sell' : 'Buy';
  final shares = limit > 0 ? (notional / limit).floor() : 0;
  if (t['side'] == 'sell') {
    return '$side up to ${money(notional)} of ${t['symbol']} at no less than ${money(limit)} a share';
  }
  return '$side $shares share${shares == 1 ? '' : 's'} of ${t['symbol']} at up to '
      '${money(limit)} each (at most ${money(shares * limit)})';
}

// ── decisions, shared with the Alerts tab ───────────────────────────────────

/// Ask, authenticate when it is real money, approve, and say what happened.
/// Returns true when the ticket was decided (whatever the outcome).
Future<bool> approveTicket(BuildContext context, GajalaApi api, Map ticket) async {
  final live = ticket['mode'] == 'live';
  final ok = await showModalBottomSheet<bool>(
    context: context,
    showDragHandle: true,
    builder: (ctx) => SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 20),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text('Approve this order?', style: Theme.of(ctx).textTheme.titleLarge),
            const SizedBox(height: 10),
            _ModeChip(live ? 'live' : 'paper'),
            const SizedBox(height: 10),
            Text(orderSummary(ticket), style: const TextStyle(fontSize: 15.5)),
            if ((ticket['reason'] ?? '').toString().isNotEmpty) ...[
              const SizedBox(height: 8),
              Text('Why: ${ticket['reason']}', style: TextStyle(color: ctx.pal.textDim)),
            ],
            const SizedBox(height: 8),
            Text(
              live
                  ? 'This places a real limit order with your broker, good for today. '
                      'The rules are checked again with a fresh price first.'
                  : 'Paper trade: simulated, no real money moves.',
              style: TextStyle(fontSize: 12.5, color: ctx.pal.textDim),
            ),
            const SizedBox(height: 18),
            FilledButton(
              key: const ValueKey('confirm-approve'),
              onPressed: () => Navigator.pop(ctx, true),
              child: Text(live ? 'Approve real order' : 'Approve'),
            ),
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          ],
        ),
      ),
    ),
  );
  if (ok != true || !context.mounted) return false;
  if (live && !await tradeAuthenticate('Approve a real-money order')) return false;
  if (!context.mounted) return false;
  final messenger = ScaffoldMessenger.of(context);
  try {
    final result = await api.tradeApprove((ticket['id'] as num).toInt(),
        deviceKey: await tradeDeviceKey());
    messenger.showSnackBar(SnackBar(content: Text(switch (result['status']) {
      'filled' => 'Filled: ${result['symbol']} ${live ? '' : '(paper)'}'.trim(),
      'submitted' => 'Order sent; waiting for it to fill.',
      'rejected' => 'Not placed: it no longer passes the rules. ${result['gate_notes'] ?? ''}',
      'expired' => 'That ticket had expired.',
      'failed' => 'The order failed. Trading may be paused; check the desk.',
      _ => 'Ticket is now ${result['status']}.',
    })));
    return true;
  } catch (e) {
    messenger.showSnackBar(SnackBar(content: Text(detailOf(e))));
    return false;
  }
}

Future<bool> rejectTicket(BuildContext context, GajalaApi api, Map ticket) async {
  final messenger = ScaffoldMessenger.of(context);
  try {
    await api.tradeReject((ticket['id'] as num).toInt());
    messenger.showSnackBar(SnackBar(content: Text('Rejected ${ticket['symbol']}.')));
    return true;
  } catch (e) {
    messenger.showSnackBar(SnackBar(content: Text(detailOf(e))));
    return false;
  }
}

/// A trade-ticket alert opens this: the ticket, its reason, and the two buttons.
Future<void> showTicketSheet(BuildContext context, WidgetRef ref, int ticketId) async {
  final api = ref.read(apiProvider);
  if (api == null) return;
  Map? ticket;
  String? error;
  try {
    ticket = (await api.tradeTicket(ticketId))['ticket'] as Map?;
  } catch (e) {
    error = detailOf(e);
  }
  if (!context.mounted) return;
  await showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    isScrollControlled: true,
    builder: (ctx) => SafeArea(
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        child: ticket == null
            ? Text(error ?? 'Ticket not found.')
            : TicketCard(
                ticket: ticket,
                onApprove: () async {
                  if (await approveTicket(ctx, api, ticket!) && ctx.mounted) Navigator.pop(ctx);
                },
                onReject: () async {
                  if (await rejectTicket(ctx, api, ticket!) && ctx.mounted) Navigator.pop(ctx);
                },
                onMemo: (path) => openTradeDoc(ctx, path),
              ),
      ),
    ),
  );
}

void openTradeDoc(BuildContext context, String path) {
  Navigator.of(context).push(MaterialPageRoute(builder: (_) => TradeDocScreen(path: path)));
}

// ── the screen ───────────────────────────────────────────────────────────────

class _TradingScreenState extends ConsumerState<TradingScreen> with WidgetsBindingObserver {
  Map<String, dynamic>? _data;
  String? _error;
  bool _loading = true;
  bool _busy = false;
  Timer? _poll;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load(first: true);
    _poll = Timer.periodic(const Duration(seconds: 30), (_) => _load());
  }

  @override
  void dispose() {
    _poll?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _load();
  }

  Future<void> _load({bool refresh = false, bool first = false}) async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    try {
      final data = await api.tradeOverview(refresh: refresh, deviceKey: await tradeDeviceKey());
      if (!mounted) return;
      setState(() {
        _data = data;
        _error = null;
        _loading = false;
      });
      final focus = widget.focusTicketId;
      if (first && focus != null &&
          !(data['open_tickets'] as List).any((t) => t['id'] == focus)) {
        // Already decided elsewhere: say so instead of showing nothing.
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text('Ticket #$focus is no longer waiting for you.')));
        }
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = detailOf(e);
          _loading = false;
        });
      }
    }
  }

  Future<void> _act(Future<bool> Function() action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      if (await action()) await _load(refresh: true);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<bool> _call(Future<void> Function(GajalaApi api) fn) async {
    final api = ref.read(apiProvider)!;
    try {
      await fn(api);
      return true;
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(detailOf(e))));
      }
      return false;
    }
  }

  @override
  Widget build(BuildContext context) {
    final d = _data;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Trading'),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            icon: const Icon(Icons.refresh),
            onPressed: () => _load(refresh: true),
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : d == null
              ? _message(Icons.cloud_off_outlined, _error ?? 'Could not reach the Mac.',
                  action: TextButton(onPressed: () => _load(refresh: true), child: const Text('Retry')))
              : RefreshIndicator(
                  onRefresh: () => _load(refresh: true),
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
                    children: [
                      _statusCard(d),
                      if (d['enabled'] == true && d['mode'] == 'live') _pairingBanner(d),
                      _ticketsSection(d),
                      _holdingsSection(d),
                      _watchlistSection(d),
                      _reportsSection(d),
                      _activitySection(d),
                      _rulesSection(d),
                    ],
                  ),
                ),
    );
  }

  Widget _message(IconData icon, String text, {Widget? action}) => Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(icon, size: 40),
            const SizedBox(height: 12),
            Text(text, textAlign: TextAlign.center),
            if (action != null) ...[const SizedBox(height: 12), action],
          ]),
        ),
      );

  Widget _section(String title, List<Widget> children, {String? subtitle}) => Padding(
        padding: const EdgeInsets.only(top: 20),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: Theme.of(context).textTheme.titleMedium),
          if (subtitle != null)
            Padding(
              padding: const EdgeInsets.only(top: 2),
              child: Text(subtitle, style: TextStyle(fontSize: 12.5, color: context.pal.textDim)),
            ),
          const SizedBox(height: 8),
          ...children,
        ]),
      );

  // ── status ──────────────────────────────────────────────────────────────
  Widget _statusCard(Map d) {
    final pal = context.pal;
    if (d['enabled'] != true) {
      return Card(
        margin: const EdgeInsets.only(top: 8),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Row(children: [
              Icon(Icons.power_settings_new, size: 20),
              SizedBox(width: 8),
              Text('Trading is off', style: TextStyle(fontSize: 17, fontWeight: FontWeight.w600)),
            ]),
            const SizedBox(height: 8),
            const Text('Nothing is proposed or traded. Research below stays readable.'),
            const SizedBox(height: 10),
            Text(
              'To try it safely with simulated money: on the Mac, add '
              'TRADING_ENABLED=true to .env and restart Gajala\'s server. Add '
              'FINANCE_ROUTINES_ENABLED=true for the scheduled research runs.',
              style: TextStyle(fontSize: 12.5, color: pal.textDim),
            ),
          ]),
        ),
      );
    }
    final account = d['account'] as Map?;
    final limits = d['limits'] as Map;
    final today = (d['committed_today'] as num?)?.toDouble() ?? 0;
    final daily = (limits['max_daily_usd'] as num).toDouble();
    final paused = d['paused'] == true;
    return Card(
      margin: const EdgeInsets.only(top: 8),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 12, 8, 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Flexible(child: _ModeChip(d['mode'].toString())),
            const Spacer(),
            Text(paused ? 'Paused' : 'Active',
                style: TextStyle(color: paused ? GajalaColors.warn : GajalaColors.ok,
                    fontWeight: FontWeight.w600)),
            Switch(
              key: const ValueKey('trading-switch'),
              value: !paused,
              onChanged: _busy ? null : (on) => _togglePause(!on),
            ),
          ]),
          _MoodChip(d['mood']?.toString()),
          const SizedBox(height: 6),
          if (account == null)
            Text(d['account_error']?.toString() ?? 'Reading the account…',
                style: TextStyle(color: pal.textDim))
          else ...[
            Text(money(account['equity'] as num?),
                style: const TextStyle(fontSize: 28, fontWeight: FontWeight.w700)),
            Text(
              'Cash ${money(account['cash'] as num?)}'
              '${((account['drawdown'] as num?) ?? 0) > 0.005 ? '  ·  down ${(((account['drawdown'] as num) * 100)).toStringAsFixed(0)}% from peak' : ''}'
              '${d['account_as_of'] != null ? '  ·  ${_ago(d['account_as_of'] as num)}' : ''}',
              style: TextStyle(color: pal.textDim, fontSize: 12.5),
            ),
            if (d['account_error'] != null)
              Text(d['account_error'].toString(),
                  style: const TextStyle(color: GajalaColors.warn, fontSize: 12)),
          ],
          const SizedBox(height: 12),
          Text('Today\'s buys: ${money(today)} of ${money(daily)}',
              style: const TextStyle(fontSize: 13)),
          const SizedBox(height: 4),
          Padding(
            padding: const EdgeInsets.only(right: 8),
            child: LinearProgressIndicator(
              value: daily > 0 ? (today / daily).clamp(0, 1).toDouble() : 0,
              minHeight: 6,
              borderRadius: BorderRadius.circular(3),
            ),
          ),
          if (paused)
            Padding(
              padding: const EdgeInsets.only(top: 10, right: 8),
              child: Text(
                'Paused: no ticket can be approved. If the desk paused itself after an '
                'unclear order, check your broker app before switching it back on.',
                style: TextStyle(fontSize: 12.5, color: pal.textDim),
              ),
            ),
        ]),
      ),
    );
  }

  Future<void> _togglePause(bool pause) async {
    if (!pause) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('Turn trading back on?'),
          content: const Text('Tickets can be approved again. If the desk paused itself, '
              'first make sure your broker app shows nothing unexpected.'),
          actions: [
            TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
            FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Turn on')),
          ],
        ),
      );
      if (ok != true) return;
    }
    await _act(() => _call((api) => api.tradePause(pause)));
  }

  Widget _pairingBanner(Map d) {
    final phone = d['phone'] as Map? ?? const {};
    if (phone['this_device'] == true) return const SizedBox.shrink();
    final pairedElsewhere = phone['paired'] == true;
    return Card(
      margin: const EdgeInsets.only(top: 12),
      color: GajalaColors.warn.withValues(alpha: 0.12),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('This phone can\'t approve real orders yet',
              style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          Text(pairedElsewhere
              ? 'Live approvals are tied to another paired device. To use this phone, run '
                  'scripts/remote-shell unbind on the Mac, then pair here.'
              : 'Live orders are approved only from your paired phone. Pairing is the same '
                  'one-time step the Terminal uses.'),
          if (!pairedElsewhere)
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton.tonal(
                onPressed: _busy
                    ? null
                    : () => _act(() async {
                          final api = ref.read(apiProvider)!;
                          try {
                            await api.shellPair(await tradeDeviceKey(), 'Gajala phone');
                            return true;
                          } catch (e) {
                            if (mounted) {
                              ScaffoldMessenger.of(context)
                                  .showSnackBar(SnackBar(content: Text(detailOf(e))));
                            }
                            return false;
                          }
                        }),
                child: const Text('Pair this phone'),
              ),
            ),
        ]),
      ),
    );
  }

  // ── tickets ─────────────────────────────────────────────────────────────
  Widget _ticketsSection(Map d) {
    final open = (d['open_tickets'] as List).cast<Map>();
    final waiting = open.where((t) => t['status'] == 'proposed').toList();
    final inFlight = open.where((t) => t['status'] != 'proposed').toList();
    if (d['enabled'] != true && open.isEmpty) return const SizedBox.shrink();
    final api = ref.read(apiProvider)!;
    return _section(
      waiting.isEmpty ? 'Nothing waiting for you' : 'Needs your decision (${waiting.length})',
      [
        if (waiting.isEmpty)
          Text('New ideas from research show up here and as a notification.',
              style: TextStyle(color: context.pal.textDim)),
        for (final t in waiting)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: TicketCard(
              key: ValueKey('ticket-${t['id']}'),
              ticket: t,
              highlight: t['id'] == widget.focusTicketId,
              busy: _busy,
              onApprove: () => _act(() => approveTicket(context, api, t)),
              onReject: () => _act(() => rejectTicket(context, api, t)),
              onMemo: (path) => openTradeDoc(context, path),
            ),
          ),
        for (final t in inFlight)
          ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.hourglass_top, size: 20),
            title: Text(orderSummary(t)),
            subtitle: Text(t['status'] == 'submitted' ? 'Sent; waiting for the fill' : 'Sending…'),
          ),
      ],
    );
  }

  // ── holdings ────────────────────────────────────────────────────────────
  Widget _holdingsSection(Map d) {
    final account = d['account'] as Map?;
    final positions = ((account?['positions'] as Map?) ?? const {}).cast<String, dynamic>();
    if (d['enabled'] != true) return const SizedBox.shrink();
    final equity = (account?['equity'] as num?)?.toDouble() ?? 0;
    final rows = positions.entries.toList()
      ..sort((a, b) => (b.value as num).compareTo(a.value as num));
    return _section('Holdings', [
      if (rows.isEmpty)
        Text('No positions yet.', style: TextStyle(color: context.pal.textDim))
      else
        for (final e in rows)
          ListTile(
            dense: true,
            contentPadding: EdgeInsets.zero,
            title: Text(e.key, style: const TextStyle(fontWeight: FontWeight.w600)),
            trailing: Text(
              '${money(e.value as num)}'
              '${equity > 0 ? '  ·  ${((e.value as num) / equity * 100).toStringAsFixed(0)}%' : ''}',
            ),
          ),
    ]);
  }

  // ── watchlist ───────────────────────────────────────────────────────────
  Widget _watchlistSection(Map d) {
    final names = (d['watchlist'] as List).cast<Map>().toList()
      ..sort((a, b) => _rank(a).compareTo(_rank(b)));
    return _section(
      'Watchlist',
      subtitle: 'Research picks names; a name can only be bought after you confirm it.',
      [
        if (names.isEmpty)
          Text('Empty until the weekly research screen runs.',
              style: TextStyle(color: context.pal.textDim)),
        for (final w in names) _watchRow(w),
      ],
    );
  }

  int _rank(Map w) => w['status'] == 'ELIGIBLE' ? (w['confirmed'] == true ? 1 : 0) : 2;

  Widget _watchRow(Map w) {
    final symbol = w['symbol'].toString();
    final eligible = w['status'] == 'ELIGIBLE';
    final confirmed = w['confirmed'] == true;
    final memo = (w['memo'] ?? '').toString();
    final (label, color) = !eligible
        ? ('Watching', context.pal.textDim)
        : confirmed
            ? ('Buyable', GajalaColors.ok)
            : ('Needs your OK', GajalaColors.warn);
    return Card(
      margin: const EdgeInsets.only(bottom: 6),
      child: ListTile(
        key: ValueKey('watch-$symbol'),
        dense: true,
        title: Text(symbol, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(label, style: TextStyle(color: color)),
        onTap: memo.isEmpty ? null : () => openTradeDoc(context, memo),
        trailing: !eligible
            ? (memo.isEmpty ? null : const Icon(Icons.description_outlined, size: 20))
            : confirmed
                ? TextButton(
                    onPressed: _busy
                        ? null
                        : () => _act(() => _call((api) => api.tradeConfirm(symbol, false))),
                    child: const Text('Remove OK'),
                  )
                : FilledButton.tonal(
                    onPressed: _busy ? null : () => _confirmName(symbol, memo),
                    child: const Text('Confirm'),
                  ),
      ),
    );
  }

  Future<void> _confirmName(String symbol, String memo) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Allow buying $symbol?'),
        content: const Text('Research marked it eligible. Confirming lets future tickets for '
            'it pass the rules; you still approve each order.'),
        actions: [
          if (memo.isNotEmpty)
            TextButton(
              onPressed: () {
                Navigator.pop(ctx, false);
                openTradeDoc(context, memo);
              },
              child: const Text('Read memo'),
            ),
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Not now')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Confirm')),
        ],
      ),
    );
    if (ok == true) await _act(() => _call((api) => api.tradeConfirm(symbol, true)));
  }

  // ── research ────────────────────────────────────────────────────────────
  Widget _reportsSection(Map d) {
    final reports = (d['reports'] as List).cast<Map>();
    if (reports.isEmpty) return const SizedBox.shrink();
    return _section('Research reports', [
      for (final r in reports.take(5))
        ListTile(
          dense: true,
          contentPadding: EdgeInsets.zero,
          leading: const Icon(Icons.article_outlined, size: 20),
          title: Text(r['title'].toString()),
          subtitle: Text(_ago(r['modified'] as num?)),
          onTap: () => openTradeDoc(context, r['path'].toString()),
        ),
    ]);
  }

  // ── activity ────────────────────────────────────────────────────────────
  Widget _activitySection(Map d) {
    final recent = (d['recent_tickets'] as List).cast<Map>();
    if (recent.isEmpty) return const SizedBox.shrink();
    return _section('Recent activity', [
      for (final t in recent.take(10))
        ListTile(
          dense: true,
          contentPadding: EdgeInsets.zero,
          leading: Icon(_outcomeIcon(t), size: 20, color: _outcomeColor(t)),
          title: Text('${t['side'] == 'sell' ? 'Sell' : 'Buy'} ${money(t['notional_usd'] as num)} '
              '${t['symbol']}'),
          subtitle: Text(
            [_outcome(t), if ((t['gate_notes'] ?? '').toString().isNotEmpty) t['gate_notes']]
                .join(': '),
            maxLines: 3,
            overflow: TextOverflow.ellipsis,
          ),
          trailing: Text(_ago(t['updated_at'] as num?),
              style: TextStyle(fontSize: 11.5, color: context.pal.textDim)),
        ),
    ]);
  }

  String _outcome(Map t) => switch (t['status']) {
        'filled' => 'Filled',
        'rejected' => (t['gate_notes'] ?? '').toString().isNotEmpty
            ? 'Stopped by the rules'
            : 'You rejected it',
        'expired' => 'Expired unanswered',
        'failed' => 'Failed',
        _ => t['status'].toString(),
      };

  IconData _outcomeIcon(Map t) => switch (t['status']) {
        'filled' => Icons.check_circle_outline,
        'rejected' => Icons.block,
        'expired' => Icons.timer_off_outlined,
        'failed' => Icons.error_outline,
        _ => Icons.circle_outlined,
      };

  Color _outcomeColor(Map t) => switch (t['status']) {
        'filled' => GajalaColors.ok,
        'failed' => GajalaColors.danger,
        'rejected' => (t['gate_notes'] ?? '').toString().isNotEmpty
            ? GajalaColors.warn
            : context.pal.textDim,
        _ => context.pal.textDim,
      };

  // ── rules ───────────────────────────────────────────────────────────────
  Widget _rulesSection(Map d) {
    final l = d['limits'] as Map;
    String pct(num v) => '${(v * 100).toStringAsFixed(0)}%';
    final rules = [
      'Stocks only, limit orders, whole shares',
      'At most ${money(l['max_order_usd'] as num)} per order, ${money(l['max_daily_usd'] as num)} a day',
      'At most ${l['max_positions']} holdings, ${pct(l['max_position_pct'] as num)} in any one',
      'Keep at least ${pct(l['cash_floor_pct'] as num)} in cash',
      'Buys stop at a 15% drop from the peak; everything stops at 25%',
      'Unanswered tickets expire after ${(l['ticket_hours'] as num).toStringAsFixed(0)} h',
      if (d['auto_approve'] == true) 'Paper tickets are approved automatically' else 'You approve every order',
    ];
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Theme(
        data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
        child: ExpansionTile(
          tilePadding: EdgeInsets.zero,
          title: Text('Safety rules', style: Theme.of(context).textTheme.titleMedium),
          subtitle: Text('Checked in code before and again at approval',
              style: TextStyle(fontSize: 12.5, color: context.pal.textDim)),
          children: [
            for (final r in rules)
              ListTile(
                dense: true,
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.shield_outlined, size: 18),
                title: Text(r),
              ),
            Text('Limits are set in the Mac\'s .env (TRADE_MAX_ORDER_USD, TRADE_MAX_DAILY_USD).',
                style: TextStyle(fontSize: 12, color: context.pal.textDim)),
          ],
        ),
      ),
    );
  }
}

// ── pieces ───────────────────────────────────────────────────────────────────

class TicketCard extends StatelessWidget {
  final Map ticket;
  final bool highlight, busy;
  final VoidCallback onApprove, onReject;
  final void Function(String path) onMemo;
  const TicketCard({
    super.key,
    required this.ticket,
    required this.onApprove,
    required this.onReject,
    required this.onMemo,
    this.highlight = false,
    this.busy = false,
  });

  @override
  Widget build(BuildContext context) {
    final t = ticket;
    final pal = context.pal;
    final waiting = t['status'] == 'proposed';
    final memo = (t['thesis_ref'] ?? '').toString();
    return Card(
      margin: EdgeInsets.zero,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(12),
        side: highlight ? const BorderSide(color: GajalaColors.accent, width: 2) : BorderSide.none,
      ),
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 10),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Flexible(
              child: Text('${t['side'] == 'sell' ? 'SELL' : 'BUY'} ${t['symbol']}',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
            ),
            const SizedBox(width: 8),
            _ModeChip(t['mode'].toString(), small: true),
            const Spacer(),
            Text(money(maxCost(t)),
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w700)),
          ]),
          const SizedBox(height: 4),
          Text(orderSummary(t), style: TextStyle(fontSize: 13, color: pal.textDim)),
          if ((t['reason'] ?? '').toString().isNotEmpty) ...[
            const SizedBox(height: 8),
            Text(t['reason'].toString()),
          ],
          const SizedBox(height: 6),
          Row(children: [
            Expanded(
              child: Text(waiting ? _expiresIn(t['expires_at'] as num?) : 'Now ${t['status']}',
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(fontSize: 12, color: pal.textDim)),
            ),
            if (memo.isNotEmpty)
              TextButton.icon(
                onPressed: () => onMemo(memo),
                icon: const Icon(Icons.description_outlined, size: 18),
                label: const Text('Memo'),
              ),
          ]),
          if (waiting)
            Row(children: [
              Expanded(
                child: OutlinedButton(
                  key: ValueKey('reject-${t['id']}'),
                  onPressed: busy ? null : onReject,
                  child: const Text('Reject'),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: FilledButton(
                  key: ValueKey('approve-${t['id']}'),
                  onPressed: busy ? null : onApprove,
                  child: const Text('Approve'),
                ),
              ),
            ]),
        ]),
      ),
    );
  }
}

class _ModeChip extends StatelessWidget {
  final String mode;
  final bool small;
  const _ModeChip(this.mode, {this.small = false});
  @override
  Widget build(BuildContext context) {
    final live = mode == 'live';
    final color = live ? GajalaColors.danger : GajalaColors.teal;
    return Container(
      padding: EdgeInsets.symmetric(horizontal: small ? 6 : 8, vertical: small ? 1 : 3),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Text(small ? (live ? 'LIVE' : 'PAPER') : (live ? 'LIVE · real money' : 'PAPER · simulated'),
          overflow: TextOverflow.ellipsis,
          style: TextStyle(color: color, fontSize: small ? 10.5 : 12, fontWeight: FontWeight.w600)),
    );
  }
}

class _MoodChip extends StatelessWidget {
  final String? mood;
  const _MoodChip(this.mood);
  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (mood) {
      'risk_on' => ('Market: upbeat', GajalaColors.ok),
      'neutral' => ('Market: neutral', GajalaColors.blue),
      'risk_off' => ('Market: cautious', GajalaColors.warn),
      _ => ('Market: unknown', context.pal.textDim),
    };
    return Text(label, style: TextStyle(fontSize: 12, color: color));
  }
}

/// A research memo or report, rendered as simple formatted text.
class TradeDocScreen extends ConsumerWidget {
  final String path;
  const TradeDocScreen({super.key, required this.path});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final api = ref.read(apiProvider);
    return Scaffold(
      appBar: AppBar(title: Text(path.split('/').last.replaceAll('.md', ''))),
      body: FutureBuilder<String>(
        future: api?.tradeDoc(path),
        builder: (context, snap) {
          if (snap.hasError) return Center(child: Text(detailOf(snap.error!)));
          if (!snap.hasData) return const Center(child: CircularProgressIndicator());
          return SelectionArea(
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [for (final line in snap.data!.split('\n')) _mdLine(context, line)],
            ),
          );
        },
      ),
    );
  }

  Widget _mdLine(BuildContext context, String line) {
    final trimmed = line.trimRight();
    final heading = RegExp(r'^(#{1,4})\s+(.*)$').firstMatch(trimmed);
    if (heading != null) {
      final level = heading.group(1)!.length;
      return Padding(
        padding: EdgeInsets.only(top: level == 1 ? 4 : 12, bottom: 4),
        child: Text(_plain(heading.group(2)!),
            style: TextStyle(fontSize: level == 1 ? 21 : (level == 2 ? 17 : 15),
                fontWeight: FontWeight.w700)),
      );
    }
    final bullet = RegExp(r'^\s*[-*]\s+(.*)$').firstMatch(trimmed);
    if (bullet != null) {
      return Padding(
        padding: const EdgeInsets.only(left: 6, bottom: 3),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('•  '),
          Expanded(child: Text(_plain(bullet.group(1)!))),
        ]),
      );
    }
    if (trimmed.isEmpty) return const SizedBox(height: 8);
    return Padding(
      padding: const EdgeInsets.only(bottom: 3),
      child: Text(_plain(trimmed), style: const TextStyle(height: 1.35)),
    );
  }

  String _plain(String s) => s.replaceAll('**', '').replaceAll('`', '');
}
