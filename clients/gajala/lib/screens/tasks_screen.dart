import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';

/// Tasks tab — the Night Shift queue cockpit. See every job's status, add work,
/// run one now, stop a running build, ship a finished one, retag or drop.
class TasksScreen extends ConsumerWidget {
  const TasksScreen({super.key});

  static const _order = [
    'running', 'awaiting_input', 'queued', 'deployed', 'staged',
    'needs_you', 'failed', 'stopped', 'held', 'shipped',
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final data = ref.watch(queueProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Tasks')),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: GajalaColors.accent,
        icon: const Icon(Icons.add, color: Colors.white),
        label: const Text('Queue task', style: TextStyle(color: Colors.white)),
        onPressed: () => _addSheet(context, ref),
      ),
      body: data.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
            child: Text(friendlyError(e),
                style: const TextStyle(color: GajalaColors.danger))),
        data: (d) {
          final jobs = [...d.jobs]..sort((a, b) =>
              _order.indexOf(a.status).compareTo(_order.indexOf(b.status)));
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(queueProvider),
            child: ListView(
              padding: const EdgeInsets.fromLTRB(12, 12, 12, 96),
              children: [
                _NightShiftHeader(d.settings),
                const SizedBox(height: 8),
                if (jobs.isEmpty)
                  Padding(
                    padding: const EdgeInsets.only(top: 60),
                    child: Center(
                      child: Text('No tasks yet.\nQueue one and it builds overnight.',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: context.pal.textDim)),
                    ),
                  )
                else
                  for (final j in jobs) _JobCard(j),
              ],
            ),
          );
        },
      ),
    );
  }
}

// ── status styling ──────────────────────────────────────────────────────────

({Color color, IconData icon, String label}) _statusStyle(String s) {
  switch (s) {
    case 'running':
      return (color: GajalaColors.blue, icon: Icons.autorenew, label: 'building');
    case 'awaiting_input':
      return (color: GajalaColors.amber, icon: Icons.help_outline, label: 'needs input');
    case 'queued':
      return (color: GajalaColors.teal, icon: Icons.schedule, label: 'queued');
    case 'deployed':
      return (color: GajalaColors.green, icon: Icons.phone_android, label: 'deployed');
    case 'staged':
      return (color: GajalaColors.violet, icon: Icons.inventory_2_outlined, label: 'staged');
    case 'needs_you':
      return (color: GajalaColors.amber, icon: Icons.pan_tool_outlined, label: 'needs you');
    case 'failed':
      return (color: GajalaColors.danger, icon: Icons.error_outline, label: 'failed');
    case 'stopped':
      return (color: GajalaColors.pink, icon: Icons.stop_circle_outlined, label: 'stopped');
    case 'held':
      return (color: GajalaColors.pink, icon: Icons.push_pin_outlined, label: 'held (mine)');
    case 'shipped':
      return (color: GajalaColors.green, icon: Icons.check_circle_outline, label: 'shipped');
    default:
      return (color: GajalaColors.teal, icon: Icons.circle, label: s);
  }
}

// ── one job ─────────────────────────────────────────────────────────────────

class _JobCard extends ConsumerWidget {
  final QueueJob j;
  const _JobCard(this.j);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final st = _statusStyle(j.status);
    final pal = context.pal;
    return Dismissible(
      key: ValueKey('job-${j.id}'),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.symmetric(horizontal: 20),
        margin: const EdgeInsets.only(bottom: 10),
        decoration: BoxDecoration(
            color: GajalaColors.danger.withValues(alpha: .18),
            borderRadius: BorderRadius.circular(14)),
        child: const Icon(Icons.delete, color: GajalaColors.danger),
      ),
      onDismissed: (_) async {
        await ref.read(apiProvider)?.dropJob(j.id);
        ref.invalidate(queueProvider);
      },
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        decoration: BoxDecoration(
          color: pal.surface,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: pal.border),
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 6, 12),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Icon(st.icon, size: 16, color: st.color),
              const SizedBox(width: 6),
              Text(st.label.toUpperCase(),
                  style: TextStyle(
                      fontSize: 10.5, letterSpacing: .6,
                      fontWeight: FontWeight.w700, color: st.color)),
              const Spacer(),
              Text('${j.projectName} · ${j.engine}',
                  style: TextStyle(fontSize: 11, color: pal.textDim)),
              _JobMenu(j),
            ]),
            const SizedBox(height: 2),
            Text(j.task,
                maxLines: 3, overflow: TextOverflow.ellipsis,
                style: TextStyle(fontSize: 14, color: pal.text, height: 1.3)),
            if (j.status == 'awaiting_input')
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text('Answer this in Alerts to continue →',
                    style: TextStyle(fontSize: 12, color: GajalaColors.amber)),
              ),
            if (j.summary != null && j.summary!.isNotEmpty &&
                j.status != 'awaiting_input')
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Text(j.summary!,
                    maxLines: 2, overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12, color: pal.textDim)),
              ),
          ]),
        ),
      ),
    );
  }
}

