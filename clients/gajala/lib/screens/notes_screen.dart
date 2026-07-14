import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';

const _kinds = ['all', 'bug', 'feature', 'idea', 'todo', 'question', 'note'];
const _kindColor = {
  'bug': GajalaColors.danger, 'feature': GajalaColors.accent,
  'idea': GajalaColors.warn, 'todo': GajalaColors.ok,
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
    final notesAsync = ref.watch(notesProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Brain Dump')),
      body: Column(children: [
        SizedBox(
          height: 48,
          child: ListView(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            children: _kinds.map((k) => Padding(
              padding: const EdgeInsets.only(right: 8, top: 8),
              child: ChoiceChip(
                label: Text(k == 'all' ? 'All' : '${k}s'),
                selected: _filter == k,
                onSelected: (_) => setState(() => _filter = k),
                selectedColor: GajalaColors.accentDim,
              ),
            )).toList(),
          ),
        ),
        Expanded(
          child: notesAsync.when(
            data: (notes) {
              final shown = _filter == 'all'
                  ? notes : notes.where((n) => n.kind == _filter).toList();
              if (shown.isEmpty) {
                return Center(child: Text('No notes here ra 🍃',
                    style: TextStyle(color: context.pal.textDim)));
              }
              return RefreshIndicator(
                onRefresh: () async => ref.invalidate(notesProvider),
                child: ListView.builder(
                  padding: const EdgeInsets.all(12),
                  itemCount: shown.length,
                  itemBuilder: (_, i) => _NoteCard(shown[i], ref),
                ),
              );
            },
            loading: () => const Center(child: CircularProgressIndicator()),
            error: (e, _) => Center(child: Text(friendlyError(e),
                style: const TextStyle(color: GajalaColors.danger))),
          ),
        ),
      ]),
      floatingActionButton: FloatingActionButton(
        backgroundColor: GajalaColors.accent,
        onPressed: () => _addDialog(context),
        child: const Icon(Icons.add, color: Colors.white),
      ),
    );
  }

  Future<void> _addDialog(BuildContext context) async {
    final title = TextEditingController();
    final body = TextEditingController();
    var kind = 'todo';
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => StatefulBuilder(builder: (c, setLocal) => AlertDialog(
        backgroundColor: context.pal.surface,
        title: const Text('New brain dump'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(controller: title, decoration: const InputDecoration(hintText: 'Title'), autofocus: true),
          const SizedBox(height: 10),
          TextField(controller: body, decoration: const InputDecoration(hintText: 'Details (optional)'), maxLines: 3),
          const SizedBox(height: 10),
          Wrap(spacing: 6, children: ['bug','feature','idea','todo','question'].map((k) =>
            ChoiceChip(label: Text(k), selected: kind == k,
              onSelected: (_) => setLocal(() => kind = k))).toList()),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Add')),
        ],
      )),
    );
    if (ok == true && title.text.trim().isNotEmpty) {
      final api = ref.read(apiProvider);
      try {
        await api!.createNote(kind: kind, title: title.text.trim(), body: body.text.trim());
        ref.invalidate(notesProvider);
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
              SnackBar(content: Text(friendlyError(e)), backgroundColor: GajalaColors.danger));
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
    return Dismissible(
      key: ValueKey(note.id),
      background: _swipeBg(Alignment.centerLeft, Icons.check, GajalaColors.ok, 'Done'),
      secondaryBackground: _swipeBg(Alignment.centerRight, Icons.delete, GajalaColors.danger, 'Drop'),
      onDismissed: (dir) async {
        final api = ref.read(apiProvider);
        final status = dir == DismissDirection.startToEnd ? 'done' : 'dropped';
        await api?.setNoteStatus(note.id, status);
        ref.invalidate(notesProvider);
        if (context.mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('#${note.id} $status'), duration: const Duration(seconds: 1)));
        }
      },
      child: Card(
        margin: const EdgeInsets.only(bottom: 10),
        child: ListTile(
          leading: Container(width: 4, height: 40, color: color),
          title: Text(note.title, style: const TextStyle(fontWeight: FontWeight.w600)),
          subtitle: Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Row(children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                decoration: BoxDecoration(color: color.withValues(alpha: .2), borderRadius: BorderRadius.circular(6)),
                child: Text(note.kind, style: TextStyle(color: color, fontSize: 11)),
              ),
              if (note.project != null) ...[
                const SizedBox(width: 8),
                Flexible(child: Text(note.project!, overflow: TextOverflow.ellipsis,
                    style: TextStyle(color: context.pal.textDim, fontSize: 12))),
              ],
            ]),
          ),
          onTap: note.body.isEmpty ? null : () => showDialog(
            context: context,
            builder: (c) => AlertDialog(
              backgroundColor: context.pal.surface,
              title: Text(note.title),
              content: SingleChildScrollView(child: SelectableText(note.body)),
              actions: [TextButton(onPressed: () => Navigator.pop(c), child: const Text('Close'))],
            ),
          ),
        ),
      ),
    );
  }

  Widget _swipeBg(Alignment a, IconData i, Color c, String label) => Container(
    color: c.withValues(alpha: .25),
    alignment: a,
    padding: const EdgeInsets.symmetric(horizontal: 20),
    child: Row(mainAxisSize: MainAxisSize.min, children: [
      Icon(i, color: c), const SizedBox(width: 6),
      Text(label, style: TextStyle(color: c, fontWeight: FontWeight.w600)),
    ]),
  );
}
