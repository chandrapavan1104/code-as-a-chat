// Data models mirroring the server's JSON.

class Skill {
  final String name, command, description, helpLine;
  final bool exposeToAgent, enabled;
  Skill({
    required this.name,
    required this.command,
    required this.description,
    required this.helpLine,
    required this.exposeToAgent,
    required this.enabled,
  });
  factory Skill.fromJson(Map<String, dynamic> j) => Skill(
    name: j['name'] ?? '',
    command: j['command'] ?? j['name'] ?? '',
    description: j['description'] ?? '',
    helpLine: j['help_line'] ?? j['description'] ?? '',
    exposeToAgent: j['expose_to_agent'] ?? true,
    enabled: j['enabled'] ?? true,
  );
}

/// A project directory the agent can work in. Carries the facts needed to tell
/// thirty similarly-named folders apart — a bare name never was enough.
class Project {
  final String name, path, displayPath;
  final bool active, isGit, hasContext;
  final String? branch, remote;
  final double? lastModified;
  Project({
    required this.name,
    required this.path,
    required this.displayPath,
    required this.active,
    required this.isGit,
    required this.hasContext,
    this.branch,
    this.remote,
    this.lastModified,
  });
  factory Project.fromJson(Map<String, dynamic> j) => Project(
    name: j['name'] ?? '',
    path: j['path'] ?? '',
    displayPath: j['display_path'] ?? j['path'] ?? '',
    active: j['active'] == true,
    isGit: j['is_git'] == true,
    hasContext: j['has_context'] == true,
    branch: j['branch'],
    remote: j['remote'],
    lastModified: (j['last_modified'] as num?)?.toDouble(),
  );

  /// "git main · owner/repo" — or an honest "not a git repo".
  String get gitLine {
    if (!isGit) return 'not a git repo';
    final parts = <String>['git ${branch ?? '?'}'];
    if (remote != null && remote!.isNotEmpty) parts.add(remote!);
    return parts.join(' · ');
  }
}

/// Raised when a project switch could not be resolved server-side.
class ProjectSwitchError implements Exception {
  final String message;
  final List<String> suggestions;
  ProjectSwitchError(this.message, this.suggestions);
  @override
  String toString() => suggestions.isEmpty
      ? message
      : '$message Did you mean: ${suggestions.join(', ')}';
}

/// One tool call inside an agent turn.
class RunStep {
  final int idx, durationMs;
  final String tool, args, result, workspace;
  final bool ok, charged;
  RunStep({
    required this.idx,
    required this.tool,
    required this.args,
    required this.result,
    required this.workspace,
    required this.ok,
    required this.charged,
    required this.durationMs,
  });
  factory RunStep.fromJson(Map<String, dynamic> j) => RunStep(
    idx: j['idx'] ?? 0,
    tool: j['tool'] ?? '',
    args: j['args'] ?? '',
    result: j['result'] ?? '',
    workspace: j['workspace'] ?? '',
    ok: (j['ok'] ?? 1) == 1 || j['ok'] == true,
    charged: (j['charged'] ?? 1) == 1 || j['charged'] == true,
    durationMs: j['duration_ms'] ?? 0,
  );

  /// Just the folder name — the full path is noise in a step list.
  String get projectName => workspace.isEmpty ? '' : workspace.split('/').last;
  String get firstResultLine {
    final lines = result.split('\n').where((l) => l.trim().isNotEmpty);
    return lines.isEmpty ? '' : lines.first;
  }
}

/// What an agent turn actually did — the record that used to vanish the moment
/// the reply landed.
/// Tokens and cost of one turn. Cost is only what providers report (the
/// Claude CLI does); [costComplete] is false when some calls had no price.
class RunUsage {
  final int inputTokens, outputTokens;
  final double? costUsd;
  final bool costComplete;
  final List<({String source, String model, int input, int? output})> calls;
  const RunUsage({
    required this.inputTokens,
    required this.outputTokens,
    required this.calls,
    this.costUsd,
    this.costComplete = false,
  });

