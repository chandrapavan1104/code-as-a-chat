import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';

/// Tasks tab — durable Night Shift work orders. Items are never deleted: they
/// move between Active and Closed and keep their closure history.
class TasksScreen extends ConsumerWidget {
  const TasksScreen({super.key});

  static const _order = [
    'running',
    'deploying',
    'awaiting_input',
    'queued',
    'deployed',
    'staged',
    'completed',
    'needs_you',
    'failed',
    'stopped',
    'held',
    'shipped',
    'closed',
  ];

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final data = ref.watch(queueProvider);
    return DefaultTabController(
      length: 2,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Tasks'),
          bottom: const TabBar(
            tabs: [
              Tab(text: 'Active'),
              Tab(text: 'Closed'),
            ],
          ),
        ),
        floatingActionButton: FloatingActionButton.extended(
          backgroundColor: GajalaColors.accent,
          icon: const Icon(Icons.add, color: Colors.white),
          label: const Text(
            'New work order',
            style: TextStyle(color: Colors.white),
          ),
          onPressed: () => _addSheet(context, ref),
        ),
        body: data.when(
          loading: () => const Center(child: CircularProgressIndicator()),
          error: (e, _) => Center(
            child: Text(
              friendlyError(e),
              style: const TextStyle(color: GajalaColors.danger),
            ),
          ),
          data: (d) {
            final active = d.jobs.where((j) => j.status != 'closed').toList()
              ..sort(
                (a, b) => _order
                    .indexOf(a.status)
                    .compareTo(_order.indexOf(b.status)),
              );
            final closed = d.jobs.where((j) => j.status == 'closed').toList();
            return TabBarView(
              children: [
                _JobList(
                  active,
                  header: Column(
                    children: [
                      _QueueHealthCard(d.health),
                      const SizedBox(height: 8),
                      _NightShiftHeader(d.settings),
                    ],
                  ),
                ),
                _JobList(closed, closed: true),
              ],
            );
          },
        ),
      ),
    );
  }
}

class _JobList extends ConsumerWidget {
  final List<QueueJob> jobs;
  final Widget? header;
  final bool closed;
  const _JobList(this.jobs, {this.header, this.closed = false});

  @override
  Widget build(BuildContext context, WidgetRef ref) => RefreshIndicator(
    onRefresh: () async => ref.invalidate(queueProvider),
    child: ListView(
      physics: const AlwaysScrollableScrollPhysics(),
      padding: const EdgeInsets.fromLTRB(12, 12, 12, 96),
      children: [
        if (header != null) ...[header!, const SizedBox(height: 8)],
        if (jobs.isEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 60),
            child: Center(
              child: Text(
                closed
                    ? 'Nothing closed.\nClosed work can always be reopened.'
                    : 'No active work.\nCreate a work order for Night Shift.',
                textAlign: TextAlign.center,
                style: TextStyle(color: context.pal.textDim),
              ),
            ),
          )
        else
          for (final j in jobs) _JobCard(j),
      ],
    ),
  );
}

// ── status styling ──────────────────────────────────────────────────────────

({Color color, IconData icon, String label}) _statusStyle(String s) {
  switch (s) {
    case 'deploying':
      return (
        color: GajalaColors.blue,
        icon: Icons.rocket_launch_outlined,
        label: 'deploying safely',
      );
    case 'running':
      return (
        color: GajalaColors.blue,
        icon: Icons.autorenew,
        label: 'building',
      );
    case 'awaiting_input':
      return (
        color: GajalaColors.amber,
        icon: Icons.help_outline,
        label: 'needs input',
      );
    case 'queued':
      return (color: GajalaColors.teal, icon: Icons.schedule, label: 'queued');
    case 'deployed':
      return (
        color: GajalaColors.green,
        icon: Icons.phone_android,
        label: 'deployed',
      );
    case 'staged':
      return (
        color: GajalaColors.violet,
        icon: Icons.inventory_2_outlined,
        label: 'staged',
      );
    case 'completed':
      return (
        color: GajalaColors.green,
        icon: Icons.task_alt,
        label: 'completed',
      );
    case 'needs_you':
      return (
        color: GajalaColors.amber,
        icon: Icons.pan_tool_outlined,
        label: 'needs you',
      );
    case 'failed':
      return (
        color: GajalaColors.danger,
        icon: Icons.error_outline,
        label: 'failed',
      );
    case 'stopped':
      return (
        color: GajalaColors.pink,
        icon: Icons.stop_circle_outlined,
        label: 'stopped',
      );
    case 'held':
      return (
        color: GajalaColors.pink,
        icon: Icons.push_pin_outlined,
        label: 'held (mine)',
      );
    case 'shipped':
      return (
        color: GajalaColors.green,
        icon: Icons.check_circle_outline,
        label: 'shipped',
      );
    case 'closed':
      return (
        color: GajalaColors.pink,
        icon: Icons.archive_outlined,
        label: 'closed',
      );
    default:
      return (color: GajalaColors.teal, icon: Icons.circle, label: s);
  }
}

({Color color, IconData icon, String label})? _awarenessStyle(String value) {
  switch (value) {
    case 'duplicate':
      return (
        color: GajalaColors.pink,
        icon: Icons.copy_outlined,
        label: 'duplicate',
      );
    case 'already_implemented':
      return (
        color: GajalaColors.green,
        icon: Icons.verified_outlined,
        label: 'already live',
      );
    case 'extension':
      return (
        color: GajalaColors.violet,
        icon: Icons.call_split,
        label: 'extension',
      );
    case 'conflict':
      return (
        color: GajalaColors.amber,
        icon: Icons.merge_type,
        label: 'overlap',
      );
    case 'unclear':
      return (
        color: GajalaColors.amber,
        icon: Icons.manage_search,
        label: 'check scope',
      );
    default:
      return null;
  }
}

// ── one job ─────────────────────────────────────────────────────────────────

