// Durable phone-side voice transcripts.
//
// Local voice actions do not pass through /run, so they need their own journal:
// writing a transcript must never re-execute the action it describes. Turns are
// kept after sync so chat history still works when the Mac is unavailable.

import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

typedef VoiceJournalRoot = Future<Directory> Function();

class VoiceJournalMessage {
  final String role;
  final String content;

  const VoiceJournalMessage(this.role, this.content);

  Map<String, dynamic> toJson() => {'role': role, 'content': content};

  factory VoiceJournalMessage.fromJson(Map<String, dynamic> json) =>
      VoiceJournalMessage(
        json['role']?.toString() ?? 'assistant',
        json['content']?.toString() ?? '',
      );
}

class VoiceJournalTurn {
  final String id;
  final String sessionId;
  final DateTime createdAt;
  final List<VoiceJournalMessage> messages;
  final bool completed;
  final bool synced;

  const VoiceJournalTurn({
    required this.id,
    required this.sessionId,
    required this.createdAt,
    this.messages = const [],
    this.completed = false,
    this.synced = false,
  });

  VoiceJournalTurn copyWith({
    List<VoiceJournalMessage>? messages,
    bool? completed,
    bool? synced,
  }) => VoiceJournalTurn(
    id: id,
    sessionId: sessionId,
    createdAt: createdAt,
    messages: messages ?? this.messages,
    completed: completed ?? this.completed,
    synced: synced ?? this.synced,
  );

  Map<String, dynamic> toJson() => {
    'id': id,
    'session_id': sessionId,
    'created_at': createdAt.toUtc().toIso8601String(),
    'messages': messages.map((message) => message.toJson()).toList(),
    'completed': completed,
    'synced': synced,
  };

  factory VoiceJournalTurn.fromJson(Map<String, dynamic> json) =>
      VoiceJournalTurn(
        id: json['id']?.toString() ?? '',
        sessionId: json['session_id']?.toString() ?? '',
        createdAt:
            DateTime.tryParse(json['created_at']?.toString() ?? '') ??
            DateTime.fromMillisecondsSinceEpoch(0, isUtc: true),
        messages: [
          for (final item in (json['messages'] as List? ?? const []))
            if (item is Map)
              VoiceJournalMessage.fromJson(Map<String, dynamic>.from(item)),
        ],
        completed: json['completed'] == true,
        synced: json['synced'] == true,
      );
}

class VoiceJournal {
  static VoiceJournal? _instance;
  static VoiceJournal get instance => _instance ??= VoiceJournal();

  final VoiceJournalRoot _root;
  Future<void> _writes = Future.value();
  final Set<String> _active = {};

  VoiceJournal({VoiceJournalRoot? dir})
    : _root = dir ?? getApplicationDocumentsDirectory;

  Future<File> _file() async =>
      File('${(await _root()).path}/voice_journal.json');

  Future<List<VoiceJournalTurn>> _read() async {
    final file = await _file();
    if (!await file.exists()) return [];
    final raw = jsonDecode(await file.readAsString()) as List;
    return [
      for (final item in raw)
        if (item is Map)
          VoiceJournalTurn.fromJson(Map<String, dynamic>.from(item)),
    ];
  }

  Future<void> _write(List<VoiceJournalTurn> turns) async {
    final file = await _file();
    await file.parent.create(recursive: true);
    final temporary = File('${file.path}.tmp');
    await temporary.writeAsString(jsonEncode(turns), flush: true);
    await temporary.rename(file.path);
  }

  Future<T> _mutate<T>(Future<T> Function(List<VoiceJournalTurn>) change) {
    final result = Completer<T>();
    _writes = _writes
        .catchError((_) {})
        .then((_) async {
          try {
            final turns = await _read();
            final value = await change(turns);
            await _write(turns);
            result.complete(value);
          } catch (error, stack) {
            result.completeError(error, stack);
            rethrow;
          }
        })
        .catchError((_) {});
    return result.future;
  }

  Future<List<VoiceJournalTurn>> load({String? sessionId}) async {
    await _writes;
    final turns = await _read();
    if (sessionId == null) return turns;
    return turns.where((turn) => turn.sessionId == sessionId).toList();
  }

  Future<void> begin(
    String id,
    String sessionId,
    String text, {
    String role = 'user',
  }) {
    _active.add(id);
    return _mutate((turns) async {
        if (turns.any((turn) => turn.id == id)) return;
        turns.add(
          VoiceJournalTurn(
            id: id,
            sessionId: sessionId,
            createdAt: DateTime.now(),
            messages: [VoiceJournalMessage(role, text)],
          ),
        );
      }).catchError((Object error) {
        _active.remove(id);
        throw error;
      });
  }

  Future<void> append(String id, String role, String text) =>
      _mutate((turns) async {
        final index = turns.indexWhere((turn) => turn.id == id);
        if (index < 0) throw StateError('Unknown voice turn $id');
        turns[index] = turns[index].copyWith(
          messages: [...turns[index].messages, VoiceJournalMessage(role, text)],
        );
      });

  Future<void> finish(String id) async {
    await _mutate((turns) async {
      final index = turns.indexWhere((turn) => turn.id == id);
      if (index < 0) throw StateError('Unknown voice turn $id');
      turns[index] = turns[index].copyWith(completed: true);
    });
    _active.remove(id);
  }

  bool isActive(String id) => _active.contains(id);

  Future<void> markSynced(String id) => _mutate((turns) async {
    final index = turns.indexWhere((turn) => turn.id == id);
    if (index < 0) return;
    turns[index] = turns[index].copyWith(synced: true);
  });
}