  static RunUsage? fromJson(dynamic j) {
    if (j is! Map) return null;
    return RunUsage(
      inputTokens: (j['input_tokens'] as num?)?.toInt() ?? 0,
      outputTokens: (j['output_tokens'] as num?)?.toInt() ?? 0,
      costUsd: (j['cost_usd'] as num?)?.toDouble(),
      costComplete: j['cost_complete'] == true,
      calls: [
        for (final c in (j['calls'] as List? ?? const []))
          (
            source: '${c['source']}',
            model: '${c['model']}',
            input: (c['input_tokens'] as num?)?.toInt() ?? 0,
            output: (c['output_tokens'] as num?)?.toInt(),
          ),
      ],
    );
  }
}

String compactTokens(int n) => n >= 1000000
    ? '${(n / 1000000).toStringAsFixed(1)}M'
    : n >= 1000
    ? '${(n / 1000).toStringAsFixed(1)}k'
    : '$n';

class RunTrace {
  final String id, workspace, prompt, stopReason, reply;

  /// Which model(s) routed the turn, e.g. "claude" or "qwen:rejected -> claude".
  /// When a turn misbehaves, this is the first thing worth knowing.
  final String brains;
  final int durationMs, chargedSteps;
  final List<RunStep> steps;
  final RunUsage? usage;
  RunTrace({
    this.usage,
    required this.id,
    required this.workspace,
    required this.prompt,
    required this.stopReason,
    required this.reply,
    required this.brains,
    required this.durationMs,
    required this.chargedSteps,
    required this.steps,
  });
  factory RunTrace.fromJson(Map<String, dynamic> j) => RunTrace(
    id: j['id'] ?? '',
    workspace: j['workspace'] ?? '',
    prompt: j['prompt'] ?? '',
    stopReason: j['stop_reason'] ?? '',
    reply: j['reply'] ?? '',
    brains: j['brains'] ?? '',
    durationMs: j['duration_ms'] ?? 0,
    chargedSteps: j['charged_steps'] ?? 0,
    usage: RunUsage.fromJson(j['usage']),
    steps: ((j['steps'] as List?) ?? const [])
        .map((e) => RunStep.fromJson(Map<String, dynamic>.from(e)))
        .toList(),
  );

  String get projectName => workspace.isEmpty ? '' : workspace.split('/').last;

  /// Plain-English reason the turn ended, for the trace footer.
  String get stopLabel => switch (stopReason) {
    'done' => 'Finished',
    'passthrough' => 'Answered directly',
    'final_output' => 'Returned the tool output',
    'step_limit' => 'Ran out of steps',
    'llm_error' => 'The routing model was unreachable',
    'no_action' => 'No action to take',
    'duplicate_stop' => 'Stopped repeating a call that had timed out',
    'error' => 'Failed',
    _ => stopReason,
  };

  bool get hitStepLimit => stopReason == 'step_limit';
}

class Note {
  final int id;
  final String? project;
  final String kind, title, body, status;
  final String? tags;
  final double? closedAt;
  final String? closeReason;
  final int? queueJobId;
  final String? queueJobStatus;
  Note({
    required this.id,
    this.project,
    required this.kind,
    required this.title,
    required this.body,
    required this.status,
    this.tags,
    this.closedAt,
    this.closeReason,
    this.queueJobId,
    this.queueJobStatus,
  });
  factory Note.fromJson(Map<String, dynamic> j) {
    final queue = j['queue_job'] as Map?;
    return Note(
      id: j['id'],
      project: j['project'],
      kind: j['kind'] ?? 'note',
      title: j['title'] ?? '',
      body: j['body'] ?? '',
      status: j['status'] ?? 'open',
      tags: j['tags'],
      closedAt: j['closed_at'],
      closeReason: j['close_reason'],
      queueJobId: queue?['id'] as int?,
      queueJobStatus: queue?['status']?.toString(),
    );
  }
}

