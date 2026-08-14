import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';

const _kinds = ['all', 'bug', 'feature', 'idea', 'todo', 'question', 'note'];
const _kindColor = {
  'bug': GajalaColors.danger,
  'feature': GajalaColors.accent,
  'idea': GajalaColors.warn,
  'todo': GajalaColors.ok,
  'question': Color(0xFFB57EDC),
};

class NotesScreen extends ConsumerStatefulWidget {
  /// When true (e.g. opened from the Brain-dump home widget), pop the note
  /// composer open right away for a fast capture.
  final bool openComposer;
  const NotesScreen({super.key, this.openComposer = false});
  @override
  ConsumerState<NotesScreen> createState() => _NotesScreenState();
}

class _NotesScreenState extends ConsumerState<NotesScreen> {
  String _filter = 'all';
  String _tabFilter = 'open'; // open | closed

  @override
  void initState() {
    super.initState();
    if (widget.openComposer) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _addDialog(context);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final notesAsync = ref.watch(notesProvider(_tabFilter));
    return Scaffold(
      appBar: AppBar(
        title: const Text('Brain Dump'),
        actions: [
          IconButton(
            tooltip: 'New brain dump',
            onPressed: () => _addDialog(context),
            icon: const Icon(Icons.add),
          ),
          const SizedBox(width: 6),
        ],
      ),
      body: Column(
        children: [
          // Tabs: Open / Closed
          SizedBox(
            height: 40,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
              children: ['open', 'closed']
                  .map(
                    (tab) => Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        label: Text(tab == 'open' ? 'Open' : 'Closed'),
                        selected: _tabFilter == tab,
                        onSelected: (_) => setState(() => _tabFilter = tab),
                        selectedColor: GajalaColors.accentDim,
                      ),
                    ),
                  )
                  .toList(),
            ),
          ),
          // Kind filters
          SizedBox(
            height: 48,
            child: ListView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 12),
              children: _kinds
                  .map(
                    (k) => Padding(
                      padding: const EdgeInsets.only(right: 8, top: 8),
                      child: ChoiceChip(
                        label: Text(k == 'all' ? 'All' : '${k}s'),
                        selected: _filter == k,
                        onSelected: (_) => setState(() => _filter = k),
                        selectedColor: GajalaColors.accentDim,
                      ),
                    ),
                  )
                  .toList(),
            ),
          ),
          Expanded(
            child: notesAsync.when(
              data: (notes) {
                final shown = _filter == 'all'
                    ? notes
                    : notes.where((n) => n.kind == _filter).toList();
                if (shown.isEmpty) {
                  return Center(
                    child: Text(
                      'No notes here ra 🍃',
                      style: TextStyle(color: context.pal.textDim),
                    ),
                  );
                }
                return RefreshIndicator(
                  onRefresh: () async => ref.invalidate(notesProvider),
                  child: ListView.builder(
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 24),
                    itemCount: shown.length,
                    itemBuilder: (_, i) => _NoteCard(shown[i], ref),
                  ),
                );
              },
              loading: () => const Center(child: CircularProgressIndicator()),
              error: (e, _) => Center(
                child: Text(
                  friendlyError(e),
                  style: const TextStyle(color: GajalaColors.danger),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _addDialog(BuildContext context) async {
    final title = TextEditingController();
    final body = TextEditingController();
    var kind = 'todo';
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(
        builder: (c, setLocal) => AlertDialog(
          backgroundColor: context.pal.surface,
          title: const Text('New brain dump'),
          content: SizedBox(
            width: double.maxFinite,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // Title field
                  Text(
                    'Title',
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: context.pal.text,
                          fontWeight: FontWeight.w600,
                        ),
                  ),
                  const SizedBox(height: 8),
                  TextField(
                    controller: title,
                    decoration: const InputDecoration(
                      hintText: "What's on your mind?",
                      isDense: true,
                    ),
                    autofocus: true,
                  ),
                  const SizedBox(height: 20),
                  // Details field
                  Text(
                    'Details (optional)',
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: context.pal.text,
                          fontWeight: FontWeight.w600,
                        ),
                  ),
                  const SizedBox(height: 8),
                  TextField(
                    controller: body,
                    decoration: const InputDecoration(
                      hintText: 'Add context, links, or any extra details...',
                      isDense: true,
                    ),
                    maxLines: 4,
                  ),
                  const SizedBox(height: 20),
                  // Kind selector
                  Text(
                    'Category',
                    style: Theme.of(context).textTheme.labelSmall?.copyWith(
                          color: context.pal.text,
                          fontWeight: FontWeight.w600,
                        ),
                  ),
                  const SizedBox(height: 10),
                  Wrap(
                    spacing: 8,
                    runSpacing: 8,
                    children: ['bug', 'feature', 'idea', 'todo', 'question']
                        .map(
                          (k) => ChoiceChip(
                            label: Text(k),
                            selected: kind == k,
                            onSelected: (_) => setLocal(() => kind = k),
                          ),
                        )
                        .toList(),
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: const Text('Add'),
            ),
          ],
        ),
      ),
    );
    if (ok == true && title.text.trim().isNotEmpty) {
      final api = ref.read(apiProvider);
      try {
        await api!.createNote(
          kind: kind,
          title: title.text.trim(),
          body: body.text.trim(),
        );
        ref.invalidate(notesProvider('open'));
        ref.invalidate(notesProvider('closed'));
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
    }
  }
}

