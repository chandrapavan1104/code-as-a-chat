import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/storage.dart';
import '../core/theme.dart';

/// Runs one explicit skill action at a time and keeps its streamed result visible.
class SkillActionScreen extends ConsumerStatefulWidget {
  final Skill skill;
  final Future<String> Function()? sessionIdLoader;
  const SkillActionScreen({
    super.key,
    required this.skill,
    this.sessionIdLoader,
  });

  @override
  ConsumerState<SkillActionScreen> createState() => _SkillActionScreenState();
}

class _SkillActionScreenState extends ConsumerState<SkillActionScreen> {
  final _arguments = TextEditingController();
  final _task = TextEditingController();
  List<Map<String, dynamic>> _projects = [];
  String? _project;
  String? _projectError;
  String? _action;
  String? _result;
  String? _error;
  String _progress = '';
  final List<String> _steps = [];
  bool _loadingProjects = true;
  bool _running = false;

  String get _name => widget.skill.name.toLowerCase();
  bool get _isCodingAgent =>
      const {'claude', 'codex', 'antigravity', 'gemini'}.contains(_name);
  bool get _hasActions =>
      const {'context', 'errors', 'auth', 'firebase', 'fix'}.contains(_name);
  List<({String value, String label})> get _actions => switch (_name) {
    'context' => const [
      (value: 'show', label: 'Show context'),
      (value: 'status', label: 'Context status'),
      (value: 'init', label: 'Initialize context'),
      (value: 'refresh', label: 'Refresh context'),
      (value: 'sync', label: 'Sync context files'),
      (value: 'add', label: 'Add changelog entry'),
    ],
    'errors' => const [
      (value: 'recent', label: 'Recent errors'),
      (value: 'server', label: 'Server errors'),
      (value: 'app', label: 'App errors'),
      (value: 'full', label: 'Full details'),
      (value: 'clear', label: 'Clear errors'),
    ],
    'auth' => const [
      (value: 'status', label: 'Check all login status'),
      (value: 'claude', label: 'Claude login help'),
      (value: 'codex', label: 'Start Codex login'),
      (value: 'gemini', label: 'Gemini login help'),
    ],
    'firebase' => const [
      (value: 'status', label: 'Firebase status'),
      (value: 'list', label: 'List Firebase projects'),
      (value: 'use', label: 'Use Firebase project'),
      (value: 'deploy', label: 'Deploy'),
      (value: 'preview', label: 'Create hosting preview'),
      (value: 'whoami', label: 'Show signed-in account'),
    ],
    'fix' => const [
      (value: 'diagnose', label: 'Diagnose a problem'),
      (value: 'ship', label: 'Ship pending fix'),
    ],
    _ => const [],
  };

  @override
  void initState() {
    super.initState();
    _action = switch (_name) {
      'context' => 'show',
      'errors' => 'recent',
      'auth' => 'status',
      'firebase' => 'status',
      'fix' => 'diagnose',
      _ => null,
    };
    _loadProjects();
  }

  @override
  void dispose() {
    _arguments.dispose();
    _task.dispose();
    super.dispose();
  }

  Future<void> _loadProjects() async {
    final api = ref.read(apiProvider);
    if (api == null) {
      setState(() {
        _loadingProjects = false;
        _projectError = 'Connect to your Mac to load projects.';
      });
      return;
    }
    try {
      final data = await api.projects();
      if (!mounted) return;
      final projects = List<Map<String, dynamic>>.from(
        (data['projects'] as List? ?? const []).map(
          (item) => Map<String, dynamic>.from(item),
        ),
      );
      setState(() {
        _projects = projects;
        _project =
            data['current_name']?.toString() ??
            projects
                .where((p) => p['active'] == true)
                .firstOrNull?['name']
                ?.toString();
        _loadingProjects = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _projectError = friendlyError(e);
        _loadingProjects = false;
      });
    }
  }

  String get _actionTitle =>
      _actions.where((item) => item.value == _action).firstOrNull?.label ??
      'Run action';

