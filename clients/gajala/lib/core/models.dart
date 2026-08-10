// Data models mirroring the server's JSON.

class Skill {
  final String name, command, description, helpLine;
  final bool exposeToAgent, enabled;
  Skill({required this.name, required this.command, required this.description,
         required this.helpLine, required this.exposeToAgent, required this.enabled});
  factory Skill.fromJson(Map<String, dynamic> j) => Skill(
        name: j['name'] ?? '',
        command: j['command'] ?? j['name'] ?? '',
        description: j['description'] ?? '',
        helpLine: j['help_line'] ?? j['description'] ?? '',
        exposeToAgent: j['expose_to_agent'] ?? true,
        enabled: j['enabled'] ?? true,
      );
}

class Note {
  final int id;
  final String? project;
  final String kind, title, body, status;
  final String? tags;
  Note({required this.id, this.project, required this.kind, required this.title,
        required this.body, required this.status, this.tags});
  factory Note.fromJson(Map<String, dynamic> j) => Note(
        id: j['id'], project: j['project'], kind: j['kind'] ?? 'note',
        title: j['title'] ?? '', body: j['body'] ?? '',
        status: j['status'] ?? 'open', tags: j['tags'],
      );
}

class SystemStats {
  final double cpu;
  final num ramPct, diskPct;
  final num ramUsed, ramTotal, diskUsed, diskTotal;
  final int? batteryPct;
  final bool? charging;
  final List<Map<String, dynamic>> topProcs;
  SystemStats({required this.cpu, required this.ramPct, required this.diskPct,
    required this.ramUsed, required this.ramTotal, required this.diskUsed,
    required this.diskTotal, this.batteryPct, this.charging, required this.topProcs});
  factory SystemStats.fromJson(Map<String, dynamic> j) {
    final bat = j['battery'];
    return SystemStats(
      cpu: (j['cpu_percent'] ?? 0).toDouble(),
      ramPct: j['ram']['percent'], ramUsed: j['ram']['used_gb'], ramTotal: j['ram']['total_gb'],
      diskPct: j['disk']['percent'], diskUsed: j['disk']['used_gb'], diskTotal: j['disk']['total_gb'],
      batteryPct: bat == null ? null : bat['percent'],
      charging: bat == null ? null : bat['charging'],
      topProcs: List<Map<String, dynamic>>.from(j['top_processes'] ?? []),
    );
  }
}

/// A Night Shift queue item (Tasks tab).
class QueueJob {
  final int id;
  final String project, projectName, task, tag, engine, status;
  final String? branch, summary;
  final List<String> filesChanged;
  final int tokensTotal;
  final double? createdAt, endedAt;
  QueueJob({
    required this.id, required this.project, required this.projectName,
    required this.task, required this.tag, required this.engine,
    required this.status, this.branch, this.summary,
    required this.filesChanged, required this.tokensTotal,
    this.createdAt, this.endedAt,
  });
  factory QueueJob.fromJson(Map<String, dynamic> j) => QueueJob(
        id: j['id'], project: j['project'] ?? '',
        projectName: j['project_name'] ?? '', task: j['task'] ?? '',
        tag: j['tag'] ?? 'auto', engine: j['engine'] ?? 'auto',
        status: j['status'] ?? 'queued', branch: j['branch'],
        summary: j['summary'],
        filesChanged: List<String>.from(j['files_changed'] ?? const []),
        tokensTotal: j['tokens_total'] ?? 0,
        createdAt: (j['created_at'] as num?)?.toDouble(),
        endedAt: (j['ended_at'] as num?)?.toDouble(),
      );
}

/// An inbox notification (Alerts tab).
class AppNotification {
  final int id;
  final String type, title, body, status;
  final bool needsResponse;
  final String? response;
  final int? refId;
  final double? createdAt;
  AppNotification({
    required this.id, required this.type, required this.title,
    required this.body, required this.status, required this.needsResponse,
    this.response, this.refId, this.createdAt,
  });
  bool get isUnread => status == 'unread';
  factory AppNotification.fromJson(Map<String, dynamic> j) => AppNotification(
        id: j['id'], type: j['type'] ?? '', title: j['title'] ?? '',
        body: j['body'] ?? '', status: j['status'] ?? 'unread',
        needsResponse: j['needs_response'] ?? false,
        response: j['response'], refId: j['ref_id'],
        createdAt: (j['created_at'] as num?)?.toDouble(),
      );
}

class ChatMessage {
  final String role; // user | bot | error | status
  String text;       // mutable so a live 'status' bubble can accumulate steps
  final String? localImage;         // absolute local file path (image the user sent)
  final List<String> remoteImages;  // full /api/file URLs (images the agent sent back)
  final String? moveTo;             // dir to offer moving this question to (confirm-to-move)
  ChatMessage(this.role, this.text,
      {this.localImage, List<String>? remoteImages, this.moveTo})
      : remoteImages = remoteImages ?? const [];
}