class _JobCard extends ConsumerWidget {
  final QueueJob j;
  const _JobCard(this.j);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final st = _statusStyle(j.status);
    final awareness = _awarenessStyle(
      j.awareness['classification']?.toString() ?? '',
    );
    final pal = context.pal;
    return InkWell(
      borderRadius: BorderRadius.circular(14),
      onTap: () => _jobDetailSheet(context, ref, j),
      child: Container(
        margin: const EdgeInsets.only(bottom: 10),
        decoration: BoxDecoration(
          color: pal.surface,
          borderRadius: BorderRadius.circular(14),
          border: Border.all(color: pal.border),
        ),
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 12, 6, 12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 7,
                      vertical: 3,
                    ),
                    decoration: BoxDecoration(
                      color: GajalaColors.accent.withValues(alpha: .12),
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Text(
                      '#${j.id}',
                      style: const TextStyle(
                        fontSize: 10.5,
                        fontWeight: FontWeight.w800,
                        color: GajalaColors.accent,
                      ),
                    ),
                  ),
                  const SizedBox(width: 7),
                  Icon(st.icon, size: 16, color: st.color),
                  const SizedBox(width: 6),
                  Text(
                    st.label.toUpperCase(),
                    style: TextStyle(
                      fontSize: 10.5,
                      letterSpacing: .6,
                      fontWeight: FontWeight.w700,
                      color: st.color,
                    ),
                  ),
                  const SizedBox(width: 7),
                  Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 7,
                      vertical: 3,
                    ),
                    decoration: BoxDecoration(
                      color:
                          (j.isDraft ? GajalaColors.amber : GajalaColors.violet)
                              .withValues(alpha: .12),
                      borderRadius: BorderRadius.circular(20),
                    ),
                    child: Text(
                      j.isDraft ? 'DRAFT' : 'REFINED',
                      style: TextStyle(
                        fontSize: 9.5,
                        fontWeight: FontWeight.w800,
                        color: j.isDraft
                            ? GajalaColors.amber
                            : GajalaColors.violet,
                      ),
                    ),
                  ),
                  const Spacer(),
                  Text(
                    '${j.projectName} · ${j.engine}',
                    style: TextStyle(fontSize: 11, color: pal.textDim),
                  ),
                  _JobMenu(j),
                ],
              ),
              const SizedBox(height: 2),
              Text(
                j.title,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 15,
                  fontWeight: FontWeight.w600,
                  color: pal.text,
                  height: 1.3,
                ),
              ),
              if ((j.spec['outcome']?.toString() ?? '').isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text(
                    j.spec['outcome'].toString(),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12, color: pal.textDim),
                  ),
                ),
              if (awareness != null)
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Row(
                    children: [
                      Icon(awareness.icon, size: 14, color: awareness.color),
                      const SizedBox(width: 4),
                      Text(
                        awareness.label.toUpperCase(),
                        style: TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.w800,
                          color: awareness.color,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          j.awareness['action']?.toString() ?? '',
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: TextStyle(fontSize: 11, color: pal.textDim),
                        ),
                      ),
                    ],
                  ),
                ),
              if (j.isDraft)
                const Padding(
                  padding: EdgeInsets.only(top: 7),
                  child: Text(
                    'Rough capture · refine before running',
                    style: TextStyle(fontSize: 12, color: GajalaColors.amber),
                  ),
                ),
              if (j.blockedBy.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 7),
                  child: Row(
                    children: [
                      const Icon(
                        Icons.account_tree_outlined,
                        size: 14,
                        color: GajalaColors.amber,
                      ),
                      const SizedBox(width: 5),
                      Text(
                        'Waiting for #${j.blockedBy.join(', #')}',
                        style: const TextStyle(
                          fontSize: 12,
                          color: GajalaColors.amber,
                        ),
                      ),
                    ],
                  ),
                ),
              if (j.status == 'awaiting_input')
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    'Answer this in Alerts to continue →',
                    style: TextStyle(fontSize: 12, color: GajalaColors.amber),
                  ),
                ),
              if ((j.supervision['blocker']?.toString() ?? '').isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 7),
                  child: Text(
                    'Why paused: ${j.supervision['blocker']}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      fontSize: 12,
                      color: GajalaColors.amber,
                    ),
                  ),
                ),
              if ((j.supervision['next_action']?.toString() ?? '').isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 5),
                  child: Text(
                    'Next: ${j.supervision['next_action']}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12, color: pal.textDim),
                  ),
                ),
              if (j.summary != null &&
                  j.summary!.isNotEmpty &&
                  j.status != 'awaiting_input')
                Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(
                    j.status == 'failed'
                        ? 'Why it failed: ${j.summary!}'
                        : j.summary!,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12, color: pal.textDim),
                  ),
                ),
            ],
          ),
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
    final canRun =
        !j.isDraft &&
        j.status != 'running' &&
        j.status != 'closed' &&
        j.blockedBy.isEmpty;
    final canStop = j.status == 'running';
    final canShip = j.status == 'staged' || j.status == 'deployed';
    return PopupMenuButton<String>(
      icon: Icon(Icons.more_vert, size: 18, color: context.pal.textDim),
      color: context.pal.surface,
      onSelected: (v) async {
        try {
          switch (v) {
            case 'edit':
              await _editJobSheet(context, ref, j);
              return;
            case 'engine':
              final engine = await _enginePicker(context, j.engine);
              if (engine == null) return;
              await api?.setJobEngine(j.id, engine);
              break;
            case 'refine':
              final instructions = await _refineInstructions(context);
              if (instructions == null) return;
              if (!context.mounted) return;
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(content: Text('Refining the rough task…')),
              );
              await api?.refineJob(j.id, instructions: instructions);
              if (context.mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    content: Text('Refined and held for your review.'),
                  ),
                );
              }
              break;
            case 'run':
              if (!context.mounted) return;
              final confirmed = await _confirmRunIfServerJob(context, j);
              if (!confirmed) return;
              await api?.runJob(j.id);
              break;
            case 'stop':
              await api?.stopJob(j.id);
              break;
            case 'ship':
              final msg = await api?.shipJob(j.id) ?? '';
              if (context.mounted) {
                ScaffoldMessenger.of(
                  context,
                ).showSnackBar(SnackBar(content: Text(msg)));
              }
              break;
            case 'auto':
            case 'mine':
              await api?.tagJob(j.id, v);
              break;
            case 'close':
              final reason = await _closeReason(context);
              if (reason == null) return;
              await api?.closeJob(j.id, reason);
              break;
            case 'reopen':
              await api?.reopenJob(j.id);
              break;
          }
          ref.invalidate(queueProvider);
          ref.invalidate(unreadCountProvider);
        } catch (e) {
          if (context.mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(
                content: Text(friendlyError(e)),
                backgroundColor: GajalaColors.danger,
              ),
            );
          }
        }
      },
      itemBuilder: (_) => [
        if (j.status != 'running' && j.status != 'closed')
          PopupMenuItem(
            value: 'engine',
            child: ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.psychology_outlined),
              title: const Text('Change engine'),
              subtitle: Text('Current: ${j.engine}'),
            ),
          ),
        if (j.status != 'running' && j.status != 'closed')
          const PopupMenuItem(
            value: 'edit',
            child: ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.edit_outlined),
              title: Text('Edit work order'),
            ),
          ),
        if (j.status != 'running' && j.status != 'closed')
          PopupMenuItem(
            value: 'refine',
            child: ListTile(
              dense: true,
              contentPadding: const EdgeInsets.only(left: 0),
              leading: const Icon(Icons.auto_fix_high_outlined),
              title: Text(
                j.isDraft ? 'Refine with Claude' : 'Re-refine with Claude',
              ),
            ),
          ),
        if (canRun)
          const PopupMenuItem(
            value: 'run',
            child: ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.play_arrow),
              title: Text('Run now'),
            ),
          ),
        if (canStop)
          const PopupMenuItem(
            value: 'stop',
            child: ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.stop),
              title: Text('Stop'),
            ),
          ),
        if (canShip)
          const PopupMenuItem(
            value: 'ship',
            child: ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.merge_type),
              title: Text('Ship (merge)'),
            ),
          ),
        if (j.status != 'closed' && !j.isDraft)
          PopupMenuItem(
            value: j.tag == 'auto' ? 'mine' : 'auto',
            child: ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.label_outline),
              title: Text(j.tag == 'auto' ? 'Hold (mine)' : 'Make auto'),
            ),
          ),
        if (j.status != 'closed' && j.status != 'running')
          const PopupMenuItem(
            value: 'close',
            child: ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.archive_outlined),
              title: Text('Close…'),
            ),
          ),
        if (j.status == 'closed')
          const PopupMenuItem(
            value: 'reopen',
            child: ListTile(
              dense: true,
              contentPadding: EdgeInsets.zero,
              leading: Icon(Icons.unarchive_outlined),
              title: Text('Reopen'),
            ),
          ),
      ],
    );
  }
}

