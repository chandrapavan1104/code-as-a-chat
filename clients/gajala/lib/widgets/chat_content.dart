import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';

import '../core/api.dart';

/// A deliberately small block model so message parsing can be tested without
/// pumping a full chat screen.
sealed class ChatContentBlock {
  const ChatContentBlock();
}

class TextBlock extends ChatContentBlock {
  final String text;
  const TextBlock(this.text);
}

class CodeBlock extends ChatContentBlock {
  final String language;
  final String code;
  const CodeBlock(this.language, this.code);
}

class FileBlock extends ChatContentBlock {
  final String path;
  const FileBlock(this.path);
  String get filename => fileDisplayName(path);
}

final _fencedCode = RegExp(r'```([^\n`]*)\n([\s\S]*?)```');
final _fileMarker = RegExp(r'\[file:\s*(/[^\]\r\n]+?)\s*\]');

String fileDisplayName(String serverPath) {
  final clean = serverPath.trim().replaceAll('\\', '/');
  final parts = clean.split('/').where((part) => part.isNotEmpty).toList();
  return parts.isEmpty ? 'file' : parts.last;
}

/// Parse fenced code and authenticated file markers without interpreting HTML,
/// links, or other model-produced markup as executable content.
List<ChatContentBlock> parseChatContent(String raw) {
  final blocks = <ChatContentBlock>[];
  var cursor = 0;
  for (final code in _fencedCode.allMatches(raw)) {
    if (code.start > cursor) {
      blocks.addAll(_parseFiles(raw.substring(cursor, code.start)));
    }
    final language = code.group(1)?.trim() ?? '';
    blocks.add(CodeBlock(language, code.group(2) ?? ''));
    cursor = code.end;
  }
  if (cursor < raw.length) blocks.addAll(_parseFiles(raw.substring(cursor)));
  return blocks;
}

List<ChatContentBlock> _parseFiles(String text) {
  final blocks = <ChatContentBlock>[];
  var cursor = 0;
  for (final match in _fileMarker.allMatches(text)) {
    if (match.start > cursor)
      blocks.add(TextBlock(text.substring(cursor, match.start)));
    final path = match.group(1)?.trim() ?? '';
    if (path.isNotEmpty && !path.contains('\u0000'))
      blocks.add(FileBlock(path));
    cursor = match.end;
  }
  if (cursor < text.length) blocks.add(TextBlock(text.substring(cursor)));
  return blocks;
}

class ChatContent extends StatelessWidget {
  final String text;
  final GajalaApi? api;
  final TextStyle? style;
  final Color? codeBackground;
  const ChatContent({
    super.key,
    required this.text,
    this.api,
    this.style,
    this.codeBackground,
  });

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      for (final block in parseChatContent(text))
        switch (block) {
          TextBlock(:final text) =>
            text.trim().isEmpty
                ? const SizedBox.shrink()
                : SelectableText(text, style: style),
          CodeBlock(:final language, :final code) => CodeSnippet(
            language: language,
            code: code,
            background:
                codeBackground ??
                Theme.of(context).colorScheme.surfaceContainerHighest,
          ),
          FileBlock(:final path) => FileAttachmentCard(path: path, api: api),
        },
    ],
  );
}

class CodeSnippet extends StatelessWidget {
  final String language;
  final String code;
  final Color background;
  const CodeSnippet({
    super.key,
    required this.language,
    required this.code,
    required this.background,
  });

  @override
  Widget build(BuildContext context) => Container(
    width: double.infinity,
    margin: const EdgeInsets.symmetric(vertical: 5),
    padding: const EdgeInsets.fromLTRB(12, 7, 7, 9),
    decoration: BoxDecoration(
      color: background,
      borderRadius: BorderRadius.circular(9),
    ),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                language.isEmpty ? 'CODE' : language.toUpperCase(),
                style: Theme.of(context).textTheme.labelSmall,
              ),
            ),
            IconButton(
              tooltip: 'Copy code',
              visualDensity: VisualDensity.compact,
              icon: const Icon(Icons.copy, size: 16),
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: code));
                if (context.mounted)
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(
                      content: Text('Code copied'),
                      duration: Duration(seconds: 1),
                    ),
                  );
              },
            ),
          ],
        ),
        SelectableText(
          code,
          style: const TextStyle(fontFamily: 'monospace', height: 1.35),
        ),
      ],
    ),
  );
}

class FileAttachmentCard extends StatelessWidget {
  final String path;
  final GajalaApi? api;
  const FileAttachmentCard({super.key, required this.path, required this.api});

  @override
  Widget build(BuildContext context) {
    final name = fileDisplayName(path);
    return Card(
      margin: const EdgeInsets.symmetric(vertical: 5),
      child: ListTile(
        leading: const Icon(Icons.insert_drive_file_outlined),
        title: Text(name, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(api == null ? 'Connection unavailable' : 'File on Mac'),
        trailing: FilledButton.tonal(
          onPressed: api == null
              ? null
              : () => Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) =>
                        FileAttachmentPreview(path: path, api: api!),
                  ),
                ),
          child: const Text('Open'),
        ),
      ),
    );
  }
}