class _JobMenu extends ConsumerWidget {
  final QueueJob j;
  const _JobMenu(this.j);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final api = ref.read(apiProvider);
    final canRun = j.status != 'running';
    final canStop = j.status == 'running';
    final canShip = j.status == 'staged' || j.status == 'deployed';
    return PopupMenuButton<String>(
      icon: Icon(Icons.more_vert, size: 18, color: context.pal.textDim),
      color: context.pal.surface,
      onSelected: (v) async {
        try {
          switch (v) {
            case 'run':
              await api?.runJob(j.id);
              break;
            case 'stop':
              await api?.stopJob(j.id);
              break;
            case 'ship':
              final msg = await api?.shipJob(j.id) ?? '';
              if (context.mounted) {
                ScaffoldMessenger.of(context)
                    .showSnackBar(SnackBar(content: Text(msg)));
              }
              break;
            case 'auto':
            case 'mine':
              await api?.tagJob(j.id, v);
              break;
            case 'drop':
              await api?.dropJob(j.id);
              break;
          }
          ref.invalidate(queueProvider);
          ref.invalidate(unreadCountProvider);
        } catch (e) {
          if (context.mounted) {
            ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                content: Text(friendlyError(e)),
                backgroundColor: GajalaColors.danger));
          }
        }
      },
      itemBuilder: (_) => [
        if (canRun)
          const PopupMenuItem(value: 'run', child: ListTile(
              dense: true, contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.play_arrow), title: Text('Run now'))),
        if (canStop)
          const PopupMenuItem(value: 'stop', child: ListTile(
              dense: true, contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.stop), title: Text('Stop'))),
        if (canShip)
          const PopupMenuItem(value: 'ship', child: ListTile(
              dense: true, contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.merge_type), title: Text('Ship (merge)'))),
        PopupMenuItem(value: j.tag == 'auto' ? 'mine' : 'auto', child: ListTile(
            dense: true, contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.label_outline),
            title: Text(j.tag == 'auto' ? 'Hold (mine)' : 'Make auto'))),
        const PopupMenuItem(value: 'drop', child: ListTile(
            dense: true, contentPadding: EdgeInsets.zero,
            leading: Icon(Icons.delete_outline), title: Text('Drop'))),
      ],
    );
  }
}

// ── Night Shift header (state + settings) ──────────────────────────────────────

class _NightShiftHeader extends ConsumerWidget {
  final Map<String, dynamic> settings;
  const _NightShiftHeader(this.settings);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pal = context.pal;
    final on = settings['enabled'] == true;
    final window = '${settings['start'] ?? '23:00'}–${settings['end'] ?? '07:00'}';
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 10, 8, 10),
      decoration: BoxDecoration(
        color: (on ? GajalaColors.green : pal.textDim).withValues(alpha: .10),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
            color: (on ? GajalaColors.green : pal.border).withValues(alpha: .4)),
      ),
      child: Row(children: [
        Icon(Icons.nightlight_round,
            color: on ? GajalaColors.green : pal.textDim, size: 20),
        const SizedBox(width: 10),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(on ? 'Night Shift on' : 'Night Shift off',
                style: TextStyle(fontWeight: FontWeight.w700, color: pal.text)),
            Text(on ? 'Builds queued tasks $window' : 'Tap the switch to run overnight',
                style: TextStyle(fontSize: 12, color: pal.textDim)),
          ]),
        ),
        IconButton(
          icon: Icon(Icons.tune, size: 20, color: pal.textDim),
          tooltip: 'Settings',
          onPressed: () => _settingsSheet(context, ref, settings),
        ),
        Switch(
          value: on,
          activeThumbColor: GajalaColors.green,
          onChanged: (v) async {
            await ref.read(apiProvider)?.setQueueSettings({'enabled': v});
            ref.invalidate(queueProvider);
          },
        ),
      ]),
    );
  }
}

