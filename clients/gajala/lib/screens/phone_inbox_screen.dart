import 'dart:async';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/api.dart';
import 'chat_screen.dart';

const phoneImportChannel = MethodChannel('gajala/phone_import');

class PhoneInboxScreen extends StatefulWidget {
  final GajalaApi api;
  final List<Map<Object?, Object?>> shared;

  const PhoneInboxScreen({
    super.key,
    required this.api,
    this.shared = const [],
  });

  @override
  State<PhoneInboxScreen> createState() => _PhoneInboxScreenState();
}

class _Transfer {
  final String id;
  final String name;
  final int? size;
  String? error;
  String? path;
  double? progress;

  _Transfer(Map<Object?, Object?> file)
    : id = file['id'] as String,
      name = file['name'] as String,
      size = file['size'] as int?;
}

class _PhoneInboxScreenState extends State<PhoneInboxScreen> {
  final _selected = <_Transfer>[];
  List<Map<String, dynamic>> _receipts = const [];
  CancelToken? _cancel;
  _Transfer? _active;
  String? _error;
  bool _loading = true;
  bool _picking = false;

  @override
  void initState() {
    super.initState();
    _selected.addAll(widget.shared.map(_Transfer.new));
    _load();
  }

  @override
  void dispose() {
    _cancel?.cancel();
    unawaited(_release(_selected.map((file) => file.id).toList()));
    super.dispose();
  }

  Future<void> _release(List<String> ids) async {
    try {
      if (ids.isNotEmpty) await phoneImportChannel.invokeMethod('release', ids);
    } on PlatformException {
      // The activity may have been recreated. The cache is also age-limited.
    } on MissingPluginException {
      // Android only.
    }
  }

  Future<void> _load() async {
    try {
      final receipts = await widget.api.phoneInbox();
      if (mounted) {
        setState(() {
          _receipts = receipts;
          _error = null;
        });
      }
    } catch (error) {
      if (mounted) setState(() => _error = friendlyError(error));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _pick() async {
    setState(() => _picking = true);
    try {
      final items =
          await phoneImportChannel.invokeListMethod<Map<Object?, Object?>>(
            'pick',
          ) ??
          const [];
      if (!mounted || items.isEmpty) return;
      final available = (10 - _selected.length).clamp(0, 10);
      setState(
        () => _selected.addAll(items.take(available).map(_Transfer.new)),
      );
      unawaited(
        _release(items.skip(available).map((i) => i['id'] as String).toList()),
      );
    } on PlatformException catch (error) {
      if (mounted)
        setState(() => _error = error.message ?? 'Could not open Files.');
    } finally {
      if (mounted) setState(() => _picking = false);
    }
  }

  bool _tooBig(_Transfer file) => (file.size ?? 0) > 200 * 1024 * 1024;

  Future<void> _send() async {
    if (_cancel != null) return;
    final cancel = CancelToken();
    setState(() => _cancel = cancel);
    for (final item in _selected) {
      if (!mounted || cancel.isCancelled) break;
      if (item.path != null || _tooBig(item)) continue;
      setState(() {
        _active = item;
        item.error = null;
        item.progress = null;
      });
      try {
        final path = await phoneImportChannel.invokeMethod<String>(
          'stage',
          item.id,
        );
        if (path == null) throw StateError('Could not prepare the file.');
        if (!mounted || cancel.isCancelled) break;
        final receipt = await widget.api.sendPhoneFile(
          id: item.id,
          name: item.name,
          file: File(path),
          cancel: cancel,
          progress: (bytes, total) {
            if (mounted && total > 0 && !cancel.isCancelled) {
              setState(() => item.progress = bytes / total);
            }
          },
        );
        if (!mounted) break;
        setState(() => item.path = receipt['path'] as String);
        unawaited(_release([item.id]));
      } on PlatformException catch (error) {
        if (mounted)
          setState(
            () => item.error = error.message ?? 'Could not read the file.',
          );
      } catch (error) {
        if (mounted) {
          setState(
            () => item.error = cancel.isCancelled
                ? 'Cancelled. Retry to check whether the Mac received this file.'
                : friendlyError(error),
          );
        }
      }
    }
    if (mounted) {
      setState(() {
        _active = null;
        _cancel = null;
      });
      await _load();
    }
  }

  Future<void> _attach(String id) async {
    try {
      final path = await widget.api.attachPhoneFile(id);
      if (!mounted) return;
      await Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => ChatScreen(attachmentPath: path),
        ),
      );
    } catch (error) {
      if (mounted) setState(() => _error = friendlyError(error));
    }
  }

