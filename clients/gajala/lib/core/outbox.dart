// Messages the server has not yet acknowledged, kept on disk.
//
// A message is written here BEFORE any network attempt and removed as soon as
// the server sends its first byte back (it always sends one immediately), so a
// message is never lost to a dropped Tailscale link, a connect timeout, or the
// app being killed mid-send. Replays reuse the original request id; the server
// deduplicates on it, so a message that did arrive is never executed twice.

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

@immutable
class OutboxEntry {
  final String requestId;
  final String command;
  final String sid;
  final String text;
  final String? imagePath; // copied into app storage, so it survives restarts
  final String? project;
  final String? continuationTaskId;
  final int? replyToMessageId;
  final String? replyToContent;
  final String? replyToRole;
  final DateTime createdAt;

  const OutboxEntry({
    required this.requestId,
    required this.command,
    required this.sid,
    required this.text,
    required this.createdAt,
    this.imagePath,
    this.project,
    this.continuationTaskId,
    this.replyToMessageId,
    this.replyToContent,
    this.replyToRole,
  });

  Map<String, dynamic> toJson() => {
    'request_id': requestId,
    'command': command,
    'sid': sid,
    'text': text,
    'image_path': imagePath,
    'project': project,
    'continuation_task_id': continuationTaskId,
    'reply_to_message_id': replyToMessageId,
    'reply_to_content': replyToContent,
    'reply_to_role': replyToRole,
    'created_at': createdAt.toUtc().toIso8601String(),
  };

  factory OutboxEntry.fromJson(Map<String, dynamic> j) => OutboxEntry(
    requestId: j['request_id'] as String,
    command: j['command'] as String,
    sid: j['sid'] as String,
    text: j['text'] as String? ?? '',
    imagePath: j['image_path'] as String?,
    project: j['project'] as String?,
    continuationTaskId: j['continuation_task_id'] as String?,
    replyToMessageId: (j['reply_to_message_id'] as num?)?.toInt(),
    replyToContent: j['reply_to_content'] as String?,
    replyToRole: j['reply_to_role'] as String?,
    createdAt: DateTime.parse(j['created_at'] as String),
  );
}

class OutboxFull implements Exception {
  @override
  String toString() =>
      'Too many unsent messages are waiting. Reconnect to the Mac first.';
}

class Outbox {
  static const maxEntries = 50;
  static const maxAge = Duration(hours: 48);

  final Future<Directory> Function() _dir;
  final DateTime Function() _now;
  Future<void> _lock = Future.value();

  Outbox({Future<Directory> Function()? dir, DateTime Function()? now})
    : _dir = dir ?? getApplicationSupportDirectory,
      _now = now ?? DateTime.now;

  static final instance = Outbox();

  Future<Directory> _root() async {
    final d = Directory('${(await _dir()).path}/outbox');
    await d.create(recursive: true);
    return d;
  }

  /// Serialize all file access; two chat threads may send at the same moment.
  Future<T> _locked<T>(Future<T> Function() body) {
    final run = _lock.then((_) => body());
    _lock = run.then((_) {}, onError: (_) {});
    return run;
  }

  Future<List<OutboxEntry>> _read() async {
    final f = File('${(await _root()).path}/outbox.json');
    if (!await f.exists()) return [];
    try {
      final raw = jsonDecode(await f.readAsString()) as List;
      return raw
          .map((e) => OutboxEntry.fromJson(Map<String, dynamic>.from(e)))
          .toList();
    } catch (_) {
      return []; // a corrupt file must not block sending forever
    }
  }

  Future<void> _write(List<OutboxEntry> entries) async {
    final root = await _root();
    final tmp = File('${root.path}/outbox.json.tmp');
    await tmp.writeAsString(jsonEncode([for (final e in entries) e.toJson()]));
    await tmp.rename('${root.path}/outbox.json'); // atomic replace
  }

  Future<void> _deleteImage(OutboxEntry e) async {
    final p = e.imagePath;
    if (p == null) return;
    final root = await _root();
    if (p.startsWith(root.path)) {
      try {
        await File(p).parent.delete(recursive: true);
      } catch (_) {}
    }
  }

  /// Persist a message before sending it. The attached image is copied into
  /// app storage because the picker's cache file may vanish after a restart.
  Future<OutboxEntry> add(OutboxEntry entry) => _locked(() async {
    final entries = await _read();
    if (entries.length >= maxEntries) throw OutboxFull();
    var e = entry;
    final img = entry.imagePath;
    if (img != null && await File(img).exists()) {
      // Own folder per message, original file name kept: the name is what
      // the Mac sees when the photo is uploaded.
      final folder = Directory(
        '${(await _root()).path}/${entry.requestId.hashCode.toUnsigned(32)}',
      );
      await folder.create(recursive: true);
      final copy = '${folder.path}/${img.split('/').last}';
      await File(img).copy(copy);
      e = OutboxEntry(
        requestId: entry.requestId,
        command: entry.command,
        sid: entry.sid,
        text: entry.text,
        imagePath: copy,
        project: entry.project,
        continuationTaskId: entry.continuationTaskId,
        createdAt: entry.createdAt,
      );
    }
    await _write([...entries, e]);
    return e;
  });

  /// The server acknowledged it (or rejected it outright): stop retrying.
  Future<void> remove(String requestId) => _locked(() async {
    final entries = await _read();
    final keep = <OutboxEntry>[];
    for (final e in entries) {
      if (e.requestId == requestId) {
        await _deleteImage(e);
      } else {
        keep.add(e);
      }
    }
    if (keep.length != entries.length) await _write(keep);
  });

  /// Pending messages, oldest first. Entries older than [maxAge] are dropped
  /// and returned separately so the chat can say so instead of going silent.
  Future<({List<OutboxEntry> pending, List<OutboxEntry> expired})> load() =>
      _locked(() async {
        final entries = await _read();
        final cutoff = _now().subtract(maxAge);
        final pending = <OutboxEntry>[], expired = <OutboxEntry>[];
        for (final e in entries) {
          (e.createdAt.isBefore(cutoff) ? expired : pending).add(e);
        }
        if (expired.isNotEmpty) {
          for (final e in expired) {
            await _deleteImage(e);
          }
          await _write(pending);
        }
        pending.sort((a, b) => a.createdAt.compareTo(b.createdAt));
        return (pending: pending, expired: expired);
      });
}
