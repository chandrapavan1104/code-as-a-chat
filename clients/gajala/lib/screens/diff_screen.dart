// Review a project's uncommitted changes on the phone, and point the agent at
// specific lines. Long-press a line to start a selection, tap another line in
// the same file to extend it, then send the range to the chat or copy it.
// Returns the chat snippet (if any) to the caller via Navigator.pop.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/diff.dart';
import '../core/state.dart';
import '../core/theme.dart';

class DiffScreen extends ConsumerStatefulWidget {
  final String? project;
  const DiffScreen({super.key, this.project});
  @override
  ConsumerState<DiffScreen> createState() => _DiffScreenState();
}

class _DiffScreenState extends ConsumerState<DiffScreen> {
  ProjectDiff? _diff;
  String? _error;
  bool _loading = true;
  bool _numbers = false;
  final Set<String> _collapsed = {};
  // Selection: one file, an inclusive index range into DiffFile.allLines.
  DiffFile? _selFile;
  int? _selStart;
  int? _selEnd;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final d = await api.projectDiff(widget.project);
      if (mounted) setState(() => _diff = d);
    } catch (e) {
      if (mounted) setState(() => _error = friendlyError(e));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  List<DiffLine> get _selected {
    final f = _selFile, a = _selStart, b = _selEnd;
    if (f == null || a == null || b == null) return const [];
    final lines = f.allLines;
    final lo = a < b ? a : b, hi = a < b ? b : a;
    return lines.sublist(lo, hi + 1);
  }

  bool _isSelected(DiffFile f, int i) {
    if (!identical(f, _selFile) || _selStart == null || _selEnd == null) return false;
    final lo = _selStart! < _selEnd! ? _selStart! : _selEnd!;
    final hi = _selStart! < _selEnd! ? _selEnd! : _selStart!;
    return i >= lo && i <= hi;
  }

  void _startSelection(DiffFile f, int i) {
    HapticFeedback.selectionClick();
    setState(() {
      _selFile = f;
      _selStart = i;
      _selEnd = i;
    });
  }

  void _tapLine(DiffFile f, int i) {
    if (_selFile == null) return;
    if (!identical(f, _selFile)) {
      _startSelection(f, i); // a new file starts a new selection
      return;
    }
    setState(() => _selEnd = i);
  }

  void _clearSelection() => setState(() {
    _selFile = null;
    _selStart = null;
    _selEnd = null;
  });

  Future<void> _copy(String text, String what) async {
    await Clipboard.setData(ClipboardData(text: text));
    if (mounted) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('$what copied')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final d = _diff;
    final sel = _selected;
    return Scaffold(
      appBar: AppBar(
        title: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('Changes${d == null ? '' : ' · ${d.project}'}'),
            if (d?.branch != null)
              Text('${d!.branch} @ ${d.head}',
                  style: TextStyle(fontSize: 11, color: context.pal.textDim)),
          ],
        ),
        actions: [
          IconButton(
            tooltip: _numbers ? 'Hide line numbers' : 'Show line numbers',
            icon: Icon(_numbers ? Icons.format_list_numbered : Icons.notes),
            onPressed: () => setState(() => _numbers = !_numbers),
          ),
          IconButton(
            tooltip: 'Refresh',
            icon: const Icon(Icons.refresh),
            onPressed: _loading ? null : _load,
          ),
        ],
      ),
      body: _body(context, d),
      bottomNavigationBar: sel.isEmpty
          ? null
          : SafeArea(
              child: Container(
                padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
                decoration: BoxDecoration(
                  color: context.pal.surface,
                  border: Border(top: BorderSide(color: context.pal.border)),
                ),
                child: Row(children: [
                  Expanded(
                    child: Text(
                      '${sel.length} line${sel.length == 1 ? '' : 's'} · ${_selFile!.path}',
                      overflow: TextOverflow.ellipsis,
                      style: TextStyle(color: context.pal.textDim),
                    ),
                  ),
                  IconButton(
                    tooltip: 'Clear selection',
                    icon: const Icon(Icons.close),
                    onPressed: _clearSelection,
                  ),
                  TextButton(
                    onPressed: () => _copy(
                        sel.map((l) => l.text).join('\n'), 'Lines'),
                    child: const Text('Copy'),
                  ),
                  FilledButton(
                    onPressed: () => Navigator.of(context)
                        .pop(snippetForChat(_selFile!, sel)),
                    child: const Text('To chat'),
                  ),
                ]),
              ),
            ),
    );
  }

  Widget _body(BuildContext context, ProjectDiff? d) {
    if (_loading && d == null) {
      return const Center(child: CircularProgressIndicator());
    }
    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Text(_error!, textAlign: TextAlign.center),
        ),
      );
    }
    if (d == null || d.files.isEmpty) {
      return Center(
        child: Text('No uncommitted changes',
            style: TextStyle(color: context.pal.textDim)),
      );
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.fromLTRB(8, 8, 8, 24),
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(4, 0, 4, 8),
            child: Text(
              '${d.files.length} file${d.files.length == 1 ? '' : 's'} · '
              '+${d.additions} −${d.deletions}'
              '${d.omittedFiles > 0 ? ' · ${d.omittedFiles} more not shown (too large)' : ''}'
              '\nLong-press a line to select, tap another to extend.',
              style: TextStyle(fontSize: 12, color: context.pal.textDim),
            ),
          ),
          for (final f in d.files) _fileCard(context, f),
        ],
      ),
    );
  }

  Widget _fileCard(BuildContext context, DiffFile f) {
    final open = !_collapsed.contains(f.path);
    final statusColor = switch (f.status) {
      'A' => GajalaColors.ok,
      'D' => GajalaColors.danger,
      'R' => GajalaColors.warn,
      _ => GajalaColors.accent,
    };
    final lines = f.allLines;
    var index = 0;
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      clipBehavior: Clip.antiAlias,
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        InkWell(
          onTap: () => setState(() =>
              open ? _collapsed.add(f.path) : _collapsed.remove(f.path)),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(10, 8, 4, 8),
            child: Row(children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: statusColor.withValues(alpha: .18),
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Text(f.status,
                    style: TextStyle(
                        color: statusColor, fontWeight: FontWeight.w700, fontSize: 12)),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  f.oldPath == null ? f.path : '${f.oldPath} → ${f.path}',
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontFamily: 'monospace', fontSize: 13),
                ),
              ),
              Text('+${f.additions} ',
                  style: const TextStyle(color: GajalaColors.ok, fontSize: 12)),
              Text('−${f.deletions}',
                  style: const TextStyle(color: GajalaColors.danger, fontSize: 12)),
              IconButton(
                tooltip: 'Copy patch',
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.copy_all_outlined, size: 18),
                onPressed: () => _copy(f.patch, 'Patch'),
              ),
              Icon(open ? Icons.expand_less : Icons.expand_more, size: 20),
            ]),
          ),
        ),
        if (open && f.binary)
          _note(context, 'Binary file — no text diff.'),
        if (open && !f.binary)
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              for (final h in f.hunks) ...[
                _hunkHeader(context, h.header),
                for (final l in h.lines) _line(context, f, l, index++),
              ],
            ]),
          ),
        if (open && f.truncated)
          _note(context, 'Large change — showing the first part only. '
              'Copy patch for what is shown.'),
        if (open && !f.binary && lines.isEmpty && !f.truncated)
          _note(context, 'No content changes (mode or rename only).'),
      ]),
    );
  }

  Widget _note(BuildContext context, String text) => Padding(
    padding: const EdgeInsets.fromLTRB(12, 4, 12, 10),
    child: Text(text, style: TextStyle(fontSize: 12, color: context.pal.textDim)),
  );

  Widget _hunkHeader(BuildContext context, String header) => Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
    color: context.pal.surfaceAlt,
    child: Text(header,
        style: TextStyle(fontFamily: 'monospace', fontSize: 11.5, color: context.pal.textDim)),
  );

  Widget _line(BuildContext context, DiffFile f, DiffLine l, int i) {
    final selected = _isSelected(f, i);
    final bg = selected
        ? GajalaColors.accent.withValues(alpha: .30)
        : l.type == '+'
        ? GajalaColors.ok.withValues(alpha: .13)
        : l.type == '-'
        ? GajalaColors.danger.withValues(alpha: .13)
        : Colors.transparent;
    String num(int? n) => (n == null ? '' : '$n').padLeft(4);
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onLongPress: () => _startSelection(f, i),
      onTap: () => _tapLine(f, i),
      child: Container(
        color: bg,
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 1),
        child: Text(
          '${_numbers ? '${num(l.oldNo)} ${num(l.newNo)}  ' : ''}${l.type} ${l.text}',
          softWrap: false,
          style: TextStyle(fontFamily: 'monospace', fontSize: 12.5, color: context.pal.text),
        ),
      ),
    );
  }
}