/// Check if a job targets the Code-as-a-Chat server repository itself.
/// The server stores the full resolved path to its REPO_DIR in the project field,
/// which typically includes `.codeasachat` for the self-hosting setup.
bool _isSelfTargetingJob(QueueJob j) {
  final project = j.project.toLowerCase();
  return project.contains('.codeasachat') || project.contains('code-as-a-chat');
}

/// Show a confirmation dialog for 'Run now' if the job targets the server repo.
/// Returns true if the user confirmed, false if they cancelled or this is not a
/// server-targeting job.
Future<bool> _confirmRunIfServerJob(BuildContext context, QueueJob j) async {
  if (!_isSelfTargetingJob(j)) {
    return true; // No confirmation needed for non-server jobs
  }

  final confirmed = await showDialog<bool>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('Run server-targeting job?'),
      content: const Text(
        'This job targets the Code-as-a-Chat server repo itself. '
        'Server changes could restart the service on ship.\n\n'
        'Tap Confirm to proceed with running this job.',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(ctx, false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(ctx, true),
          child: const Text('Confirm'),
        ),
      ],
    ),
  );

  return confirmed ?? false;
}

Future<String?> _closeReason(BuildContext context) async {
  final reason = TextEditingController();
  return showDialog<String>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('Close work order?'),
      content: TextField(
        controller: reason,
        autofocus: true,
        minLines: 2,
        maxLines: 4,
        decoration: const InputDecoration(
          labelText: 'Reason (required)',
          hintText: 'Why is this being closed?',
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(ctx),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () {
            final value = reason.text.trim();
            if (value.isNotEmpty) Navigator.pop(ctx, value);
          },
          child: const Text('Close'),
        ),
      ],
    ),
  );
}

Future<String?> _refineInstructions(BuildContext context) async {
  final instructions = TextEditingController();
  return showDialog<String>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('Refine with Claude?'),
      content: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'This sends only the rough task text and the instructions below '
            'to Claude Sonnet. No repository files or local paths are included.',
          ),
          const SizedBox(height: 14),
          TextField(
            controller: instructions,
            minLines: 2,
            maxLines: 5,
            decoration: const InputDecoration(
              labelText: 'Additional instructions (optional)',
              hintText: 'e.g. Research only; do not contact anyone',
            ),
          ),
        ],
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(ctx),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(ctx, instructions.text.trim()),
          child: const Text('Send and refine'),
        ),
      ],
    ),
  );
}