  String? _prompt() {
    if (_isCodingAgent || (_name == 'fix' && _action == 'diagnose')) {
      return _task.text.trim();
    }
    if (_name == 'build') return '';
    if (_hasActions) {
      final action = _action ?? '';
      if ((_name == 'context' && action == 'add')) {
        final text = _arguments.text.trim();
        return text.isEmpty ? null : 'add $text';
      }
      if (_name == 'errors' && action == 'recent') return '';
      if (_name == 'firebase' &&
          const {'use', 'deploy', 'preview'}.contains(action)) {
        final args = _arguments.text.trim();
        return action == 'use'
            ? 'use $args'
            : args.isEmpty
            ? action
            : '$action $args';
      }
      if (_name == 'fix' && action == 'diagnose') return _task.text.trim();
      return action;
    }
    return _arguments.text.trim();
  }

  String? _emptyPromptMessage() {
    if ((_isCodingAgent || (_name == 'fix' && _action == 'diagnose')) &&
        _task.text.trim().isEmpty) {
      return 'Describe the task before running it.';
    }
    if (_hasActions &&
        _name == 'context' &&
        _action == 'add' &&
        _arguments.text.trim().isEmpty) {
      return 'Enter a changelog entry to add.';
    }
    if (_name == 'firebase' &&
        _action == 'use' &&
        _arguments.text.trim().isEmpty) {
      return 'Enter a Firebase project ID.';
    }
    return null;
  }

  bool get _requiresConfirmation =>
      (_name == 'context' &&
          const {'init', 'refresh', 'sync', 'add'}.contains(_action)) ||
      (_name == 'errors' && _action == 'clear') ||
      (_name == 'auth' && _action == 'codex') ||
      (_name == 'firebase' && const {'use', 'deploy'}.contains(_action)) ||
      (_name == 'fix' && _action == 'ship') ||
      _name == 'build';

  String get _confirmationMessage => switch (_name) {
    'context' when _action == 'init' =>
      'Create the shared AGENTS.md, CLAUDE.md, and GEMINI.md context files in $_project?',
    'context' when _action == 'refresh' =>
      'Ask Claude to rewrite the shared context files for $_project?',
    'context' when _action == 'sync' =>
      'Converge the shared context files in $_project?',
    'context' when _action == 'add' =>
      'Add this entry to the shared changelog in $_project?',
    'errors' => 'Clear all captured server and app errors?',
    'auth' =>
      'Start a Codex device-code login? You will approve it yourself in the provider browser.',
    'firebase' when _action == 'use' =>
      'Set the active Firebase project for $_project?',
    'firebase' => 'Deploy Firebase resources from $_project?',
    'fix' => 'Merge and apply the pending fix?',
    'build' => 'Build and deploy a new Gajala APK?',
    _ => 'Run this action?',
  };