class SystemStats {
  final double cpu;
  final num ramPct, diskPct;
  final num ramUsed, ramTotal, diskUsed, diskTotal;
  final int? batteryPct;
  final bool? charging;
  final List<Map<String, dynamic>> topProcs;
  SystemStats({
    required this.cpu,
    required this.ramPct,
    required this.diskPct,
    required this.ramUsed,
    required this.ramTotal,
    required this.diskUsed,
    required this.diskTotal,
    this.batteryPct,
    this.charging,
    required this.topProcs,
  });
  factory SystemStats.fromJson(Map<String, dynamic> j) {
    final bat = j['battery'];
    return SystemStats(
      cpu: (j['cpu_percent'] ?? 0).toDouble(),
      ramPct: j['ram']['percent'],
      ramUsed: j['ram']['used_gb'],
      ramTotal: j['ram']['total_gb'],
      diskPct: j['disk']['percent'],
      diskUsed: j['disk']['used_gb'],
      diskTotal: j['disk']['total_gb'],
      batteryPct: bat == null ? null : bat['percent'],
      charging: bat == null ? null : bat['charging'],
      topProcs: List<Map<String, dynamic>>.from(j['top_processes'] ?? []),
    );
  }
}

/// A Night Shift queue item (Tasks tab).
class QueueResultSummary {
  final bool available;
  final String kind, completeness;
  final int artifactCount;
  const QueueResultSummary({
    this.available = false,
    this.kind = 'unknown',
    this.completeness = 'none',
    this.artifactCount = 0,
  });

  factory QueueResultSummary.fromJson(Map<String, dynamic> json) =>
      QueueResultSummary(
        available: json['available'] == true,
        kind: json['kind']?.toString() ?? 'unknown',
        completeness: json['completeness']?.toString() ?? 'none',
        artifactCount: (json['artifact_count'] as num?)?.toInt() ?? 0,
      );
}

class QueueResultArtifact {
  final String kind, ref, name;
  final int? size;
  final List<String> filesChanged;
  const QueueResultArtifact({
    required this.kind,
    required this.ref,
    this.name = '',
    this.size,
    this.filesChanged = const [],
  });

  factory QueueResultArtifact.fromJson(Map<String, dynamic> json) =>
      QueueResultArtifact(
        kind: json['kind']?.toString() ?? 'artifact',
        ref: json['path']?.toString() ?? json['ref']?.toString() ?? '',
        name: json['name']?.toString() ?? '',
        size: (json['size'] as num?)?.toInt(),
        filesChanged: List<String>.from(json['files_changed'] ?? const []),
      );
}

class QueueAttempt {
  final int id, attemptNo;
  final String engine, stage, status;
  final String? error, stdout, stderr, output;
  final int? exitCode;
  final bool stdoutTruncated, stderrTruncated, outputTruncated;
  final double? startedAt, endedAt;
  const QueueAttempt({
    required this.id,
    required this.attemptNo,
    required this.engine,
    required this.stage,
    required this.status,
    this.error,
    this.exitCode,
    this.stdout,
    this.stderr,
    this.output,
    this.stdoutTruncated = false,
    this.stderrTruncated = false,
    this.outputTruncated = false,
    this.startedAt,
    this.endedAt,
  });

  factory QueueAttempt.fromJson(Map<String, dynamic> json) => QueueAttempt(
    id: (json['id'] as num?)?.toInt() ?? 0,
    attemptNo: (json['attempt_no'] as num?)?.toInt() ?? 0,
    engine: json['engine']?.toString() ?? 'unknown',
    stage: json['stage']?.toString() ?? 'unknown',
    status: json['status']?.toString() ?? 'unknown',
    error: json['error']?.toString(),
    exitCode: (json['exit_code'] as num?)?.toInt(),
    stdout: json['stdout']?.toString(),
    stderr: json['stderr']?.toString(),
    output: json['output']?.toString(),
    stdoutTruncated: _queueBool(json['stdout_truncated']),
    stderrTruncated: _queueBool(json['stderr_truncated']),
    outputTruncated: _queueBool(json['output_truncated']),
    startedAt: (json['started_at'] as num?)?.toDouble(),
    endedAt: (json['ended_at'] as num?)?.toDouble(),
  );
}