  String _size(int? size) {
    if (size == null) return 'Size unavailable';
    if (size < 1024) return '$size bytes';
    if (size < 1024 * 1024) return '${(size / 1024).toStringAsFixed(1)} KiB';
    return '${(size / (1024 * 1024)).toStringAsFixed(1)} MiB';
  }

  @override
  Widget build(BuildContext context) {
    final canSend = _selected.any(
      (item) => item.path == null && !_tooBig(item),
    );
    final working = _cancel != null;
    return PopScope(
      canPop: !working,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('Phone inbox'),
          actions: [
            IconButton(
              tooltip: 'Refresh',
              onPressed: _loading || working ? null : _load,
              icon: const Icon(Icons.refresh),
            ),
          ],
        ),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            OutlinedButton.icon(
              onPressed: working || _picking || _selected.length == 10
                  ? null
                  : _pick,
              icon: const Icon(Icons.add),
              label: const Text('Choose files'),
            ),
            for (final item in _selected)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: Icon(
                  item.path == null
                      ? Icons.insert_drive_file_outlined
                      : Icons.check,
                ),
                title: Text(item.name),
                subtitle: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      item.error ??
                          (_tooBig(item)
                              ? 'This file exceeds 200 MiB'
                              : item.path != null
                              ? 'Received by Mac'
                              : item == _active
                              ? item.progress == null
                                    ? 'Preparing file…'
                                    : 'Sending…'
                              : _size(item.size)),
                    ),
                    if (item == _active)
                      LinearProgressIndicator(value: item.progress),
                  ],
                ),
                trailing: IconButton(
                  tooltip: 'Remove file',
                  onPressed: working
                      ? null
                      : () {
                          setState(() => _selected.remove(item));
                          unawaited(_release([item.id]));
                        },
                  icon: const Icon(Icons.close),
                ),
              ),
            if (working)
              TextButton(
                onPressed: () => _cancel?.cancel(),
                child: const Text('Cancel transfer'),
              )
            else if (canSend)
              FilledButton(
                onPressed: _send,
                child: Text(
                  _selected.any((item) => item.error != null)
                      ? 'Retry'
                      : 'Send to Mac',
                ),
              ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 8),
                child: Text(
                  _error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
            const Divider(),
            const ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text('Received by Mac'),
            ),
            if (_loading) const LinearProgressIndicator(),
            if (_receipts.isEmpty && !_loading && _error == null)
              const Text('Files you send will appear here.'),
            for (final receipt in _receipts)
              ListTile(
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.insert_drive_file_outlined),
                title: Text(receipt['name'] as String),
                subtitle: Text(_size(receipt['size'] as int?)),
                trailing: PopupMenuButton<String>(
                  tooltip: 'File actions',
                  onSelected: (value) async {
                    if (value == 'attach') {
                      await _attach(receipt['id'] as String);
                    } else {
                      await Clipboard.setData(
                        ClipboardData(text: receipt['path'] as String),
                      );
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(content: Text('Mac path copied')),
                        );
                      }
                    }
                  },
                  itemBuilder: (_) => const [
                    PopupMenuItem(value: 'copy', child: Text('Copy Mac path')),
                    PopupMenuItem(
                      value: 'attach',
                      child: Text('Attach to chat'),
                    ),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}