Future<String?> _enginePicker(BuildContext context, String current) =>
    showDialog<String>(
      context: context,
      builder: (ctx) => SimpleDialog(
        title: const Text('Choose engine'),
        children: [
          for (final option in const [
            ('auto', 'Auto', 'Gajala chooses using quota and availability'),
            ('claude', 'Claude', 'Pin this job to Claude'),
            ('codex', 'Codex', 'Pin this job to Codex'),
            ('gemini', 'Gemini', 'Pin this job to Gemini'),
          ])
            SimpleDialogOption(
              onPressed: () => Navigator.pop(ctx, option.$1),
              child: Row(
                children: [
                  Icon(
                    current == option.$1
                        ? Icons.radio_button_checked
                        : Icons.radio_button_off,
                    color: current == option.$1 ? GajalaColors.accent : null,
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          option.$2,
                          style: const TextStyle(fontWeight: FontWeight.w600),
                        ),
                        Text(
                          option.$3,
                          style: TextStyle(
                            fontSize: 12,
                            color: ctx.pal.textDim,
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
        ],
      ),
    );

Future<void> _editJobSheet(
  BuildContext context,
  WidgetRef ref,
  QueueJob j,
) async {
  final spec = j.spec;
  TextEditingController field(String key) =>
      TextEditingController(text: spec[key]?.toString() ?? '');
  TextEditingController listField(String key) =>
      TextEditingController(text: List.from(spec[key] ?? const []).join('\n'));
  final source = field('source_text');
  final title = field('title');
  final outcome = field('outcome');
  final workContext = field('context');
  final plan = listField('plan');
  final policy = field('policy');
  final acceptance = listField('acceptance');
  final testing = field('test_handoff');
  final scope = field('out_of_scope');
  final assumptions = listField('assumptions');
  final dependencies = TextEditingController(text: j.dependsOn.join(', '));
  final project = TextEditingController(text: j.project);
  var engine = j.engine;
  if (!['auto', 'claude', 'codex', 'gemini'].contains(engine)) engine = 'auto';
  var workType = j.workType;

  List<String> lines(TextEditingController controller) => controller.text
      .split('\n')
      .map((v) => v.trim())
      .where((v) => v.isNotEmpty)
      .toList();

  await showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: context.pal.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setSheet) => DraggableScrollableSheet(
        expand: false,
        initialChildSize: .94,
        minChildSize: .65,
        maxChildSize: .98,
        builder: (_, scroll) => ListView(
          controller: scroll,
          padding: EdgeInsets.fromLTRB(
            20,
            16,
            20,
            MediaQuery.of(ctx).viewInsets.bottom + 24,
          ),
          children: [
            Text(
              'Edit work order #${j.id}',
              style: Theme.of(ctx).textTheme.titleLarge,
            ),
            const SizedBox(height: 4),
            Text(
              'Saving always holds the job for review.',
              style: TextStyle(fontSize: 12, color: ctx.pal.textDim),
            ),
            const SizedBox(height: 16),
            DropdownButtonFormField<String>(
              initialValue: workType,
              decoration: const InputDecoration(labelText: 'Work type'),
              items: const [
                DropdownMenuItem(value: 'coding', child: Text('Coding')),
                DropdownMenuItem(value: 'research', child: Text('Research')),
              ],
              onChanged: (v) => setSheet(() => workType = v ?? workType),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: source,
              minLines: 2,
              maxLines: 6,
              decoration: const InputDecoration(
                labelText: 'Original / rough request',
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: title,
              decoration: const InputDecoration(labelText: 'Title *'),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: outcome,
              minLines: 2,
              maxLines: 5,
              decoration: const InputDecoration(labelText: 'Outcome'),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: workContext,
              minLines: 2,
              maxLines: 6,
              decoration: const InputDecoration(labelText: 'Context'),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: plan,
              minLines: 3,
              maxLines: 10,
              decoration: const InputDecoration(
                labelText: 'Plan (one step per line)',
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: policy,
              minLines: 2,
              maxLines: 7,
              decoration: const InputDecoration(labelText: 'Policy'),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: acceptance,
              minLines: 3,
              maxLines: 10,
              decoration: const InputDecoration(
                labelText: 'Acceptance (one item per line)',
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: testing,
              minLines: 2,
              maxLines: 6,
              decoration: const InputDecoration(
                labelText: 'Phone test handoff',
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: assumptions,
              minLines: 2,
              maxLines: 6,
              decoration: const InputDecoration(
                labelText: 'Assumptions (one per line)',
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: scope,
              minLines: 2,
              maxLines: 6,
              decoration: const InputDecoration(labelText: 'Out of scope'),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: dependencies,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                labelText: 'Dependency job IDs',
                hintText: '8, 12',
              ),
            ),
            const SizedBox(height: 10),
            TextField(
              controller: project,
              decoration: const InputDecoration(
                labelText: 'Project path or name',
              ),
            ),
            const SizedBox(height: 10),
            DropdownButtonFormField<String>(
              initialValue: engine,
              decoration: const InputDecoration(labelText: 'Engine'),
              items: const [
                DropdownMenuItem(value: 'auto', child: Text('Auto')),
                DropdownMenuItem(value: 'claude', child: Text('Claude')),
                DropdownMenuItem(value: 'codex', child: Text('Codex')),
                DropdownMenuItem(value: 'gemini', child: Text('Gemini')),
              ],
              onChanged: (v) => setSheet(() => engine = v ?? engine),
            ),
            const SizedBox(height: 20),
            FilledButton.icon(
              onPressed: () async {
                if (title.text.trim().isEmpty) return;
                final deps = dependencies.text
                    .split(',')
                    .map((v) => int.tryParse(v.trim()))
                    .whereType<int>()
                    .toList();
                final edited = <String, dynamic>{
                  'version': 2,
                  'readiness': 'refined',
                  'work_type': workType,
                  'title': title.text.trim(),
                  'outcome': outcome.text.trim(),
                  'context': workContext.text.trim(),
                  'plan': lines(plan),
                  'policy': policy.text.trim(),
                  'acceptance': lines(acceptance),
                  'test_handoff': testing.text.trim(),
                  'out_of_scope': scope.text.trim(),
                  'assumptions': lines(assumptions),
                  'source_text': source.text.trim(),
                };
                try {
                  await ref
                      .read(apiProvider)
                      ?.editJob(
                        j.id,
                        spec: edited,
                        dependsOn: deps,
                        project: project.text.trim(),
                        engine: engine,
                      );
                  ref.invalidate(queueProvider);
                  if (ctx.mounted) Navigator.pop(ctx);
                } catch (e) {
                  if (ctx.mounted) {
                    ScaffoldMessenger.of(ctx).showSnackBar(
                      SnackBar(
                        content: Text(friendlyError(e)),
                        backgroundColor: GajalaColors.danger,
                      ),
                    );
                  }
                }
              },
              icon: const Icon(Icons.save_outlined),
              label: const Text('Save and hold for review'),
            ),
          ],
        ),
      ),
    ),
  );
}

Future<void> _jobDetailSheet(
  BuildContext context,
  WidgetRef ref,
  QueueJob j,
) async {
  final spec = j.spec;
  final plan = List<String>.from(spec['plan'] ?? const []);
  final acceptance = List<String>.from(spec['acceptance'] ?? const []);
  String value(String key) => spec[key]?.toString().trim() ?? '';
  String when(double? epoch) => epoch == null
      ? '—'
      : DateTime.fromMillisecondsSinceEpoch(
          (epoch * 1000).round(),
        ).toLocal().toString().split('.').first;

  Widget section(
    BuildContext ctx,
    String heading,
    String body, {
    IconData icon = Icons.notes_outlined,
  }) {
    if (body.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 18),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 16, color: GajalaColors.accent),
              const SizedBox(width: 7),
              Text(
                heading.toUpperCase(),
                style: Theme.of(ctx).textTheme.labelSmall?.copyWith(
                  color: GajalaColors.accent,
                  fontWeight: FontWeight.w800,
                  letterSpacing: .6,
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          SelectableText(
            body,
            style: TextStyle(color: ctx.pal.text, height: 1.4),
          ),
        ],
      ),
    );
  }

  await showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: context.pal.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (ctx) => DraggableScrollableSheet(
      expand: false,
      initialChildSize: .88,
      minChildSize: .5,
      maxChildSize: .96,
      builder: (_, scroll) => ListView(
        controller: scroll,
        padding: const EdgeInsets.fromLTRB(20, 14, 20, 32),
        children: [
          Center(
            child: Container(
              width: 42,
              height: 4,
              decoration: BoxDecoration(
                color: ctx.pal.border,
                borderRadius: BorderRadius.circular(4),
              ),
            ),
          ),
          const SizedBox(height: 18),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  j.title,
                  style: Theme.of(
                    ctx,
                  ).textTheme.titleLarge?.copyWith(fontWeight: FontWeight.w700),
                ),
              ),
              _JobMenu(j),
            ],
          ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 8,
            runSpacing: 6,
            children: [
              Chip(label: Text('#${j.id} · ${_statusStyle(j.status).label}')),
              Chip(
                avatar: Icon(
                  j.isDraft ? Icons.edit_note : Icons.auto_awesome,
                  size: 16,
                ),
                label: Text(j.isDraft ? 'Draft' : 'Agent-ready'),
              ),
              Chip(label: Text('${j.projectName} · ${j.engine}')),
              Chip(
                avatar: Icon(
                  j.workType == 'research' ? Icons.travel_explore : Icons.code,
                  size: 16,
                ),
                label: Text(j.workType == 'research' ? 'Research' : 'Coding'),
              ),
              if (j.tag == 'mine') const Chip(label: Text('held by me')),
            ],
          ),
          if (j.blockedBy.isNotEmpty)
            section(
              ctx,
              'Dependencies',
              'Blocked until #${j.blockedBy.join(', #')} ships or completes.',
              icon: Icons.account_tree_outlined,
            ),
          section(
            ctx,
            'Queue supervisor',
            [
              if ((j.supervision['blocker']?.toString() ?? '').isNotEmpty)
                'Why paused: ${j.supervision['blocker']}',
              if ((j.supervision['next_action']?.toString() ?? '').isNotEmpty)
                'Next action: ${j.supervision['next_action']}',
              'Attempts: ${j.supervision['attempts'] ?? 0}/${j.supervision['max_attempts'] ?? 3}',
              if ((j.supervision['failure_kind']?.toString() ?? '').isNotEmpty)
                'Failure type: ${j.supervision['failure_kind']}',
            ].join('\n'),
            icon: Icons.health_and_safety_outlined,
          ),
          if ((j.awareness['classification']?.toString() ?? '').isNotEmpty)
            section(
              ctx,
              'Project awareness',
              [
                'Decision: ${(j.awareness['classification'] ?? 'new').toString().replaceAll('_', ' ')} '
                    '(${(((j.awareness['confidence'] as num?) ?? 0) * 100).round()}% confidence)',
                if (j.awareness['match'] is Map &&
                    ((j.awareness['match'] as Map)['title']?.toString() ?? '')
                        .isNotEmpty)
                  'Closest match: ${(j.awareness['match'] as Map)['title']}',
                if (j.awareness['match'] is Map &&
                    ((j.awareness['match'] as Map)['evidence'] as List?)
                            ?.isNotEmpty ==
                        true)
                  'Evidence: ${((j.awareness['match'] as Map)['evidence'] as List).join(' · ')}',
                if ((j.awareness['action']?.toString() ?? '').isNotEmpty)
                  'Action: ${j.awareness['action']}',
              ].join('\n'),
              icon: Icons.hub_outlined,
            ),
          if (j.dependencies.isNotEmpty && j.blockedBy.isEmpty)
            section(
              ctx,
              'Dependencies',
              j.dependencies
                  .map(
                    (d) =>
                        '${d['satisfied'] == true ? '✓' : '○'} '
                        '#${d['id']} · ${d['status']} · ${d['title'] ?? ''}',
                  )
                  .join('\n'),
              icon: Icons.account_tree_outlined,
            ),
          section(ctx, 'Outcome', value('outcome'), icon: Icons.flag_outlined),
          section(ctx, 'Context', value('context')),
          section(
            ctx,
            'Approved plan',
            plan
                .asMap()
                .entries
                .map((e) => '${e.key + 1}. ${e.value}')
                .join('\n'),
            icon: Icons.route_outlined,
          ),
          section(ctx, 'Policy', value('policy'), icon: Icons.policy_outlined),
          section(
            ctx,
            'Acceptance',
            acceptance.map((v) => '• $v').join('\n'),
            icon: Icons.fact_check_outlined,
          ),
          section(
            ctx,
            'Phone test handoff',
            value('test_handoff'),
            icon: Icons.phone_android_outlined,
          ),
          section(
            ctx,
            'Out of scope',
            value('out_of_scope'),
            icon: Icons.block_outlined,
          ),
          if (List.from(j.spec['assumptions'] ?? const []).isNotEmpty)
            section(
              ctx,
              'Refinement assumptions',
              List.from(
                j.spec['assumptions'] ?? const [],
              ).map((v) => '• $v').join('\n'),
              icon: Icons.lightbulb_outline,
            ),
          if ((j.spec['source_text']?.toString() ?? '').isNotEmpty)
            section(
              ctx,
              'Original rough capture',
              j.spec['source_text'].toString(),
              icon: Icons.edit_note,
            ),
          section(
            ctx,
            'Run metadata',
            [
              'Created: ${when(j.createdAt)}',
              'Finished: ${when(j.endedAt)}',
              if (j.closedAt != null) 'Closed: ${when(j.closedAt)}',
              'Branch: ${j.branch ?? '—'}',
              'Tokens: ${j.tokensTotal}',
            ].join('\n'),
            icon: Icons.info_outline,
          ),
          ExpansionTile(
            tilePadding: EdgeInsets.zero,
            title: const Text('Raw worker prompt'),
            subtitle: const Text('Complete compatibility/audit copy'),
            children: [
              Align(
                alignment: Alignment.centerLeft,
                child: SelectableText(
                  j.task,
                  style: TextStyle(color: ctx.pal.text, height: 1.4),
                ),
              ),
            ],
          ),
          if (j.summary?.isNotEmpty == true)
            section(
              ctx,
              j.status == 'failed' ? 'Why it failed' : 'Worker summary',
              j.summary!,
              icon: Icons.summarize_outlined,
            ),
          if (j.filesChanged.isNotEmpty)
            section(
              ctx,
              'Files changed',
              j.filesChanged.join('\n'),
              icon: Icons.folder_copy_outlined,
            ),
          if (j.status == 'closed')
            section(
              ctx,
              'Closed',
              j.closeReason ?? 'No reason recorded',
              icon: Icons.archive_outlined,
            ),
          if (j.closureHistory.isNotEmpty)
            section(
              ctx,
              'Closure history',
              j.closureHistory
                  .map(
                    (h) =>
                        '${h['reason'] ?? 'Closed'} (from ${h['previous_status'] ?? 'unknown'})',
                  )
                  .join('\n'),
              icon: Icons.history,
            ),
          const SizedBox(height: 24),
          if (j.status != 'running' && j.status != 'closed')
            FilledButton.icon(
              onPressed: () async {
                try {
                  final instructions = await _refineInstructions(ctx);
                  if (instructions == null) return;
                  if (!ctx.mounted) return;
                  ScaffoldMessenger.of(ctx).showSnackBar(
                    const SnackBar(content: Text('Refining the rough task…')),
                  );
                  await ref
                      .read(apiProvider)
                      ?.refineJob(j.id, instructions: instructions);
                  ref.invalidate(queueProvider);
                  if (ctx.mounted) Navigator.pop(ctx);
                } catch (e) {
                  if (ctx.mounted) {
                    ScaffoldMessenger.of(ctx).showSnackBar(
                      SnackBar(
                        content: Text(friendlyError(e)),
                        backgroundColor: GajalaColors.danger,
                      ),
                    );
                  }
                }
              },
              icon: const Icon(Icons.auto_fix_high_outlined),
              label: Text(
                j.isDraft ? 'Refine with Claude' : 'Re-refine with Claude',
              ),
            ),
          if (j.status == 'closed')
            FilledButton.icon(
              onPressed: () async {
                await ref.read(apiProvider)?.reopenJob(j.id);
                ref.invalidate(queueProvider);
                if (ctx.mounted) Navigator.pop(ctx);
              },
              icon: const Icon(Icons.unarchive_outlined),
              label: const Text('Reopen as held'),
            ),
        ],
      ),
    ),
  );
}

