import 'package:flutter/material.dart';
import '../core/storage.dart';
import '../core/theme.dart';
import 'chat_screen.dart';

/// Inspect saved CLI sessions and continue one in its pinned project context.
class SessionsScreen extends StatefulWidget {
  final dynamic api;
  final String? project;
  final String? clientId;
  final ValueChanged<Map<String, dynamic>>? onContinue;

  const SessionsScreen({
    super.key,
    required this.api,
    this.project,
    this.clientId,
    this.onContinue,
  });

  @override
  State<SessionsScreen> createState() => _SessionsScreenState();
}

class _SessionsScreenState extends State<SessionsScreen> {
  static const _engines = ['all', 'claude', 'codex', 'gemini'];
  String _engine = 'all';
  List<Map<String, dynamic>> _items = [];
  final Map<String, Map<String, dynamic>> _details = {};
  final Set<String> _detailLoading = {};
  String? _detailError;
  String? _continuing;
  String? _error;
  String? _clientId;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    if (widget.clientId != null) {
      _clientId = widget.clientId;
    } else {
      _loadClientId();
    }
    _load();
  }

  Future<void> _loadClientId() async {
    final id = await Storage.sessionId();
    if (mounted) setState(() => _clientId = id);
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final dynamic api = widget.api;
      final response = Map<String, dynamic>.from(
        await api.librarySessions(
          _engine == 'all' ? null : _engine,
          widget.project,
        ),
      );
      final entries = response['items'] ?? response['sessions'] ?? const [];
      if (!mounted) return;
      setState(() {
        _items = List<Map<String, dynamic>>.from(
          (entries as List).map((item) => Map<String, dynamic>.from(item)),
        );
        _details.clear();
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not load sessions: $e';
        _loading = false;
      });
    }
  }

  String _id(Map<String, dynamic> item) =>
      (item['id'] ?? item['session_id'] ?? '').toString();

  String _engineFor(Map<String, dynamic> item) =>
      (item['engine'] ?? _engine).toString();

  String _projectFor(Map<String, dynamic> item) =>
      (item['project'] ?? item['cwd'] ?? widget.project ?? '').toString();

  Future<void> _loadDetail(Map<String, dynamic> item) async {
    final id = _id(item);
    if (id.isEmpty || _details.containsKey(id) || _detailLoading.contains(id)) {
      return;
    }
    setState(() {
      _detailLoading.add(id);
      _detailError = null;
    });
    try {
      final dynamic api = widget.api;
      final result = Map<String, dynamic>.from(
        await api.librarySession(id, _engineFor(item)),
      );
      if (mounted) setState(() => _details[id] = result);
    } catch (e) {
      if (mounted) setState(() => _detailError = 'Could not load session: $e');
    } finally {
      if (mounted) setState(() => _detailLoading.remove(id));
    }
  }

  Future<void> _continue(Map<String, dynamic> item) async {
    final id = _id(item);
    if (id.isEmpty || _continuing != null) return;
    setState(() => _continuing = id);
    try {
      final dynamic api = widget.api;
      final clientId = _clientId ?? await Storage.sessionId();
      _clientId = clientId;
      final result = Map<String, dynamic>.from(
        await api.continueLibrarySession(
          id,
          _engineFor(item),
          _projectFor(item),
          clientId: clientId,
        ),
      );
      if (!mounted) return;
      final sessionId = (result['session_id'] ?? '').toString();
      final project = (result['project'] ?? _projectFor(item)).toString();
      if (sessionId.isEmpty) {
        throw StateError('The server did not return a conversation session.');
      }
      if (widget.onContinue != null) {
        widget.onContinue!(result);
        return;
      }
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ChatScreen(
            command: _engineFor(item),
            title: '${_engineFor(item)} session',
            sessionId: sessionId,
            project: project.isEmpty ? null : project,
          ),
        ),
      );
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not continue session: $e')),
        );
      }
    } finally {
      if (mounted) setState(() => _continuing = null);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Sessions'),
      actions: [
        IconButton(
          tooltip: 'Refresh',
          onPressed: _loading ? null : _load,
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    body: Column(
      children: [
        SizedBox(
          height: 56,
          child: ListView(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            scrollDirection: Axis.horizontal,
            children: [
              for (final engine in _engines)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text(
                      engine == 'all' ? 'All engines' : _capitalize(engine),
                    ),
                    selected: _engine == engine,
                    onSelected: (_) {
                      if (_engine == engine) return;
                      setState(() => _engine = engine);
                      _load();
                    },
                  ),
                ),
            ],
          ),
        ),
        if (_loading) const LinearProgressIndicator(minHeight: 2),
        if (_error != null)
          Expanded(
            child: Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(_error!, textAlign: TextAlign.center),
                  TextButton.icon(
                    onPressed: _load,
                    icon: const Icon(Icons.refresh),
                    label: const Text('Retry'),
                  ),
                ],
              ),
            ),
          )
        else if (!_loading && _items.isEmpty)
          const Expanded(
            child: Center(child: Text('No saved sessions for this filter.')),
          )
        else
          Expanded(
            child: RefreshIndicator(
              onRefresh: _load,
              child: ListView.builder(
                physics: const AlwaysScrollableScrollPhysics(),
                itemCount: _items.length,
                itemBuilder: (context, index) => _sessionTile(_items[index]),
              ),
            ),
          ),
      ],
    ),
  );

  Widget _sessionTile(Map<String, dynamic> item) {
    final id = _id(item);
    final engine = _engineFor(item);
    final title = (item['title'] ?? item['name'] ?? id).toString();
    final project = _projectFor(item);
    final detail = _details[id];
    final turns = (detail?['turns'] as List?) ?? const [];
    final preview = (detail?['preview'] ?? item['preview'] ?? '').toString();
    final busy = _continuing == id;
    return Card(
      margin: const EdgeInsets.fromLTRB(12, 5, 12, 5),
      child: ExpansionTile(
        key: ValueKey('session-$id'),
        onExpansionChanged: (expanded) {
          if (expanded) _loadDetail(item);
        },
        leading: CircleAvatar(
          backgroundColor: context.pal.surfaceAlt,
          child: Icon(_engineIcon(engine), size: 20),
        ),
        title: Text(title, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(
          [
            if (engine.isNotEmpty) _capitalize(engine),
            if (project.isNotEmpty) project,
          ].join(' · '),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        childrenPadding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
        children: [
          if (_detailLoading.contains(id))
            const Padding(
              padding: EdgeInsets.all(12),
              child: LinearProgressIndicator(),
            )
          else if (_detailError != null)
            Text(_detailError!, style: TextStyle(color: GajalaColors.danger))
          else ...[
            if (preview.isNotEmpty)
              Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(preview),
                ),
              ),
            for (final turn in turns.take(6))
              _turnCard(Map<String, dynamic>.from(turn)),
            if (detail?['truncated'] == true)
              const Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: EdgeInsets.symmetric(vertical: 4),
                  child: Text('Earlier session content is omitted.'),
                ),
              ),
            if (turns.isEmpty &&
                preview.isEmpty &&
                !_detailLoading.contains(id))
              const Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: EdgeInsets.symmetric(vertical: 4),
                  child: Text('No preview available.'),
                ),
              ),
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton.icon(
                onPressed: busy ? null : () => _continue(item),
                icon: busy
                    ? const SizedBox.square(
                        dimension: 16,
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : const Icon(Icons.play_arrow),
                label: const Text('Continue'),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _turnCard(Map<String, dynamic> turn) {
    final role = (turn['role'] ?? 'message').toString();
    final content = (turn['content'] ?? '').toString();
    if (content.isEmpty) return const SizedBox.shrink();
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.symmetric(vertical: 3),
      padding: const EdgeInsets.all(9),
      decoration: BoxDecoration(
        color: context.pal.surfaceAlt,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            _capitalize(role),
            style: Theme.of(context).textTheme.labelSmall,
          ),
          const SizedBox(height: 3),
          Text(content, maxLines: 5, overflow: TextOverflow.ellipsis),
        ],
      ),
    );
  }

  IconData _engineIcon(String engine) => switch (engine.toLowerCase()) {
    'claude' => Icons.auto_awesome,
    'codex' => Icons.terminal,
    'gemini' => Icons.diamond_outlined,
    _ => Icons.history,
  };

  String _capitalize(String text) =>
      text.isEmpty ? text : '${text[0].toUpperCase()}${text.substring(1)}';
}
