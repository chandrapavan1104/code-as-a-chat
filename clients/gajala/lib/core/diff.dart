// Uncommitted changes of a project, as served by GET /api/projects/diff.

class DiffLine {
  final String type; // '+', '-', or ' '
  final int? oldNo;
  final int? newNo;
  final String text;
  const DiffLine(this.type, this.oldNo, this.newNo, this.text);

  factory DiffLine.fromJson(Map<String, dynamic> j) => DiffLine(
    (j['t'] ?? ' ').toString(),
    (j['old'] as num?)?.toInt(),
    (j['new'] as num?)?.toInt(),
    (j['text'] ?? '').toString(),
  );
}

class DiffHunk {
  final String header;
  final List<DiffLine> lines;
  const DiffHunk(this.header, this.lines);
}

class DiffFile {
  final String path;
  final String? oldPath;
  final String status; // M A D R
  final bool binary;
  final bool truncated;
  final int additions;
  final int deletions;
  final List<DiffHunk> hunks;
  final String patch;
  const DiffFile({
    required this.path,
    required this.status,
    required this.hunks,
    required this.patch,
    this.oldPath,
    this.binary = false,
    this.truncated = false,
    this.additions = 0,
    this.deletions = 0,
  });

  /// Every line of the file's hunks in order, for range selection.
  List<DiffLine> get allLines => [for (final h in hunks) ...h.lines];

  factory DiffFile.fromJson(Map<String, dynamic> j) => DiffFile(
    path: (j['path'] ?? '').toString(),
    oldPath: j['old_path']?.toString(),
    status: (j['status'] ?? 'M').toString(),
    binary: j['binary'] == true,
    truncated: j['truncated'] == true,
    additions: (j['additions'] as num?)?.toInt() ?? 0,
    deletions: (j['deletions'] as num?)?.toInt() ?? 0,
    patch: (j['patch'] ?? '').toString(),
    hunks: [
      for (final h in (j['hunks'] as List? ?? const []))
        DiffHunk(
          (h['header'] ?? '').toString(),
          [
            for (final l in (h['lines'] as List? ?? const []))
              DiffLine.fromJson(Map<String, dynamic>.from(l)),
          ],
        ),
    ],
  );
}

class ProjectDiff {
  final String project;
  final String? branch;
  final String? head;
  final List<DiffFile> files;
  final int omittedFiles;
  final int additions;
  final int deletions;
  const ProjectDiff({
    required this.project,
    required this.files,
    this.branch,
    this.head,
    this.omittedFiles = 0,
    this.additions = 0,
    this.deletions = 0,
  });

  factory ProjectDiff.fromJson(Map<String, dynamic> j) => ProjectDiff(
    project: (j['project'] ?? '').toString(),
    branch: j['branch']?.toString(),
    head: j['head']?.toString(),
    omittedFiles: (j['omitted_files'] as num?)?.toInt() ?? 0,
    additions: (j['additions'] as num?)?.toInt() ?? 0,
    deletions: (j['deletions'] as num?)?.toInt() ?? 0,
    files: [
      for (final f in (j['files'] as List? ?? const []))
        DiffFile.fromJson(Map<String, dynamic>.from(f)),
    ],
  );
}

/// Selected lines turned into a chat-ready reference the agent can act on.
String snippetForChat(DiffFile file, List<DiffLine> lines) {
  final numbers = [for (final l in lines) l.newNo ?? l.oldNo].whereType<int>();
  final range = numbers.isEmpty
      ? ''
      : numbers.first == numbers.last
      ? ' line ${numbers.first}'
      : ' lines ${numbers.first}–${numbers.last}';
  final body = lines.map((l) => '${l.type}${l.text}').join('\n');
  return 'In `${file.path}`$range:\n```diff\n$body\n```\n';
}