class QueueJobResult {
  final int jobId;
  final bool available;
  final String kind, completeness, text;
  final String? title;
  final List<QueueResultArtifact> artifacts;
  final List<QueueAttempt> attempts;
  const QueueJobResult({
    required this.jobId,
    required this.available,
    required this.kind,
    required this.completeness,
    required this.text,
    this.title,
    this.artifacts = const [],
    this.attempts = const [],
  });

  factory QueueJobResult.fromJson(Map<String, dynamic> json) {
    final details = Map<String, dynamic>.from(json['result'] ?? json);
    return QueueJobResult(
      jobId: (json['job_id'] as num?)?.toInt() ?? 0,
      available: details['available'] == true,
      kind: details['kind']?.toString() ?? 'unknown',
      completeness: details['completeness']?.toString() ?? 'none',
      text: details['text']?.toString() ?? '',
      title: json['title']?.toString() ?? details['title']?.toString(),
      artifacts: (details['artifacts'] as List? ?? const [])
          .map(
            (e) => QueueResultArtifact.fromJson(Map<String, dynamic>.from(e)),
          )
          .toList(),
      attempts:
          (json['attempts'] as List? ??
                  details['attempts'] as List? ??
                  const [])
              .map((e) => QueueAttempt.fromJson(Map<String, dynamic>.from(e)))
              .toList(),
    );
  }
}

bool _queueBool(dynamic value) => value == true || value == 1;

class QueueJob {
  final int id;
  final String project,
      projectName,
      task,
      title,
      tag,
      engine,
      status,
      readiness;
  final String? branch, summary, closeReason, previousStatus;
  final Map<String, dynamic> spec, deployment, supervision, awareness;
  final QueueResultSummary result;
  final int attemptCount;
  final List<String> filesChanged;
  final List<int> dependsOn, blockedBy;
  final List<Map<String, dynamic>> dependencies, closureHistory;
  final int tokensTotal;
  final double? createdAt, endedAt, closedAt;
  QueueJob({
    required this.id,
    required this.project,
    required this.projectName,
    required this.task,
    required this.title,
    required this.readiness,
    required this.tag,
    required this.engine,
    required this.status,
    this.branch,
    this.summary,
    this.closeReason,
    this.previousStatus,
    required this.spec,
    required this.deployment,
    required this.supervision,
    required this.awareness,
    this.result = const QueueResultSummary(),
    this.attemptCount = 0,
    required this.filesChanged,
    required this.tokensTotal,
    required this.dependsOn,
    required this.blockedBy,
    required this.dependencies,
    required this.closureHistory,
    this.createdAt,
    this.endedAt,
    this.closedAt,
  });
  factory QueueJob.fromJson(Map<String, dynamic> j) => QueueJob(
    id: j['id'],
    project: j['project'] ?? '',
    projectName: j['project_name'] ?? '',
    task: j['task'] ?? '',
    title: j['title'] ?? j['task'] ?? 'Untitled task',
    readiness: j['readiness'] ?? j['spec']?['readiness'] ?? 'draft',
    tag: j['tag'] ?? 'auto',
    engine: j['engine'] ?? 'auto',
    status: j['status'] ?? 'queued',
    branch: j['branch'],
    summary: j['summary'],
    closeReason: j['close_reason'],
    previousStatus: j['previous_status'],
    spec: Map<String, dynamic>.from(j['spec'] ?? const {}),
    deployment: Map<String, dynamic>.from(j['deployment'] ?? const {}),
    supervision: Map<String, dynamic>.from(j['supervision'] ?? const {}),
    awareness: Map<String, dynamic>.from(j['awareness'] ?? const {}),
    result: QueueResultSummary.fromJson(
      Map<String, dynamic>.from(j['result'] ?? const {}),
    ),
    attemptCount: (j['attempt_count'] as num?)?.toInt() ?? 0,
    filesChanged: List<String>.from(j['files_changed'] ?? const []),
    dependsOn: List<int>.from(j['depends_on'] ?? const []),
    blockedBy: List<int>.from(j['blocked_by'] ?? const []),
    dependencies: List<Map<String, dynamic>>.from(
      (j['dependencies'] ?? const []).map((e) => Map<String, dynamic>.from(e)),
    ),
    closureHistory: List<Map<String, dynamic>>.from(
      (j['closure_history'] ?? const []).map(
        (e) => Map<String, dynamic>.from(e),
      ),
    ),
    tokensTotal: j['tokens_total'] ?? 0,
    createdAt: (j['created_at'] as num?)?.toDouble(),
    endedAt: (j['ended_at'] as num?)?.toDouble(),
    closedAt: (j['closed_at'] as num?)?.toDouble(),
  );