// ── Night Shift header (state + settings) ──────────────────────────────────────

class _QueueHealthCard extends ConsumerWidget {
  final Map<String, dynamic> health;
  const _QueueHealthCard(this.health);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final state = health['state']?.toString() ?? 'working';
    final attention = state == 'attention';
    final healthy = state == 'healthy';
    final color = attention
        ? GajalaColors.amber
        : healthy
        ? GajalaColors.green
        : GajalaColors.blue;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .10),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: color.withValues(alpha: .35)),
      ),
      child: Row(
        children: [
          Icon(
            attention
                ? Icons.manage_search
                : healthy
                ? Icons.verified_outlined
                : Icons.health_and_safety_outlined,
            color: color,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'QUEUE SUPERVISOR · ${state.toUpperCase()}',
                  style: TextStyle(
                    color: color,
                    fontSize: 11,
                    fontWeight: FontWeight.w800,
                    letterSpacing: .5,
                  ),
                ),
                const SizedBox(height: 3),
                Text(
                  health['headline']?.toString() ??
                      'Continuously checking tasks and deployments.',
                  style: TextStyle(color: context.pal.text, fontSize: 13),
                ),
                const SizedBox(height: 3),
                Text(
                  '${health['working'] ?? 0} working · ${health['recovering'] ?? 0} recovering · ${health['held'] ?? 0} held · ${health['needs_attention'] ?? 0} needs decision\n${health['capabilities_known'] ?? 0} verified project capabilities known',
                  style: TextStyle(color: context.pal.textDim, fontSize: 11),
                ),
                if ((health['awareness_error']?.toString() ?? '').isNotEmpty)
                  Text(
                    'Awareness refresh issue: ${health['awareness_error']}',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                      color: GajalaColors.amber,
                      fontSize: 11,
                    ),
                  ),
              ],
            ),
          ),
          IconButton(
            tooltip: 'Run supervisor now',
            onPressed: () async {
              try {
                await ref.read(apiProvider)?.superviseQueue();
                ref.invalidate(queueProvider);
              } catch (e) {
                if (context.mounted) {
                  ScaffoldMessenger.of(
                    context,
                  ).showSnackBar(SnackBar(content: Text(friendlyError(e))));
                }
              }
            },
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
    );
  }
}

