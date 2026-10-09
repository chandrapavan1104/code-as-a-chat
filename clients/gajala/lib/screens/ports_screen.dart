import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api.dart';
import '../core/ports_api.dart';
import '../core/state.dart';
import '../core/theme.dart';

class PortsScreen extends ConsumerStatefulWidget {
  const PortsScreen({super.key});
  @override
  ConsumerState<PortsScreen> createState() => _PortsScreenState();
}

class _PortsScreenState extends ConsumerState<PortsScreen> {
  final _filter = TextEditingController();
  late Future<Map<String, dynamic>> _ports;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _ports = _load();
  }

  @override
  void dispose() {
    _filter.dispose();
    super.dispose();
  }

  Future<Map<String, dynamic>> _load() async {
    final api = ref.read(apiProvider);
    if (api == null) throw Exception('not connected');
    return api.listeningPorts();
  }

  Future<void> _refresh() async {
    setState(() => _ports = _load());
    try {
      await _ports;
    } catch (_) {}
  }

  Future<void> _showDetails(int port) async {
    try {
      final api = ref.read(apiProvider);
      if (api == null) throw Exception('not connected');
      final detail = await api.listeningPort(port);
      if (!mounted) return;
      final processes = (detail['processes'] as List).cast<Map>();
      await showModalBottomSheet<void>(
        context: context,
        showDragHandle: true,
        builder: (context) => SafeArea(
          child: ListView(
            padding: const EdgeInsets.all(20),
            shrinkWrap: true,
            children: [
              Text('Port $port', style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 8),
              ...processes.map(
                (p) => _ProcessDetails(process: Map<String, dynamic>.from(p)),
              ),
            ],
          ),
        ),
      );
    } catch (e) {
      if (mounted) _snack(friendlyError(e));
    }
  }

  Future<void> _terminate(Map<String, dynamic> process) async {
    final pid = process['pid'];
    final port = process['port'];
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Terminate this process?'),
        content: Text(
          '${process['command']} (PID $pid) is listening on port $port. '
          'Gajala will send SIGTERM after checking that the process and listener have not changed.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton.tonal(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Terminate'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    setState(() => _busy = true);
    try {
      final api = ref.read(apiProvider);
      if (api == null) throw Exception('not connected');
      await api.terminateListener(port as int, process);
      if (mounted) _snack('SIGTERM sent to PID $pid');
      await _refresh();
    } catch (e) {
      if (mounted) _snack(friendlyError(e));
      await _refresh();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _snack(String message) => ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Listening ports'),
      actions: [
        IconButton(
          tooltip: 'Refresh',
          onPressed: _busy ? null : _refresh,
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    body: FutureBuilder<Map<String, dynamic>>(
      future: _ports,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting &&
            !snapshot.hasData) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snapshot.hasError) {
          return Center(
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    friendlyError(snapshot.error!),
                    textAlign: TextAlign.center,
                  ),
                  const SizedBox(height: 12),
                  FilledButton.tonal(
                    onPressed: _refresh,
                    child: const Text('Retry'),
                  ),
                ],
              ),
            ),
          );
        }
        final rows = (snapshot.data?['ports'] as List? ?? const [])
            .map((e) => Map<String, dynamic>.from(e as Map))
            .toList();
        final available = snapshot.data?['available'] == true;
        final needle = _filter.text.trim().toLowerCase();
        final shown = rows
            .where(
              (p) =>
                  needle.isEmpty ||
                  '${p['port']} ${p['pid']} ${p['command']} ${p['user']} ${p['address']}'
                      .toLowerCase()
                      .contains(needle),
            )
            .toList();
        return RefreshIndicator(
          onRefresh: _refresh,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
            children: [
              TextField(
                controller: _filter,
                onChanged: (_) => setState(() {}),
                decoration: const InputDecoration(
                  prefixIcon: Icon(Icons.search),
                  hintText: 'Filter port, process, PID, or address',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              if (!available)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 40),
                  child: Column(
                    children: [
                      Icon(Icons.error_outline, size: 36),
                      SizedBox(height: 8),
                      Text(
                        'Port inspection is unavailable on the Mac (lsof is missing).',
                      ),
                    ],
                  ),
                )
              else if (rows.isEmpty)
                const Padding(
                  padding: EdgeInsets.symmetric(vertical: 40),
                  child: Column(
                    children: [
                      Icon(Icons.check_circle_outline, size: 36),
                      SizedBox(height: 8),
                      Text('No listening TCP ports found'),
                    ],
                  ),
                )
              else if (shown.isEmpty)
                const Padding(
                  padding: EdgeInsets.all(24),
                  child: Center(child: Text('No matches')),
                )
              else
                ...shown.map(
                  (p) => _PortCard(
                    process: p,
                    onDetails: () => _showDetails(p['port'] as int),
                    onTerminate: p['can_terminate'] != true || _busy
                        ? null
                        : () => _terminate(p),
                  ),
                ),
            ],
          ),
        );
      },
    ),
  );
}

class _PortCard extends StatelessWidget {
  const _PortCard({
    required this.process,
    required this.onDetails,
    this.onTerminate,
  });
  final Map<String, dynamic> process;
  final VoidCallback onDetails;
  final VoidCallback? onTerminate;

  @override
  Widget build(BuildContext context) => Card(
    margin: const EdgeInsets.only(bottom: 8),
    child: ListTile(
      onTap: onDetails,
      leading: const Icon(Icons.settings_ethernet),
      title: Text(
        ':${process['port']}  ·  ${process['command']}',
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      subtitle: Text(
        'PID ${process['pid']} · ${process['user']} · ${process['address']}',
        maxLines: 2,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: onTerminate == null
          ? const Icon(Icons.shield_outlined)
          : IconButton(
              tooltip: 'Terminate process',
              icon: const Icon(Icons.stop_circle_outlined),
              color: GajalaColors.danger,
              onPressed: onTerminate,
            ),
    ),
  );
}

class _ProcessDetails extends StatelessWidget {
  const _ProcessDetails({required this.process});
  final Map<String, dynamic> process;
  @override
  Widget build(BuildContext context) {
    final started = process['started_at'];
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _DetailLine('PID', '${process['pid']}'),
            _DetailLine('Command', '${process['command']}'),
            _DetailLine('User', '${process['user']}'),
            _DetailLine('Address', '${process['address']}'),
            if (started != null)
              _DetailLine(
                'Started',
                DateTime.fromMillisecondsSinceEpoch(
                  ((started as num).toDouble() * 1000).round(),
                ).toLocal().toString(),
              ),
            if (process['cmdline'] != null)
              _DetailLine('Command line', '${process['cmdline']}'),
            if (process['protected'] == true)
              const Text('This is the API server and is protected.'),
          ],
        ),
      ),
    );
  }
}

class _DetailLine extends StatelessWidget {
  const _DetailLine(this.label, this.value);
  final String label, value;
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 4),
    child: Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          width: 100,
          child: Text(label, style: TextStyle(color: context.pal.textDim)),
        ),
        Expanded(child: SelectableText(value)),
      ],
    ),
  );
}
