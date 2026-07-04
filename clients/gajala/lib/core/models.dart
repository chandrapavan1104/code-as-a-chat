// Data models mirroring the server's JSON.

class Skill {
  final String name, command, description, helpLine;
  final bool exposeToAgent;
  Skill({required this.name, required this.command, required this.description,
         required this.helpLine, required this.exposeToAgent});
  factory Skill.fromJson(Map<String, dynamic> j) => Skill(
        name: j['name'] ?? '',
        command: j['command'] ?? j['name'] ?? '',
        description: j['description'] ?? '',
        helpLine: j['help_line'] ?? j['description'] ?? '',
        exposeToAgent: j['expose_to_agent'] ?? true,
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

class ChatMessage {
  final String role; // user | bot | error | status
  String text;       // mutable so a live 'status' bubble can accumulate steps
  ChatMessage(this.role, this.text);
}