class _NightShiftHeader extends ConsumerWidget {
  final Map<String, dynamic> settings;
  const _NightShiftHeader(this.settings);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pal = context.pal;
    final on = settings['enabled'] == true;
    final window =
        '${settings['start'] ?? '23:00'}–${settings['end'] ?? '07:00'}';
    return Container(
      padding: const EdgeInsets.fromLTRB(14, 10, 8, 10),
      decoration: BoxDecoration(
        color: (on ? GajalaColors.green : pal.textDim).withValues(alpha: .10),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(
          color: (on ? GajalaColors.green : pal.border).withValues(alpha: .4),
        ),
      ),
      child: Row(
        children: [
          Icon(
            Icons.nightlight_round,
            color: on ? GajalaColors.green : pal.textDim,
            size: 20,
          ),
          const SizedBox(width: 10),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  on ? 'Night Shift on' : 'Night Shift off',
                  style: TextStyle(
                    fontWeight: FontWeight.w700,
                    color: pal.text,
                  ),
                ),
                Text(
                  on
                      ? 'Builds queued tasks $window'
                      : 'Tap the switch to run overnight',
                  style: TextStyle(fontSize: 12, color: pal.textDim),
                ),
              ],
            ),
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
        ],
      ),
    );
  }
}

Future<void> _settingsSheet(
  BuildContext context,
  WidgetRef ref,
  Map<String, dynamic> s,
) async {
  final start = TextEditingController(text: '${s['start'] ?? '23:00'}');
  final end = TextEditingController(text: '${s['end'] ?? '07:00'}');
  final maxJobs = TextEditingController(text: '${s['max_jobs'] ?? 12}');
  await showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: context.pal.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
    ),
    builder: (ctx) => Padding(
      padding: EdgeInsets.fromLTRB(
        20,
        18,
        20,
        MediaQuery.of(ctx).viewInsets.bottom + 20,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            'Night Shift settings',
            style: Theme.of(ctx).textTheme.titleMedium,
          ),
          const SizedBox(height: 14),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: start,
                  decoration: const InputDecoration(labelText: 'Start (HH:MM)'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: TextField(
                  controller: end,
                  decoration: const InputDecoration(labelText: 'End (HH:MM)'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          TextField(
            controller: maxJobs,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: 'Max jobs / night'),
          ),
          const SizedBox(height: 18),
          SizedBox(
            width: double.infinity,
            child: FilledButton(
              onPressed: () async {
                await ref.read(apiProvider)?.setQueueSettings({
                  'start': start.text.trim(),
                  'end': end.text.trim(),
                  'max_jobs':
                      int.tryParse(maxJobs.text.trim()) ?? s['max_jobs'],
                });
                ref.invalidate(queueProvider);
                if (ctx.mounted) Navigator.pop(ctx);
              },
              child: const Text('Save'),
            ),
          ),
        ],
      ),
    ),
  );
}