Future<void> _settingsSheet(
    BuildContext context, WidgetRef ref, Map<String, dynamic> s) async {
  final start = TextEditingController(text: '${s['start'] ?? '23:00'}');
  final end = TextEditingController(text: '${s['end'] ?? '07:00'}');
  final maxJobs = TextEditingController(text: '${s['max_jobs'] ?? 12}');
  await showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: context.pal.surface,
    shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
    builder: (ctx) => Padding(
      padding: EdgeInsets.fromLTRB(
          20, 18, 20, MediaQuery.of(ctx).viewInsets.bottom + 20),
      child: Column(mainAxisSize: MainAxisSize.min, children: [
        Text('Night Shift settings', style: Theme.of(ctx).textTheme.titleMedium),
        const SizedBox(height: 14),
        Row(children: [
          Expanded(child: TextField(controller: start,
              decoration: const InputDecoration(labelText: 'Start (HH:MM)'))),
          const SizedBox(width: 12),
          Expanded(child: TextField(controller: end,
              decoration: const InputDecoration(labelText: 'End (HH:MM)'))),
        ]),
        const SizedBox(height: 12),
        TextField(controller: maxJobs, keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Max jobs / night')),
        const SizedBox(height: 18),
        SizedBox(width: double.infinity, child: FilledButton(
          onPressed: () async {
            await ref.read(apiProvider)?.setQueueSettings({
              'start': start.text.trim(),
              'end': end.text.trim(),
              'max_jobs': int.tryParse(maxJobs.text.trim()) ?? s['max_jobs'],
            });
            ref.invalidate(queueProvider);
            if (ctx.mounted) Navigator.pop(ctx);
          },
          child: const Text('Save'),
        )),
      ]),
    ),
  );
}

// ── add-job sheet ─────────────────────────────────────────────────────────────

Future<void> _addSheet(BuildContext context, WidgetRef ref) async {
  final task = TextEditingController();
  final project = TextEditingController();
  final current = ref.read(projectsProvider).valueOrNull?['current_name']?.toString();
  String tag = 'auto';
  String engine = 'auto';
  await showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: context.pal.surface,
    shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
    builder: (ctx) => StatefulBuilder(builder: (ctx, setSheet) {
      Widget chip(String label, String value, String group, void Function() pick) {
        final sel = group == value;
        return Padding(
          padding: const EdgeInsets.only(right: 8),
          child: ChoiceChip(
            label: Text(label),
            selected: sel,
            onSelected: (_) => setSheet(pick),
          ),
        );
      }

      return Padding(
        padding: EdgeInsets.fromLTRB(
            20, 18, 20, MediaQuery.of(ctx).viewInsets.bottom + 20),
        child: Column(mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Queue a task', style: Theme.of(ctx).textTheme.titleMedium),
          const SizedBox(height: 12),
          TextField(controller: task, autofocus: true, minLines: 2, maxLines: 4,
              decoration: const InputDecoration(
                  hintText: 'What should the agent build?')),
          const SizedBox(height: 10),
          TextField(controller: project,
              decoration: InputDecoration(
                  labelText: 'Project',
                  hintText: current == null ? 'active project' : 'blank = $current')),
          const SizedBox(height: 14),
          Text('WHO RUNS IT', style: Theme.of(ctx).textTheme.labelSmall),
          const SizedBox(height: 6),
          Row(children: [
            chip('Auto (overnight)', 'auto', tag, () => tag = 'auto'),
            chip('Mine (hold)', 'mine', tag, () => tag = 'mine'),
          ]),
          const SizedBox(height: 12),
          Text('ENGINE', style: Theme.of(ctx).textTheme.labelSmall),
          const SizedBox(height: 6),
          Wrap(children: [
            for (final e in ['auto', 'claude', 'codex', 'gemini'])
              chip(e, e, engine, () => engine = e),
          ]),
          const SizedBox(height: 18),
          SizedBox(width: double.infinity, child: FilledButton(
            onPressed: () async {
              if (task.text.trim().isEmpty) return;
              try {
                await ref.read(apiProvider)?.addJob(task.text.trim(),
                    project: project.text.trim().isEmpty ? null : project.text.trim(),
                    tag: tag, engine: engine);
                ref.invalidate(queueProvider);
                if (ctx.mounted) Navigator.pop(ctx);
              } catch (e) {
                if (ctx.mounted) {
                  ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(
                      content: Text(friendlyError(e)),
                      backgroundColor: GajalaColors.danger));
                }
              }
            },
            child: const Text('Add to queue'),
          )),
        ]),
      );
    }),
  );
}