class _NoteCard extends StatelessWidget {
  final Note note;
  final WidgetRef ref;
  const _NoteCard(this.note, this.ref);

  @override
  Widget build(BuildContext context) {
    final color = _kindColor[note.kind] ?? context.pal.textDim;
    final isClosed = note.status == 'closed';

    return Dismissible(
      key: ValueKey(note.id),
      background: _swipeBg(
        Alignment.centerLeft,
        Icons.check,
        GajalaColors.ok,
        'Done',
      ),
      // No secondary (right) background: close requires explicit action with reason
      confirmDismiss: isClosed ? (_) async => false : null,
      onDismissed: (dir) async {
        if (dir == DismissDirection.startToEnd) {
          final api = ref.read(apiProvider);
          await api?.setNoteStatus(note.id, 'done');
          ref.invalidate(notesProvider('open'));
          ref.invalidate(notesProvider('closed'));
          if (context.mounted) {
            ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(
                content: Text('#${note.id} done'),
                duration: const Duration(seconds: 1),
              ),
            );
          }
        }
      },
      child: Card(
        margin: const EdgeInsets.only(bottom: 8),
        child: Column(
          children: [
            ListTile(
              dense: true,
              contentPadding: const EdgeInsets.fromLTRB(14, 6, 14, 2),
              visualDensity: const VisualDensity(vertical: -2),
              leading: Container(width: 4, height: 40, color: color),
              title: Text(
                note.title,
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
              subtitle: Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Container(
                          padding: const EdgeInsets.symmetric(
                            horizontal: 7,
                            vertical: 2,
                          ),
                          decoration: BoxDecoration(
                            color: color.withValues(alpha: .2),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Text(
                            note.kind,
                            style: TextStyle(color: color, fontSize: 11),
                          ),
                        ),
                        if (note.project != null) ...[
                          const SizedBox(width: 8),
                          Flexible(
                            child: Text(
                              note.project!,
                              overflow: TextOverflow.ellipsis,
                              style: TextStyle(
                                color: context.pal.textDim,
                                fontSize: 12,
                              ),
                            ),
                          ),
                        ],
                      ],
                    ),
                    if (isClosed && note.closeReason != null) ...[
                      const SizedBox(height: 6),
                      Text(
                        'Closed: ${note.closeReason}',
                        style: TextStyle(
                          color: context.pal.textDim,
                          fontSize: 11,
                          fontStyle: FontStyle.italic,
                        ),
                      ),
                    ],
                  ],
                ),
              ),
              onTap: note.body.isEmpty
                  ? null
                  : () => showDialog(
                      context: context,
                      builder: (c) => AlertDialog(
                        backgroundColor: context.pal.surface,
                        title: Text(note.title),
                        content: SingleChildScrollView(
                          child: SelectableText(note.body),
                        ),
                        actions: [
                          TextButton(
                            onPressed: () => Navigator.pop(c),
                            child: const Text('Close'),
                          ),
                        ],
                      ),
                    ),
            ),
            // Action buttons based on status
            Padding(
              padding: const EdgeInsets.fromLTRB(14, 2, 14, 8),
              child: Align(
                alignment: Alignment.centerRight,
                child: Wrap(
                  spacing: 6,
                  runSpacing: 4,
                  children: _buildActions(context),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  List<Widget> _buildActions(BuildContext context) {
    if (note.status == 'closed') {
      return [
        OutlinedButton.icon(
          icon: const Icon(Icons.undo, size: 16),
          label: const Text('Reopen'),
          onPressed: () => _reopen(context),
        ),
      ];
    }

    final actions = <Widget>[
      if (note.queueJobId != null)
        TextButton.icon(
          icon: const Icon(Icons.task_alt, size: 16),
          label: Text(
            'Queue #${note.queueJobId} · ${note.queueJobStatus ?? 'linked'}',
          ),
          onPressed: null,
        )
      else if (note.kind == 'todo')
        TextButton.icon(
          icon: const Icon(Icons.arrow_forward, size: 16),
          label: const Text('Move to queue'),
          onPressed: () => _convertToQueue(context),
        ),
      TextButton.icon(
        icon: const Icon(Icons.archive_outlined, size: 16),
        label: const Text('Close'),
        onPressed: () => _closeDialog(context),
      ),
    ];
    return actions;
  }

  Future<void> _closeDialog(BuildContext context) async {
    final reasonCtrl = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: context.pal.surface,
        title: const Text('Close note'),
        content: TextField(
          controller: reasonCtrl,
          decoration: const InputDecoration(
            hintText: 'Reason for closing (optional)',
          ),
          maxLines: 3,
          autofocus: true,
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Close'),
          ),
        ],
      ),
    );
    if (ok == true) {
      try {
        final api = ref.read(apiProvider);
        await api?.closeNote(note.id, reason: reasonCtrl.text.trim());
        ref.invalidate(notesProvider('open'));
        ref.invalidate(notesProvider('closed'));
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              content: Text('#${note.id} closed'),
              duration: const Duration(seconds: 1),
            ),
          );
        }
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
    }
  }

  Future<void> _reopen(BuildContext context) async {
    try {
      final api = ref.read(apiProvider);
      await api?.reopenNote(note.id);
      ref.invalidate(notesProvider('open'));
      ref.invalidate(notesProvider('closed'));
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('#${note.id} reopened'),
            duration: const Duration(seconds: 1),
          ),
        );
      }
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
  }

  Future<void> _convertToQueue(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        backgroundColor: context.pal.surface,
        title: const Text('Move to Tasks?'),
        content: const Text(
          'This creates a linked Draft work order for refinement and moves the '
          'brain dump to Closed. Both remain recoverable.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          FilledButton.icon(
            onPressed: () => Navigator.pop(c, true),
            icon: const Icon(Icons.arrow_forward),
            label: const Text('Move'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    try {
      final job = await ref.read(apiProvider)?.convertNoteToQueue(note.id);
      ref.invalidate(notesProvider('open'));
      ref.invalidate(notesProvider('closed'));
      ref.invalidate(queueProvider);
      if (context.mounted && job != null) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Moved to Draft task #${job.id} · refine when ready'),
          ),
        );
      }
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
  }

  Widget _swipeBg(Alignment a, IconData i, Color c, String label) => Container(
    color: c.withValues(alpha: .25),
    alignment: a,
    padding: const EdgeInsets.symmetric(horizontal: 20),
    child: Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(i, color: c),
        const SizedBox(width: 6),
        Text(
          label,
          style: TextStyle(color: c, fontWeight: FontWeight.w600),
        ),
      ],
    ),
  );
}
