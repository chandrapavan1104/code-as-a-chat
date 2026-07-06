import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/state.dart';
import '../core/theme.dart';

String _humanTokens(dynamic n) {
  if (n == null) return '—';
  final v = (n as num).toDouble();
  if (v >= 1e9) return '${(v / 1e9).toStringAsFixed(1)}B';
  if (v >= 1e6) return '${(v / 1e6).toStringAsFixed(1)}M';
  if (v >= 1e3) return '${(v / 1e3).toStringAsFixed(1)}K';
  return v.toStringAsFixed(0);
}

class UsageScreen extends ConsumerStatefulWidget {
  const UsageScreen({super.key});

  @override
  ConsumerState<UsageScreen> createState() => _UsageScreenState();
}

class _UsageScreenState extends ConsumerState<UsageScreen>
    with WidgetsBindingObserver {
  DateTime? _fetchedAt; // when the currently-shown data landed

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // Always pull fresh on open — don't trust a value cached from a past visit.
    WidgetsBinding.instance.addPostFrameCallback((_) => _refresh());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Coming back from background with this screen up → refetch so usage is live.
    if (state == AppLifecycleState.resumed && mounted) _refresh();
  }

  Future<void> _refresh() => ref.refresh(usageProvider.future);

  String _stamp(DateTime t) {
    final d = DateTime.now().difference(t);
    if (d.inSeconds < 5) return 'updated just now';
    if (d.inSeconds < 60) return 'updated ${d.inSeconds}s ago';
    if (d.inMinutes < 60) return 'updated ${d.inMinutes}m ago';
    final h = t.hour.toString().padLeft(2, '0');
    final m = t.minute.toString().padLeft(2, '0');
    return 'updated $h:$m';
  }

  @override
  Widget build(BuildContext context) {
    final usage = ref.watch(usageProvider);
    // Record the moment fresh data arrives so we can show an "updated" stamp.
    ref.listen(usageProvider, (_, next) {
      if (next.hasValue && !next.isLoading) _fetchedAt = DateTime.now();
    });
    return Scaffold(
      appBar: AppBar(
        title: const Text('Codaur'),
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _refresh),
        ],
      ),
      body: usage.when(
        data: (providers) => RefreshIndicator(
          onRefresh: _refresh,
          child: ListView(
            padding: const EdgeInsets.all(12),
            children: [
              if (_fetchedAt != null)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8, left: 4),
                  child: Text(_stamp(_fetchedAt!),
                      style: TextStyle(color: context.pal.textDim, fontSize: 11)),
                ),
              for (final p in providers) _ProviderCard(p),
            ],
          ),
        ),
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text(friendlyError(e),
            style: const TextStyle(color: GajalaColors.danger))),
      ),
    );
  }
}

class _ProviderCard extends StatelessWidget {
  final Map<String, dynamic> p;
  const _ProviderCard(this.p);
  @override
  Widget build(BuildContext context) {
    final limits = (p['limits'] as List?) ?? const [];
    // Some engines (Antigravity) don't expose tokens — show activity instead.
    final hasTokens = p['today_tokens'] != null || p['total_tokens'] != null;
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text((p['provider'] ?? '?').toString().toUpperCase(),
                style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 15)),
            if (p['plan'] != null) ...[
              const SizedBox(width: 8),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                decoration: BoxDecoration(color: GajalaColors.accentDim.withValues(alpha: .3),
                    borderRadius: BorderRadius.circular(6)),
                child: Text(p['plan'].toString(),
                    style: const TextStyle(fontSize: 11, color: GajalaColors.accent)),
              ),
            ],
          ]),
          const SizedBox(height: 10),
          for (int i = 0; i < limits.length; i++) ...[
            if (i > 0) const SizedBox(height: 8),
            _limitBar(
              (limits[i]['label'] ?? 'usage').toString(),
              (limits[i]['pct'] as num).toDouble(),
              detail: limits[i]['detail']?.toString(),
            ),
          ],
          if (limits.isEmpty)
            Text(hasTokens
                    ? 'No live rate-limit data'
                    : 'Activity only — token usage not exposed by this tool',
                style: TextStyle(color: context.pal.textDim, fontSize: 12)),
          const SizedBox(height: 12),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: hasTokens
                ? [
                    _stat(context, 'today', _humanTokens(p['today_tokens'])),
                    _stat(context, '30-day', _humanTokens(p['total_tokens'])),
                    _stat(context, 'threads', (p['threads'] ?? 0).toString()),
                  ]
                : [
                    _stat(context, 'conversations', (p['threads'] ?? 0).toString()),
                    _stat(context, 'steps', (p['events'] ?? 0).toString()),
                  ],
          ),
        ]),
      ),
    );
  }

  Widget _limitBar(String label, double pct, {String? detail}) => Builder(builder: (context) {
    final frac = (pct / 100).clamp(0.0, 1.0);
    final color = frac > .85 ? GajalaColors.danger : frac > .6 ? GajalaColors.warn : GajalaColors.ok;
    final pctStr = pct >= 10 ? pct.toStringAsFixed(0) : pct.toStringAsFixed(1);
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text(label, style: TextStyle(color: context.pal.textDim, fontSize: 12)),
        Text(detail != null ? '$detail · $pctStr%' : '$pctStr% used',
            style: TextStyle(color: color, fontSize: 12)),
      ]),
      const SizedBox(height: 4),
      ClipRRect(borderRadius: BorderRadius.circular(4),
        child: LinearProgressIndicator(value: frac, minHeight: 7,
            backgroundColor: context.pal.surfaceAlt, color: color)),
    ]);
  });

  Widget _stat(BuildContext context, String label, String value) => Column(children: [
    Text(value, style: const TextStyle(fontWeight: FontWeight.w700, fontSize: 16)),
    Text(label, style: TextStyle(color: context.pal.textDim, fontSize: 11)),
  ]);
}