// ── add-job sheet ─────────────────────────────────────────────────────────────

Future<void> _addSheet(BuildContext context, WidgetRef ref) async {
  final rough = TextEditingController();
  final roughProject = TextEditingController();
  final title = TextEditingController();
  final outcome = TextEditingController();
  final workContext = TextEditingController();
  final plan = TextEditingController();
  final policy = TextEditingController(
    text:
        'Stay inside the selected repo. Preserve user changes. Stop and ask before destructive or external actions.',
  );
  final acceptance = TextEditingController();
  final testing = TextEditingController();
  final outOfScope = TextEditingController();
  final dependencies = TextEditingController();
  final project = TextEditingController();
  final current = ref
      .read(projectsProvider)
      .valueOrNull?['current_name']
      ?.toString();
  String tag = 'auto';
  String engine = 'auto';
  await showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: context.pal.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(18)),
    ),
    builder: (ctx) => StatefulBuilder(
      builder: (ctx, setSheet) {
        Widget chip(
          String label,
          String value,
          String group,
          void Function() pick,
        ) {
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

        List<String> lines(TextEditingController c) => c.text
            .split('\n')
            .map((v) => v.trim())
            .where((v) => v.isNotEmpty)
            .toList();
        return DraggableScrollableSheet(
          expand: false,
          initialChildSize: .92,
          minChildSize: .6,
          maxChildSize: .97,
          builder: (_, scroll) => ListView(
            controller: scroll,
            padding: EdgeInsets.fromLTRB(
              20,
              14,
              20,
              MediaQuery.of(ctx).viewInsets.bottom + 20,
            ),
            children: [
              Center(
                child: Container(
                  width: 42,
                  height: 4,
                  decoration: BoxDecoration(
                    color: ctx.pal.border,
                    borderRadius: BorderRadius.circular(4),
                  ),
                ),
              ),
              const SizedBox(height: 14),
              Text('New work order', style: Theme.of(ctx).textTheme.titleLarge),
              const SizedBox(height: 4),
              Text(
                'Capture it quickly now, then let an agent refine it.',
                style: TextStyle(fontSize: 12, color: ctx.pal.textDim),
              ),
              const SizedBox(height: 16),
              TextField(
                controller: rough,
                autofocus: true,
                minLines: 3,
                maxLines: 7,
                decoration: const InputDecoration(
                  labelText: 'Rough task',
                  hintText: 'Say it naturally — incomplete is okay',
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: roughProject,
                decoration: InputDecoration(
                  labelText: 'Project',
                  hintText: current == null
                      ? 'active project'
                      : 'blank = $current',
                ),
              ),
              const SizedBox(height: 10),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  onPressed: () async {
                    if (rough.text.trim().isEmpty) return;
                    try {
                      await ref
                          .read(apiProvider)
                          ?.addJob(
                            rough.text.trim(),
                            project: roughProject.text.trim().isEmpty
                                ? null
                                : roughProject.text.trim(),
                            tag: 'mine',
                          );
                      ref.invalidate(queueProvider);
                      if (ctx.mounted) Navigator.pop(ctx);
                    } catch (e) {
                      if (ctx.mounted) {
                        ScaffoldMessenger.of(ctx).showSnackBar(
                          SnackBar(
                            content: Text(friendlyError(e)),
                            backgroundColor: GajalaColors.danger,
                          ),
                        );
                      }
                    }
                  },
                  icon: const Icon(Icons.edit_note),
                  label: const Text('Save as draft'),
                ),
              ),
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 18),
                child: Row(
                  children: [
                    Expanded(child: Divider()),
                    Padding(
                      padding: EdgeInsets.symmetric(horizontal: 12),
                      child: Text('OR ADD REFINED WORK'),
                    ),
                    Expanded(child: Divider()),
                  ],
                ),
              ),
              TextField(
                controller: title,
                decoration: const InputDecoration(labelText: 'Title *'),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: outcome,
                minLines: 2,
                maxLines: 4,
                decoration: const InputDecoration(
                  labelText: 'Outcome *',
                  hintText: 'What should be true when this is done?',
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: workContext,
                minLines: 2,
                maxLines: 5,
                decoration: const InputDecoration(
                  labelText: 'Context',
                  hintText:
                      'Background, current behavior, useful files or constraints',
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: plan,
                minLines: 3,
                maxLines: 8,
                decoration: const InputDecoration(
                  labelText: 'Approved plan * (one step per line)',
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: acceptance,
                minLines: 3,
                maxLines: 8,
                decoration: const InputDecoration(
                  labelText: 'Acceptance criteria * (one per line)',
                ),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: testing,
                minLines: 2,
                maxLines: 5,
                decoration: const InputDecoration(
                  labelText: 'Phone test handoff *',
                  hintText:
                      'URL/app path and exact steps you can test from your phone',
                ),
              ),
              const SizedBox(height: 10),
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                title: const Text('Safety, scope & dependencies'),
                children: [
                  TextField(
                    controller: policy,
                    minLines: 3,
                    maxLines: 7,
                    decoration: const InputDecoration(
                      labelText: 'Worker policy',
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: outOfScope,
                    minLines: 2,
                    maxLines: 5,
                    decoration: const InputDecoration(
                      labelText: 'Out of scope',
                    ),
                  ),
                  const SizedBox(height: 10),
                  TextField(
                    controller: dependencies,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(
                      labelText: 'Depends on job IDs',
                      hintText: '8, 12',
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),
              TextField(
                controller: project,
                decoration: InputDecoration(
                  labelText: 'Project',
                  hintText: current == null
                      ? 'active project'
                      : 'blank = $current',
                ),
              ),
              const SizedBox(height: 14),
              Text('WHO RUNS IT', style: Theme.of(ctx).textTheme.labelSmall),
              const SizedBox(height: 6),
              Row(
                children: [
                  chip('Auto (overnight)', 'auto', tag, () => tag = 'auto'),
                  chip('Mine (hold)', 'mine', tag, () => tag = 'mine'),
                ],
              ),
              const SizedBox(height: 12),
              Text('ENGINE', style: Theme.of(ctx).textTheme.labelSmall),
              const SizedBox(height: 6),
              Wrap(
                children: [
                  for (final e in ['auto', 'claude', 'codex', 'gemini'])
                    chip(e, e, engine, () => engine = e),
                ],
              ),
              const SizedBox(height: 18),
              SizedBox(
                width: double.infinity,
                child: FilledButton(
                  onPressed: () async {
                    final requiredMissing =
                        title.text.trim().isEmpty ||
                        outcome.text.trim().isEmpty ||
                        lines(plan).isEmpty ||
                        lines(acceptance).isEmpty ||
                        testing.text.trim().isEmpty;
                    if (tag == 'auto' && requiredMissing) {
                      ScaffoldMessenger.of(ctx).showSnackBar(
                        const SnackBar(
                          content: Text(
                            'Auto work needs title, outcome, plan, acceptance, and phone testing.',
                          ),
                        ),
                      );
                      return;
                    }
                    if (title.text.trim().isEmpty) return;
                    try {
                      final deps = dependencies.text
                          .split(',')
                          .map((v) => int.tryParse(v.trim()))
                          .whereType<int>()
                          .toList();
                      final spec = <String, dynamic>{
                        'version': 2,
                        'readiness': 'refined',
                        'title': title.text.trim(),
                        'outcome': outcome.text.trim(),
                        'context': workContext.text.trim(),
                        'plan': lines(plan),
                        'policy': policy.text.trim(),
                        'acceptance': lines(acceptance),
                        'test_handoff': testing.text.trim(),
                        'out_of_scope': outOfScope.text.trim(),
                      };
                      await ref
                          .read(apiProvider)
                          ?.addJob(
                            '',
                            project: project.text.trim().isEmpty
                                ? null
                                : project.text.trim(),
                            tag: tag,
                            engine: engine,
                            spec: spec,
                            dependsOn: deps,
                          );
                      ref.invalidate(queueProvider);
                      if (ctx.mounted) Navigator.pop(ctx);
                    } catch (e) {
                      if (ctx.mounted) {
                        ScaffoldMessenger.of(ctx).showSnackBar(
                          SnackBar(
                            content: Text(friendlyError(e)),
                            backgroundColor: GajalaColors.danger,
                          ),
                        );
                      }
                    }
                  },
                  child: const Text('Add to queue'),
                ),
              ),
            ],
          ),
        );
      },
    ),
  );
}