  Future<bool> _confirm() async =>
      await showDialog<bool>(
        context: context,
        builder: (dialogContext) => AlertDialog(
          title: const Text('Confirm action'),
          content: Text(_confirmationMessage),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialogContext, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(dialogContext, true),
              child: const Text('Continue'),
            ),
          ],
        ),
      ) ??
      false;

  Future<void> _run() async {
    if (_running) return;
    final prompt = _prompt();
    final validation = _emptyPromptMessage();
    if (validation != null || prompt == null) {
      setState(
        () =>
            _error = validation ?? 'Enter a value before running this action.',
      );
      return;
    }
    if (_requiresConfirmation && !await _confirm()) return;
    final api = ref.read(apiProvider);
    if (api == null) {
      setState(
        () => _error = 'Connect to your Mac before running this action.',
      );
      return;
    }

    setState(() {
      _running = true;
      _result = null;
      _error = null;
      _steps.clear();
      _progress = 'Starting ${widget.skill.name}…';
    });
    try {
      final sessionId = await (widget.sessionIdLoader ?? Storage.sessionId)();
      var receivedFinal = false;
      await for (final event in api.runStream(
        widget.skill.command,
        prompt,
        sessionId,
        notify: false,
        project: _project,
      )) {
        if (!mounted) return;
        if (event['type'] == 'phone_request') {
          setState(() {
            _error =
                'This action needs an on-phone approval that is unavailable from the skill screen.';
            _progress = '';
          });
          break;
        }
        switch (event['type']) {
          case 'step':
            final number = (event['n'] as num?)?.toInt();
            final label = (event['tool'] ?? event['label'] ?? 'Working…')
                .toString();
            final line = number == null ? label : 'Step $number · $label';
            setState(() {
              _progress = line;
              _steps.add(line);
              if (_steps.length > 30) _steps.removeAt(0);
            });
          case 'run':
            final project = event['project']?.toString();
            if (project != null && project.isNotEmpty) {
              setState(() => _progress = 'Running in $project');
            }
          case 'error':
            setState(() {
              _error = (event['message'] ?? 'The action failed.').toString();
              _progress = '';
            });
          case 'final':
            receivedFinal = true;
            setState(() {
              _result = (event['result'] ?? '(no result)').toString();
              _error = null;
              _progress = 'Finished';
              final changedProject = event['workspace']?.toString();
              if (changedProject != null && changedProject.isNotEmpty) {
                _project = changedProject;
              }
            });
        }
      }
      if (mounted && !receivedFinal && _error == null) {
        setState(() {
          _error = 'The stream ended before a final result arrived.';
          _progress = '';
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = friendlyError(e);
          _progress = '';
        });
      }
    } finally {
      if (mounted) setState(() => _running = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final api = ref.watch(apiProvider);
    return Scaffold(
      appBar: AppBar(title: Text(_screenTitle)),
      body: api == null
          ? const Center(child: Text('Connect to your Mac to run this skill.'))
          : SafeArea(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  Flexible(
                    flex: _result == null && _error == null ? 0 : 1,
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                            widget.skill.helpLine,
                            style: TextStyle(color: context.pal.textDim),
                          ),
                          const SizedBox(height: 12),
                          _projectPicker(),
                          if (_projectError != null) ...[
                            const SizedBox(height: 6),
                            Text(
                              _projectError!,
                              style: TextStyle(color: context.pal.textDim),
                            ),
                          ],
                          if (_hasActions) ...[
                            const SizedBox(height: 14),
                            DropdownButtonFormField<String>(
                              key: ValueKey('action-${widget.skill.name}'),
                              initialValue: _action,
                              decoration: const InputDecoration(
                                labelText: 'Action',
                                border: OutlineInputBorder(),
                              ),
                              items: [
                                for (final action in _actions)
                                  DropdownMenuItem(
                                    value: action.value,
                                    child: Text(action.label),
                                  ),
                              ],
                              onChanged: _running
                                  ? null
                                  : (value) => setState(() {
                                      _action = value;
                                      _error = null;
                                    }),
                            ),
                          ],
                          if (_needsTextInput) ...[
                            const SizedBox(height: 12),
                            TextField(
                              controller: _isCodingAgent ? _task : _arguments,
                              minLines: 2,
                              maxLines: 5,
                              enabled: !_running,
                              textInputAction: TextInputAction.newline,
                              decoration: InputDecoration(
                                labelText: _isCodingAgent
                                    ? 'Task'
                                    : _name == 'fix'
                                    ? 'Problem description'
                                    : _name == 'firebase' && _action == 'use'
                                    ? 'Firebase project ID'
                                    : _name == 'firebase' && _action == 'deploy'
                                    ? 'Deploy target (optional)'
                                    : _name == 'firebase' &&
                                          _action == 'preview'
                                    ? 'Preview channel name (optional)'
                                    : _name == 'context'
                                    ? 'Changelog entry'
                                    : 'Command arguments',
                                hintText: _isCodingAgent
                                    ? 'Describe what you want the agent to do'
                                    : null,
                                border: const OutlineInputBorder(),
                              ),
                            ),
                          ],
                          if (_error != null) ...[
                            const SizedBox(height: 12),
                            Container(
                              padding: const EdgeInsets.all(12),
                              decoration: BoxDecoration(
                                color: GajalaColors.danger.withValues(
                                  alpha: .1,
                                ),
                                borderRadius: BorderRadius.circular(10),
                              ),
                              child: SelectableText(
                                _error!,
                                style: TextStyle(color: GajalaColors.danger),
                              ),
                            ),
                          ],
                          if (_progress.isNotEmpty) ...[
                            const SizedBox(height: 10),
                            Row(
                              children: [
                                if (_running)
                                  const SizedBox.square(
                                    dimension: 16,
                                    child: CircularProgressIndicator(
                                      strokeWidth: 2,
                                    ),
                                  ),
                                if (_running) const SizedBox(width: 10),
                                Expanded(child: Text(_progress)),
                              ],
                            ),
                            if (_steps.isNotEmpty)
                              Padding(
                                padding: const EdgeInsets.only(top: 4),
                                child: Text(
                                  _steps.last,
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                  style: TextStyle(color: context.pal.textDim),
                                ),
                              ),
                          ],
                        ],
                      ),
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
                    child: FilledButton.icon(
                      onPressed: _running ? null : _run,
                      icon: _running
                          ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.play_arrow),
                      label: Text(_running ? 'Running…' : _runLabel),
                    ),
                  ),
                  if (_result != null)
                    Expanded(
                      flex: 2,
                      child: Container(
                        margin: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                        padding: const EdgeInsets.all(14),
                        decoration: BoxDecoration(
                          color: context.pal.surfaceAlt,
                          borderRadius: BorderRadius.circular(12),
                        ),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              'Result',
                              style: Theme.of(context).textTheme.titleSmall,
                            ),
                            const Divider(),
                            Expanded(
                              child: SingleChildScrollView(
                                child: SelectableText(_result!),
                              ),
                            ),
                          ],
                        ),
                      ),
                    ),
                ],
              ),
            ),
    );
  }

  bool get _needsTextInput =>
      _isCodingAgent ||
      (_name == 'context' && _action == 'add') ||
      (_name == 'fix' && _action == 'diagnose') ||
      (_name == 'firebase' &&
          const {'use', 'deploy', 'preview'}.contains(_action)) ||
      (!_hasActions && _name != 'build');

  String get _screenTitle => switch (_name) {
    'build' => 'Build Gajala',
    'claude' => 'Claude task',
    'codex' => 'Codex task',
    'antigravity' || 'gemini' => 'Gemini task',
    _ =>
      widget.skill.name.isEmpty
          ? 'Skill action'
          : '${widget.skill.name[0].toUpperCase()}${widget.skill.name.substring(1)}',
  };

  String get _runLabel => switch (_name) {
    'build' => 'Build APK',
    _ when _isCodingAgent => 'Run task',
    _ when _hasActions => 'Run $_actionTitle',
    _ => 'Run skill',
  };

  Widget _projectPicker() {
    if (_loadingProjects) {
      return const LinearProgressIndicator(minHeight: 2);
    }
    if (_projects.isEmpty) {
      return InputDecorator(
        decoration: const InputDecoration(
          labelText: 'Project',
          border: OutlineInputBorder(),
        ),
        child: Text(_project ?? 'Use current server project'),
      );
    }
    final names = _projects
        .map((item) => item['name']?.toString() ?? '')
        .where((name) => name.isNotEmpty)
        .toSet()
        .toList();
    final value = names.contains(_project) ? _project : null;
    return DropdownButtonFormField<String>(
      key: const ValueKey('project-picker'),
      initialValue: value,
      decoration: const InputDecoration(
        labelText: 'Project',
        border: OutlineInputBorder(),
      ),
      items: [
        for (final project in _projects)
          if ((project['name']?.toString() ?? '').isNotEmpty)
            DropdownMenuItem(
              value: project['name'].toString(),
              child: Text(
                project['name'].toString(),
                overflow: TextOverflow.ellipsis,
              ),
            ),
      ],
      onChanged: _running ? null : (value) => setState(() => _project = value),
    );
  }
}
