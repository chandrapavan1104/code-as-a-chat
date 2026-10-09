import 'package:flutter/material.dart';
import '../core/api.dart';
import '../core/theme.dart';
import '../widgets/chat_content.dart';

/// Browse files in the selected project using the authenticated library API.
class FilesScreen extends StatefulWidget {
  final dynamic api;
  final String? project;

  const FilesScreen({super.key, required this.api, this.project});

  @override
  State<FilesScreen> createState() => _FilesScreenState();
}

class _FilesScreenState extends State<FilesScreen> {
  String _path = '';
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  bool _truncated = false;
  String? _error;
  String? _sharing;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load([String? path]) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final dynamic api = widget.api;
      final result = Map<String, dynamic>.from(
        await api.libraryFiles(path ?? _path, widget.project),
      );
      final entries = result['items'] ?? result['files'] ?? const [];
      if (!mounted) return;
      setState(() {
        _truncated = result['truncated'] == true;
        _path = (result['current_path'] ?? result['path'] ?? path ?? _path)
            .toString();
        _items = List<Map<String, dynamic>>.from(
          (entries as List).map((entry) => Map<String, dynamic>.from(entry)),
        );
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = 'Could not load this folder: $e';
        _loading = false;
      });
    }
  }

  String _join(String parent, String child) {
    if (parent.isEmpty || parent == '.') return child;
    return '${parent.replaceFirst(RegExp(r'/+$'), '')}/$child';
  }

  String _parent(String path) {
    final clean = path.replaceFirst(RegExp(r'/+$'), '');
    final slash = clean.lastIndexOf('/');
    return slash <= 0 ? '' : clean.substring(0, slash);
  }

  bool _isDirectory(Map<String, dynamic> item) =>
      item['is_dir'] == true ||
      item['type'] == 'directory' ||
      item['kind'] == 'directory';

  String _itemPath(Map<String, dynamic> item) =>
      (item['path'] ?? _join(_path, (item['name'] ?? '').toString()))
          .toString();

  Future<void> _share(Map<String, dynamic> item) async {
    final path = _itemPath(item);
    setState(() => _sharing = path);
    try {
      final dynamic api = widget.api;
      final result = await api.shareLibraryFile(path, widget.project);
      if (!mounted) return;
      final map = result is Map ? Map<String, dynamic>.from(result) : {};
      final display = (map['shared_path'] ?? map['path'] ?? path).toString();
      setState(() => _sharing = null);
      await showDialog<void>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Ready to open on phone'),
          content: FileAttachmentCard(
            path: display,
            api: widget.api is GajalaApi ? widget.api as GajalaApi : null,
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Done'),
            ),
          ],
        ),
      );
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Could not share file: $e')));
      }
    } finally {
      if (mounted) setState(() => _sharing = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Files'),
        actions: [
          IconButton(
            tooltip: 'Refresh',
            onPressed: _loading ? null : () => _load(),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          _breadcrumb(context),
          if (_truncated)
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              child: Text('Showing the first 500 items in this folder.'),
            ),
          if (_loading) const LinearProgressIndicator(minHeight: 2),
          if (_error != null)
            Expanded(
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.folder_off_outlined, size: 40),
                      const SizedBox(height: 12),
                      Text(_error!, textAlign: TextAlign.center),
                      TextButton.icon(
                        onPressed: () => _load(),
                        icon: const Icon(Icons.refresh),
                        label: const Text('Retry'),
                      ),
                    ],
                  ),
                ),
              ),
            )
          else if (!_loading && _items.isEmpty)
            const Expanded(child: Center(child: Text('This folder is empty.')))
          else
            Expanded(
              child: RefreshIndicator(
                onRefresh: () => _load(),
                child: ListView.separated(
                  physics: const AlwaysScrollableScrollPhysics(),
                  itemCount: _items.length,
                  separatorBuilder: (_, _) => const Divider(height: 1),
                  itemBuilder: (context, index) {
                    final item = _items[index];
                    final directory = _isDirectory(item);
                    final name = (item['name'] ?? item['path'] ?? 'File')
                        .toString();
                    final path = _itemPath(item);
                    if (directory) {
                      return ListTile(
                        leading: const Icon(Icons.folder_outlined),
                        title: Text(
                          name,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                        subtitle: _subtitle(item),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => _load(path),
                      );
                    }
                    return Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 8),
                      child: Row(
                        children: [
                          Expanded(
                            child: ListTile(
                              leading: const Icon(
                                Icons.insert_drive_file_outlined,
                              ),
                              title: Text(
                                name,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                              ),
                              subtitle: item['size'] == null
                                  ? null
                                  : Text('${item['size']} bytes'),
                              onTap: _sharing == path
                                  ? null
                                  : () => _share(item),
                            ),
                          ),
                          IconButton(
                            tooltip: 'Share file',
                            onPressed: _sharing == path
                                ? null
                                : () => _share(item),
                            icon: _sharing == path
                                ? const SizedBox.square(
                                    dimension: 18,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                    ),
                                  )
                                : const Icon(Icons.ios_share_outlined),
                          ),
                        ],
                      ),
                    );
                  },
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _breadcrumb(BuildContext context) {
    final parts = _path.split('/').where((part) => part.isNotEmpty).toList();
    return Material(
      color: context.pal.surface,
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            IconButton(
              tooltip: 'Project root',
              onPressed: _path.isEmpty || _loading ? null : () => _load(''),
              icon: const Icon(Icons.home_outlined),
            ),
            if (_path.isNotEmpty)
              IconButton(
                tooltip: 'Parent folder',
                onPressed: _loading ? null : () => _load(_parent(_path)),
                icon: const Icon(Icons.arrow_upward),
              ),
            for (var i = 0; i < parts.length; i++) ...[
              const Icon(Icons.chevron_right, size: 18),
              TextButton(
                onPressed: _loading
                    ? null
                    : () => _load(
                        '${_path.startsWith('/') ? '/' : ''}${parts.take(i + 1).join('/')}',
                      ),
                child: Text(parts[i]),
              ),
            ],
            if (parts.isEmpty)
              const Padding(
                padding: EdgeInsets.only(right: 12),
                child: Text('Project root'),
              ),
          ],
        ),
      ),
    );
  }

  Widget? _subtitle(Map<String, dynamic> item) {
    final details = <String>[];
    final count = item['item_count'] ?? item['count'];
    if (count != null) details.add('$count items');
    final modified = item['modified'] ?? item['modified_at'];
    if (modified != null) details.add(modified.toString());
    return details.isEmpty ? null : Text(details.join(' · '));
  }
}