class FileAttachmentPreview extends StatefulWidget {
  final String path;
  final GajalaApi api;
  const FileAttachmentPreview({
    super.key,
    required this.path,
    required this.api,
  });
  @override
  State<FileAttachmentPreview> createState() => _FileAttachmentPreviewState();
}

class _FileAttachmentPreviewState extends State<FileAttachmentPreview> {
  static const _maxPreviewBytes = 128 * 1024;
  double? progress;
  String? localPath;
  String? error;
  bool opening = false;

  String get filename => fileDisplayName(widget.path);
  String get extension => filename.contains('.')
      ? filename.substring(filename.lastIndexOf('.') + 1).toLowerCase()
      : '';
  bool get image =>
      {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}.contains(extension);
  bool get textLike => {
    'txt',
    'md',
    'json',
    'yaml',
    'yml',
    'csv',
    'log',
    'dart',
    'py',
    'js',
    'ts',
    'tsx',
    'jsx',
    'html',
    'css',
    'xml',
    'sh',
    'toml',
  }.contains(extension);

  Future<void> _download({bool open = false}) async {
    if (opening) return;
    setState(() {
      opening = true;
      progress = 0;
      error = null;
    });
    try {
      final dir = await getApplicationDocumentsDirectory();
      final safe = filename.replaceAll(RegExp(r'[^A-Za-z0-9._-]'), '_');
      final target =
          '${dir.path}/gajala_${DateTime.now().microsecondsSinceEpoch}_$safe';
      await widget.api.downloadFile(widget.api.fileUrl(widget.path), target, (
        value,
      ) {
        if (mounted) setState(() => progress = value);
      });
      if (!mounted) return;
      setState(() {
        localPath = target;
        opening = false;
        progress = 1;
      });
      if (open) await _openLocal();
    } catch (e) {
      if (mounted)
        setState(() {
          error = 'Download failed: $e';
          opening = false;
        });
    }
  }

  Future<void> _openLocal() async {
    final path = localPath;
    if (path == null) return;
    setState(() => opening = true);
    try {
      final result = await OpenFilex.open(path);
      if (!mounted) return;
      setState(() {
        opening = false;
        if (result.type != ResultType.done) {
          error = 'Could not open file: ${result.message}';
        }
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          opening = false;
          error = 'Could not open file: $e';
        });
      }
    }
  }

  Future<String> _readPreview(String path) async {
    final bytes = await File(path)
        .openRead(0, _maxPreviewBytes)
        .fold<List<int>>(<int>[], (all, chunk) => all..addAll(chunk));
    final text = utf8.decode(bytes, allowMalformed: true);
    return bytes.length >= _maxPreviewBytes
        ? '$text\n\n[Preview limited to 128 KB]'
        : text;
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(filename, overflow: TextOverflow.ellipsis)),
    body: Padding(
      padding: const EdgeInsets.all(16),
      child: localPath == null ? _downloadPrompt() : _preview(context),
    ),
  );

  Widget _downloadPrompt() => Center(
    child: Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(
          image ? Icons.image_outlined : Icons.insert_drive_file_outlined,
          size: 48,
        ),
        const SizedBox(height: 10),
        Text('Download $filename to view it', textAlign: TextAlign.center),
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: opening ? null : () => _download(),
          icon: const Icon(Icons.download),
          label: const Text('Download'),
        ),
        if (progress != null) ...[
          const SizedBox(height: 12),
          LinearProgressIndicator(value: progress),
        ],
        if (error != null)
          Padding(
            padding: const EdgeInsets.only(top: 10),
            child: Text(
              error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
          ),
      ],
    ),
  );

  Widget _preview(BuildContext context) {
    final path = localPath!;
    if (image)
      return Column(
        children: [
          Expanded(
            child: InteractiveViewer(
              child: Image.file(
                File(path),
                fit: BoxFit.contain,
                errorBuilder: (_, _, _) =>
                    const Center(child: Text('Could not preview image')),
              ),
            ),
          ),
          _externalOpen(),
        ],
      );
    if (textLike) {
      return FutureBuilder<String>(
        future: _readPreview(path),
        builder: (context, snap) {
          if (snap.hasError)
            return Column(
              children: [
                const Expanded(
                  child: Center(
                    child: Text('Could not preview this text file.'),
                  ),
                ),
                _externalOpen(),
              ],
            );
          if (!snap.hasData)
            return const Center(child: CircularProgressIndicator());
          return Column(
            children: [
              Expanded(
                child: SingleChildScrollView(
                  child: SelectableText(
                    snap.data!,
                    style: const TextStyle(
                      fontFamily: 'monospace',
                      height: 1.35,
                    ),
                  ),
                ),
              ),
              _externalOpen(),
            ],
          );
        },
      );
    }
    return Column(
      children: [
        const Expanded(
          child: Center(
            child: Text('Downloaded and ready to open in another app.'),
          ),
        ),
        _externalOpen(),
      ],
    );
  }

  Widget _externalOpen([String? label]) => Padding(
    padding: const EdgeInsets.only(top: 10),
    child: FilledButton.icon(
      onPressed: opening ? null : _openLocal,
      icon: const Icon(Icons.open_in_new),
      label: Text(label ?? 'Open in another app'),
    ),
  );
}
