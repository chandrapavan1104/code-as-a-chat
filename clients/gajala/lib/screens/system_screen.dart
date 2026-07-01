import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/state.dart';
import '../core/theme.dart';

class SystemScreen extends ConsumerStatefulWidget {
  const SystemScreen({super.key});
  @override
  ConsumerState<SystemScreen> createState() => _SystemScreenState();
}

class _SystemScreenState extends ConsumerState<SystemScreen> {
  Timer? _timer;
  @override
  void initState() {
    super.initState();
    _timer = Timer.periodic(const Duration(seconds: 5),
        (_) => ref.invalidate(systemProvider));
  }
  @override
  void dispose() { _timer?.cancel(); super.dispose(); }

  @override
  Widget build(BuildContext context) {
    final sys = ref.watch(systemProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('System'), actions: [
        IconButton(icon: const Icon(Icons.refresh),
            onPressed: () => ref.invalidate(systemProvider)),
      ]),
      body: sys.when(
        data: (s) => ListView(padding: const EdgeInsets.all(16), children: [
          Wrap(spacing: 18, runSpacing: 18, alignment: WrapAlignment.center, children: [
            _Gauge('CPU', s.cpu / 100, '${s.cpu.toStringAsFixed(0)}%'),
            _Gauge('RAM', s.ramPct / 100, '${s.ramPct}%', '${s.ramUsed}/${s.ramTotal} GB'),
            _Gauge('Disk', s.diskPct / 100, '${s.diskPct}%', '${s.diskUsed}/${s.diskTotal} GB'),
            if (s.batteryPct != null)
              _Gauge('Battery', s.batteryPct! / 100, '${s.batteryPct}%',
                  s.charging == true ? 'charging' : 'on battery'),
          ]),
          const SizedBox(height: 24),
          Text('TOP PROCESSES', style: TextStyle(
              color: context.pal.textDim, fontWeight: FontWeight.w700, letterSpacing: 1)),
          const SizedBox(height: 8),
          ...s.topProcs.map((p) => Card(
            margin: const EdgeInsets.only(bottom: 8),
            child: ListTile(
              dense: true,
              title: Text(p['name']?.toString() ?? '?'),
              subtitle: Text('pid ${p['pid']}', style: const TextStyle(fontSize: 11)),
              trailing: Text('CPU ${p['cpu']}%  ·  RAM ${p['mem']}%',
                  style: TextStyle(color: context.pal.textDim, fontSize: 12)),
            ),
          )),
        ]),
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text(friendlyError(e),
            style: const TextStyle(color: GajalaColors.danger))),
      ),
    );
  }
}

class _Gauge extends StatelessWidget {
  final String label, value;
  final String? sub;
  final double frac;
  const _Gauge(this.label, this.frac, this.value, [this.sub]);
  @override
  Widget build(BuildContext context) {
    final color = frac > .85 ? GajalaColors.danger : frac > .6 ? GajalaColors.warn : GajalaColors.ok;
    return SizedBox(
      width: 150,
      child: Column(children: [
        SizedBox(
          height: 110, width: 110,
          child: Stack(alignment: Alignment.center, children: [
            SizedBox(height: 110, width: 110, child: CircularProgressIndicator(
                value: frac.clamp(0, 1), strokeWidth: 9,
                backgroundColor: context.pal.surfaceAlt, color: color)),
            Column(mainAxisSize: MainAxisSize.min, children: [
              Text(value, style: const TextStyle(fontSize: 22, fontWeight: FontWeight.w800)),
            ]),
          ]),
        ),
        const SizedBox(height: 8),
        Text(label, style: const TextStyle(fontWeight: FontWeight.w600)),
        if (sub != null) Text(sub!, style: TextStyle(color: context.pal.textDim, fontSize: 12)),
      ]),
    );
  }
}