  bool get isDraft => readiness != 'refined';
  String get workType => spec['work_type']?.toString() ?? 'coding';
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
    required this.id,
    required this.type,
    required this.title,
    required this.body,
    required this.status,
    required this.needsResponse,
    this.response,
    this.refId,
    this.createdAt,
  });
  bool get isUnread => status == 'unread';
  factory AppNotification.fromJson(Map<String, dynamic> j) => AppNotification(
    id: j['id'],
    type: j['type'] ?? '',
    title: j['title'] ?? '',
    body: j['body'] ?? '',
    status: j['status'] ?? 'unread',
    needsResponse: j['needs_response'] ?? false,
    response: j['response'],
    refId: j['ref_id'],
    createdAt: (j['created_at'] as num?)?.toDouble(),
  );
}

/// A multiple-choice question the agent attached to a reply ([[ask:{…}]]).
class AskCard {
  final String question;
  final List<String> options;
  final bool multi;
  const AskCard(this.question, this.options, {this.multi = false});
}

class ChatMessage {
  final String role; // user | bot | error | status
  String text; // mutable so a live 'status' bubble can accumulate steps
  final String? localImage; // absolute local file path (image the user sent)
  final List<String>
  remoteImages; // full /api/file URLs (images the agent sent back)
  final String?
  moveTo; // dir to offer moving this question to (confirm-to-move)
  // What the agent did to produce this reply. Kept on the message so the trace
  // survives the live bubble being replaced — it used to be discarded there.
  final String? runId;
  final String? localRequestId;
  final List<RunStep> steps;
  final String? project; // where the turn ran
  final String? stopLabel; // why it stopped
  final bool hitStepLimit;
  final AskCard? ask; // choices to tap, when the agent asked a question
  final int? messageId; // durable server identity, used for explicit replies
  final int? replyToMessageId;
  final String? replyToContent;
  final String? replyToRole;
  ChatMessage(
    this.role,
    this.text, {
    this.ask,
    this.localImage,
    List<String>? remoteImages,
    this.moveTo,
    this.runId,
    this.localRequestId,
    List<RunStep>? steps,
    this.project,
    this.stopLabel,
    this.hitStepLimit = false,
    this.messageId,
    this.replyToMessageId,
    this.replyToContent,
    this.replyToRole,
  }) : remoteImages = remoteImages ?? const [],
       steps = steps ?? const [];
}

class AssistantWork {
  final String id, status, summary, nextAction, blocker;
  final String command;
  final int revision;
  const AssistantWork({
    required this.id,
    required this.status,
    required this.summary,
    required this.nextAction,
    required this.blocker,
    required this.revision,
    this.command = '',
  });

  bool get isActive => const {
    'accepted',
    'working',
    'recovering',
    'waiting_for_user',
    'failed',
    'unverified',
  }.contains(status);

  /// A completed result can still receive an explicit correction. The client
  /// never treats it as running or restores it as active work; classification
  /// must first decide that the new message is a correction/retry.
  bool get isContinuable => isActive || status == 'completed';

  factory AssistantWork.fromJson(Map<String, dynamic> j) => AssistantWork(
    id: j['id']?.toString() ?? '',
    command: j['command']?.toString() ?? '',
    status: j['status']?.toString() ?? 'accepted',
    summary: j['summary']?.toString() ?? '',
    nextAction: j['next_action']?.toString() ?? '',
    blocker: j['blocker']?.toString() ?? '',
    revision: (j['revision'] as num?)?.toInt() ?? 1,
  );
}
