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

class UsageScreen extends ConsumerWidget {
  const UsageScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final usage = ref.watch(usageProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Usage'), actions: [
        IconButton(icon: const Icon(Icons.refresh),
            onPressed: () => ref.invalidate(usageProvider)),
      ]),
      body: usage.when(
        data: (providers) => RefreshIndicator(
          onRefresh: () async => ref.invalidate(usageProvider),
          child: ListView(
            padding: const EdgeInsets.all(12),
            children: [
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
    final primary = p['primary_pct'];
    final secondary = p['secondary_pct'];
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
          if (primary != null) _limitBar('5-hour window', (primary as num).toDouble()),
          if (secondary != null) ...[
            const SizedBox(height: 8),
            _limitBar('weekly', (secondary as num).toDouble()),
          ],
          if (primary == null && secondary == null)
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

  Widget _limitBar(String label, double pct) => Builder(builder: (context) {
    final frac = (pct / 100).clamp(0.0, 1.0);
    final color = frac > .85 ? GajalaColors.danger : frac > .6 ? GajalaColors.warn : GajalaColors.ok;
    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
        Text(label, style: TextStyle(color: context.pal.textDim, fontSize: 12)),
        Text('${pct.toStringAsFixed(0)}% used', style: TextStyle(color: color, fontSize: 12)),
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
