import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import '../core/api.dart';
import '../core/chat_controller.dart';
import '../core/models.dart';
import '../core/push.dart';
import '../core/state.dart';
import '../core/theme.dart';

class ChatScreen extends ConsumerStatefulWidget {
  final String command;   // 'shell' = Gajala agent; else a specific skill
  final String title;
  const ChatScreen({super.key, this.command = 'shell', this.title = 'Gajala'});
  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen>
    with WidgetsBindingObserver {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  String? _installId;         // stable per-install id
  String? _sid;               // per-directory conversation id (installId::dir)
  XFile? _pending;   // image picked but not yet sent
  String? _dir;               // active project name (for the header)
  String _model = 'auto';     // pinned coding engine
  String? _lastUserText;      // last thing the user typed (to resend on "move")
  int _lastMsgCount = 0;      // to auto-scroll only when something new lands

  /// The conversation this screen is showing. State lives in the controller so
  /// it survives navigating away (a running turn keeps running and stays visible).
  ChatKey? get _key => _sid == null ? null : ChatKey(widget.command, _sid!);
  ChatController? get _chat =>
      _key == null ? null : ref.read(chatControllerProvider(_key!).notifier);

  String get _welcome => widget.command == 'shell'
      ? 'Em sangathi mava! Gajala ikkada 🔥\nCheppu — em kavali?'
      : 'Send a /${widget.command} request, or just type.';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _bootstrap();
  }

  /// Resolve the install id + active directory/model, then open THAT
  /// directory's conversation. Each directory is its own thread; the model is
  /// not part of the thread (switching models keeps the same conversation).
  Future<void> _bootstrap() async {
    _installId = await ref.read(sessionIdProvider.future);
    if (widget.command != 'shell') {
      setState(() => _sid = _sidFor(null));    // per-engine thread (persisted)
      Push.activeSession = _sid;
      await _chat?.ensureLoaded(welcome: _welcome);
      _restoreDraft();
      return;
    }
    final api = ref.read(apiProvider);
    String? dir;
    var model = 'auto';
    if (api != null) {
      try {
        final results = await Future.wait([api.projects(), api.model()]);
        dir = results[0]['current_name']?.toString();
        model = results[1]['engine']?.toString() ?? 'auto';
      } catch (_) {/* header stays blank */}
    }
    if (!mounted) return;
    setState(() {
      _dir = dir;
      _model = model;
      _sid = _sidFor(dir);
    });
    Push.activeSession = _sid;
    await _chat?.ensureLoaded(welcome: _welcome);
    _restoreDraft();
  }

  /// Put back the half-typed message you left in this conversation.
  void _restoreDraft() {
    if (_key == null || !mounted) return;
    final d = ref.read(chatControllerProvider(_key!)).draft;
    if (d.isNotEmpty && _input.text.isEmpty) _input.text = d;
  }

  /// Conversation id. The Gajala (shell) chat is per-directory — that dir IS the
  /// thread. Each skill tab (claude/codex/gemini/…) keeps its own engine thread.
  String _sidFor(String? dir) {
    if (widget.command != 'shell') {
      return '$_installId::${widget.command}';
    }
    if (dir == null || dir.isEmpty) return _installId ?? '';
    final slug = dir.toLowerCase().replaceAll(RegExp(r'[^a-z0-9]+'), '-');
    return '$_installId::$slug';
  }

  /// The server owns the active project. If it changed — the agent used the
  /// projects tool mid-turn, or it moved while we were backgrounded — follow it
  /// so the header + thread key stay in sync and reopening lands on the right
  /// thread. [reload] pulls the destination thread's history (used after we've
  /// been away); mid-turn we keep the visible messages and just re-key + note it.
  Future<void> _syncWorkspace(String? name, {bool reload = false}) async {
    if (widget.command != 'shell' || name == null || name.isEmpty) return;
    if (name == _dir) return;
    final sid = _sidFor(name);
    setState(() {
      _dir = name;
      _sid = sid;
    });
    Push.activeSession = sid;
    await _chat?.ensureLoaded(welcome: _welcome);
    if (!reload) _chat?.addSystemNote('Switched to $name');
  }

  /// Re-check the server's active project (shell chat only). Called on resume so
  /// a switch made elsewhere — another screen, the Mac, an agent — is reflected.
  Future<void> _refreshWorkspace() async {
    if (widget.command != 'shell') return;
    final api = ref.read(apiProvider);
    if (api == null) return;
    try {
      final proj = await api.projects();
      await _syncWorkspace(proj['current_name']?.toString(), reload: true);
    } catch (_) {/* keep showing the current thread */}
  }

  /// Swap the visible conversation to another directory's thread.
  Future<void> _switchConversation(String dir) async {
    if (dir == _dir) return;
    final sid = _sidFor(dir);
    setState(() {
      _dir = dir;
      _sid = sid;
    });
    Push.activeSession = sid;
    await _chat?.ensureLoaded(welcome: _welcome);
  }

  /// Confirm-to-move: switch to [dir] (server workspace + thread), then re-ask
  /// the question there. Wired to the "Ask in {dir} chat" action on a move reply.
  Future<void> _moveAndAsk(String dir) async {
    final prompt = _lastUserText;
    final api = ref.read(apiProvider);
    if (api != null) {
      try {
        await api.switchProject(dir);   // keep server workspace in lock-step
      } catch (_) {}
    }
    await _switchConversation(dir);
    if (prompt != null && prompt.isNotEmpty) {
      _input.text = prompt;
      await _send();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    if (Push.activeSession == _sid) Push.activeSession = null;
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Only "viewing" this chat while it's on top AND the app is foregrounded.
    if (state == AppLifecycleState.resumed) {
      Push.activeSession = _sid;
      _refreshWorkspace();   // catch a project switch made while we were away
    } else if (Push.activeSession == _sid) {
      Push.activeSession = null;
    }
  }

  /// Load a specific conversation's history (per-directory), rebuilding any
  /// images, and show a welcome line if the thread is empty.
  Future<void> _pickImage() async {
    try {
      final x = await ImagePicker().pickImage(
          source: ImageSource.gallery, maxWidth: 2000, imageQuality: 85);
      if (x != null) setState(() => _pending = x);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Could not pick image: $e')));
      }
    }
  }

  /// Send whatever is typed. The controller owns the turn, so it keeps running
  /// (and stays visible) if you navigate away — and if a turn is already in
  /// flight this message is queued instead of dropped.
  Future<void> _send() async {
    final text = _input.text.trim();
    final attach = _pending;
    if (text.isEmpty && attach == null) return;
    final chat = _chat;
    if (chat == null) return;
    _lastUserText = text;
    setState(() => _pending = null);
    _input.clear();
    chat.setDraft('');
    await chat.send(text, imagePath: attach?.path);
  }

  /// Pin the view to the newest message. A single post-frame scroll lands short
  /// when content is still laying out (long history, network images resolving
  /// their height), which is why a reopened chat used to sit at an old position.
  /// So we scroll now and again shortly after, and jump (not animate) on load.
  void _scrollEnd({bool animate = true}) {
    void go() {
      if (!_scroll.hasClients) return;
      final target = _scroll.position.maxScrollExtent;
      if (animate) {
        _scroll.animateTo(target,
            duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
      } else {
        _scroll.jumpTo(target);
      }
    }

    WidgetsBinding.instance.addPostFrameCallback((_) {
      go();
      Future.delayed(const Duration(milliseconds: 300), () {
        if (mounted) WidgetsBinding.instance.addPostFrameCallback((_) => go());
      });
    });
  }

  static String _modelLabel(String e) => e == 'auto' ? 'auto' : e[0].toUpperCase() + e.substring(1);

  Widget _buildTitle(BuildContext context) {
    // Skill-specific chats keep a plain title; the Gajala agent chat shows the
    // active "directory · model" and lets you switch both.
    if (widget.command != 'shell') return Text(widget.title);
    return InkWell(
      onTap: _openContextSheet,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(mainAxisSize: MainAxisSize.min, children: [
            Text(widget.title),
            const SizedBox(width: 3),
            const Icon(Icons.expand_more, size: 18),
          ]),
          Text(
            _dir == null ? 'tap to set context' : '$_dir · ${_modelLabel(_model)}',
            style: TextStyle(
                fontSize: 11, fontWeight: FontWeight.w400, color: context.pal.textDim),
          ),
        ],
      ),
    );
  }

  void _openContextSheet() {
    final api = ref.read(apiProvider);
    if (api == null) return;
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: context.pal.surface,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
      builder: (_) => _ContextSheet(
        api: api,
        currentDir: _dir,
        currentModel: _model,
        onChanged: (dir, model) {
          if (model != null) setState(() => _model = model);
          if (dir != null && dir != _dir) {
            // Switching directory swaps the whole conversation to that thread.
            _switchConversation(dir);
          } else if (model != null) {
            // Same thread, just a different engine — note it inline.
            _chat?.addSystemNote('Model → ${_modelLabel(_model)}');
            _scrollEnd();
          }
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final imgHeaders = ref.read(apiProvider)?.authHeaders;
    // Conversation state lives in the controller, so a turn started here keeps
    // running (and stays on screen) even if you leave and come back.
    final chat = _key == null
        ? const ChatState()
        : ref.watch(chatControllerProvider(_key!));
    final msgs = chat.messages;
    final sending = chat.sending;

    // Auto-scroll only when something new arrives (not on every rebuild).
    if (msgs.length != _lastMsgCount) {
      _lastMsgCount = msgs.length;
      _scrollEnd();
    }
    // Follow a project switch the agent made during a turn.
    if (chat.workspace != null && chat.workspace != _dir) {
      WidgetsBinding.instance
          .addPostFrameCallback((_) => _syncWorkspace(chat.workspace));
    }

    return Scaffold(
      appBar: AppBar(title: _buildTitle(context)),
      body: Column(children: [
        Expanded(
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.all(12),
            itemCount: msgs.length,
            itemBuilder: (_, i) => _Bubble(msgs[i], imgHeaders,
                onMove: sending ? null : _moveAndAsk),
          ),
        ),
        Container(
          decoration: BoxDecoration(
            color: context.pal.bg,
            border: Border(top: BorderSide(color: context.pal.border)),
          ),
          padding: const EdgeInsets.fromLTRB(8, 8, 8, 12),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            if (_pending != null)
              Align(
                alignment: Alignment.centerLeft,
                child: Padding(
                  padding: const EdgeInsets.only(bottom: 8, left: 4),
                  child: Stack(clipBehavior: Clip.none, children: [
                    ClipRRect(
                      borderRadius: BorderRadius.circular(10),
                      child: Image.file(File(_pending!.path),
                          width: 64, height: 64, fit: BoxFit.cover),
                    ),
                    Positioned(
                      top: -8, right: -8,
                      child: GestureDetector(
                        onTap: () => setState(() => _pending = null),
                        child: CircleAvatar(
                          radius: 11, backgroundColor: context.pal.surfaceAlt,
                          child: Icon(Icons.close, size: 14, color: context.pal.text),
                        ),
                      ),
                    ),
                  ]),
                ),
              ),
            Row(crossAxisAlignment: CrossAxisAlignment.end, children: [
              IconButton(
                icon: Icon(Icons.add_photo_alternate_outlined, color: context.pal.textDim),
                onPressed: sending ? null : _pickImage,
                tooltip: 'Attach image',
              ),
              Expanded(
                child: TextField(
                  controller: _input,
                  minLines: 1, maxLines: 6,
                  // Enter inserts a newline; the button sends (mobile standard).
                  keyboardType: TextInputType.multiline,
                  textInputAction: TextInputAction.newline,
                  onChanged: (v) => _chat?.setDraft(v),
                  decoration: InputDecoration(
                    hintText: sending
                        ? 'Send again to queue…'
                        : 'Message or /command…',
                  ),
                ),
              ),
              const SizedBox(width: 8),
              // Stays enabled while a turn runs — a second message queues.
              CircleAvatar(
                backgroundColor: GajalaColors.accent,
                child: IconButton(
                  icon: Icon(sending ? Icons.playlist_add : Icons.send,
                      color: Colors.white, size: 20),
                  onPressed: _send,
                  tooltip: sending ? 'Queue message' : 'Send',
                ),
              ),
            ]),
          ]),
        ),
      ]),
    );
  }
}

class _Bubble extends StatelessWidget {
  final ChatMessage m;
  final Map<String, String>? imgHeaders;   // auth headers for /api/file images
  final void Function(String dir)? onMove;  // confirm-to-move action
  const _Bubble(this.m, this.imgHeaders, {this.onMove});
  @override
  Widget build(BuildContext context) {
    if (m.role == 'status') return _StatusBubble(m.text);
    // Typed while a turn was running — waiting its turn, sent automatically.
    if (m.role == 'queued') {
      return Align(
        alignment: Alignment.centerRight,
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 4),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          constraints: const BoxConstraints(maxWidth: 300),
          decoration: BoxDecoration(
            color: GajalaColors.userBubble.withValues(alpha: .35),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: context.pal.textDim.withValues(alpha: .4)),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.end, children: [
            Text(m.text, style: TextStyle(color: context.pal.text.withValues(alpha: .75))),
            const SizedBox(height: 3),
            Row(mainAxisSize: MainAxisSize.min, children: [
              Icon(Icons.schedule, size: 11, color: context.pal.textDim),
              const SizedBox(width: 4),
              Text('queued',
                  style: TextStyle(fontSize: 10.5, color: context.pal.textDim)),
            ]),
          ]),
        ),
      );
    }
    if (m.role == 'system') {
      return Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 8),
          child: Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
            decoration: BoxDecoration(
              color: context.pal.surfaceAlt,
              borderRadius: BorderRadius.circular(12),
            ),
            child: Text(m.text,
                style: TextStyle(fontSize: 11.5, color: context.pal.textDim)),
          ),
        ),
      );
    }
    final isUser = m.role == 'user';
    final isError = m.role == 'error';
    final hasText = m.text.trim().isNotEmpty;
    final radius = BorderRadius.only(
      topLeft: const Radius.circular(16), topRight: const Radius.circular(16),
      bottomLeft: Radius.circular(isUser ? 16 : 4),
      bottomRight: Radius.circular(isUser ? 4 : 16),
    );
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * .82),
        decoration: BoxDecoration(
          color: isUser ? GajalaColors.userBubble : context.pal.botBubble,
          borderRadius: radius,
          border: isError ? Border.all(color: GajalaColors.danger) : null,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            // Image the user attached (rendered from the local file).
            if (m.localImage != null)
              Padding(
                padding: EdgeInsets.only(bottom: hasText ? 8 : 0),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 320),
                    child: Image.file(File(m.localImage!),
                        fit: BoxFit.contain,
                        errorBuilder: (_, _, _) => const SizedBox()),
                  ),
                ),
              ),
            // Images the agent sent back (fetched from the server, auth'd).
            for (final url in m.remoteImages)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 320),
                    child: Image.network(url,
                        headers: imgHeaders, fit: BoxFit.contain,
                        loadingBuilder: (c, w, p) => p == null
                            ? w
                            : const SizedBox(
                                height: 120,
                                child: Center(child: CircularProgressIndicator(strokeWidth: 2))),
                        errorBuilder: (_, _, _) => Text('[image unavailable]',
                            style: TextStyle(color: context.pal.textDim))),
                  ),
                ),
              ),
            if (hasText)
              SelectableText(m.text,
                  style: TextStyle(
                      color: isError ? GajalaColors.danger : context.pal.text, height: 1.35)),
            if (m.moveTo != null && onMove != null) ...[
              const SizedBox(height: 8),
              TextButton.icon(
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
                  backgroundColor: GajalaColors.accent.withValues(alpha: 0.15),
                  foregroundColor: GajalaColors.accent,
                  visualDensity: VisualDensity.compact,
                ),
                icon: const Icon(Icons.arrow_forward, size: 16),
                label: Text('Ask in ${m.moveTo} chat'),
                onPressed: () => onMove!(m.moveTo!),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// Live progress bubble: a spinner plus the accumulating step labels while the
/// agent works. Replaced by the real reply bubble when `final` lands.
class _StatusBubble extends StatelessWidget {
  final String text;
  const _StatusBubble(this.text);
  @override
  Widget build(BuildContext context) {
    final lines = text.split('\n').where((l) => l.trim().isNotEmpty).toList();
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * .82),
        decoration: BoxDecoration(
          color: context.pal.botBubble,
          borderRadius: const BorderRadius.only(
            topLeft: Radius.circular(16), topRight: Radius.circular(16),
            bottomLeft: Radius.circular(4), bottomRight: Radius.circular(16),
          ),
        ),
        child: Row(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Padding(
            padding: const EdgeInsets.only(top: 3, right: 10),
            child: SizedBox(
              width: 13, height: 13,
              child: CircularProgressIndicator(strokeWidth: 2, color: context.pal.textDim),
            ),
          ),
          Flexible(
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              for (final l in lines)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 1),
                  child: Text(l,
                      style: TextStyle(
                          color: context.pal.textDim, fontSize: 13,
                          fontStyle: FontStyle.italic, height: 1.3)),
                ),
            ]),
          ),
        ]),
      ),
    );
  }
}

/// Bottom sheet to switch the active directory and pin the coding model, plus a
/// "continue on Mac" handoff for the active Claude session.
class _ContextSheet extends StatefulWidget {
  final GajalaApi api;
  final String? currentDir;
  final String currentModel;
  final void Function(String? dir, String? model) onChanged;
  const _ContextSheet(
      {required this.api,
      required this.currentDir,
      required this.currentModel,
      required this.onChanged});
  @override
  State<_ContextSheet> createState() => _ContextSheetState();
}

class _ContextSheetState extends State<_ContextSheet> {
  List<Map<String, dynamic>> _projects = [];
  List<Map<String, dynamic>> _sessions = [];
  String? _dir;
  late String _model;
  Map<String, String> _engineModels = {};   // engine → pinned model ('' = default)
  Map<String, String> _engineBackups = {};   // engine → backup model ('' = none)
  Map<String, List<String>> _presets = {};   // engine → selectable models
  List<String> _engines = const ['auto', 'claude', 'codex', 'gemini', 'qwen'];
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _dir = widget.currentDir;
    _model = widget.currentModel;
    _load();
  }

  Future<void> _load() async {
    try {
      final proj = await widget.api.projects();
      final sess = await widget.api.activeSessions();
      final mdl = await widget.api.model();
      if (!mounted) return;
      setState(() {
        _projects = List<Map<String, dynamic>>.from(proj['projects'] ?? []);
        _dir = proj['current_name']?.toString() ?? _dir;
        _sessions = List<Map<String, dynamic>>.from(sess['sessions'] ?? []);
        _engineModels = (mdl['models'] as Map?)
                ?.map((k, v) => MapEntry(k.toString(), (v ?? '').toString())) ??
            {};
        _engineBackups = (mdl['backup_models'] as Map?)
                ?.map((k, v) => MapEntry(k.toString(), (v ?? '').toString())) ??
            {};
        _engines = (mdl['options'] as List?)?.map((e) => e.toString()).toList() ??
            _engines;
        _presets = (mdl['presets'] as Map?)?.map((k, v) =>
                MapEntry(k.toString(), List<String>.from(v ?? const []))) ??
            {};
      });
    } catch (_) {/* leave lists empty */}
  }

  /// Pin a model for the currently selected engine.
  Future<void> _pinEngineModel(String engine, String model) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final now = await widget.api.setEngineModel(engine, model);
      if (mounted) {
        setState(() => _engineModels =
            now.map((k, v) => MapEntry(k.toString(), (v ?? '').toString())));
      }
    } catch (_) {} finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// Pin the backup model (used if the primary fails) for the selected engine.
  Future<void> _pinEngineBackup(String engine, String backup) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final now = await widget.api.setEngineBackup(engine, backup);
      if (mounted) {
        setState(() => _engineBackups =
            now.map((k, v) => MapEntry(k.toString(), (v ?? '').toString())));
      }
    } catch (_) {} finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _switchDir(String name) async {
    if (_busy || name == _dir) return;
    setState(() => _busy = true);
    try {
      final now = await widget.api.switchProject(name);
      widget.onChanged(now, null);
      final sess = await widget.api.activeSessions();
      if (mounted) {
        setState(() {
          _dir = now;
          _sessions = List<Map<String, dynamic>>.from(sess['sessions'] ?? []);
        });
      }
    } catch (_) {} finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _pinModel(String engine) async {
    if (_busy || engine == _model) return;
    setState(() => _busy = true);
    try {
      final now = await widget.api.setModel(engine);
      widget.onChanged(null, now);
      if (mounted) setState(() => _model = now);
    } catch (_) {} finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    final resumable = _sessions.where((s) => s['resume_cmd'] != null).toList();
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(
            left: 16, right: 16, top: 14,
            bottom: 16 + MediaQuery.of(context).viewInsets.bottom),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: [
              Center(
                child: Container(
                  width: 36, height: 4, margin: const EdgeInsets.only(bottom: 14),
                  decoration: BoxDecoration(
                      color: pal.textDim, borderRadius: BorderRadius.circular(2)),
                ),
              ),
              _sectionLabel(context, 'MODEL'),
              const SizedBox(height: 8),
              Wrap(
                spacing: 8, runSpacing: 8,
                children: _engines.map((e) {
                  final on = e == _model;
                  return ChoiceChip(
                    label: Text(_ChatScreenState._modelLabel(e)),
                    selected: on,
                    onSelected: _busy ? null : (_) => _pinModel(e),
                  );
                }).toList(),
              ),
              const SizedBox(height: 6),
              Text(
                _model == 'auto'
                    ? 'Gajala picks the engine per request.'
                    : 'Coding runs on ${_ChatScreenState._modelLabel(_model)} unless you name another.',
                style: TextStyle(fontSize: 11.5, color: pal.textDim),
              ),
              // Per-engine model picker — only meaningful once an engine is pinned.
              if (_model != 'auto' && (_presets[_model]?.isNotEmpty ?? false)) ...[
                const SizedBox(height: 16),
                _sectionLabel(context,
                    '${_ChatScreenState._modelLabel(_model).toUpperCase()} MODEL'),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8, runSpacing: 8,
                  children: [
                    for (final m in ['default', ...?_presets[_model]])
                      Builder(builder: (_) {
                        final cur = _engineModels[_model] ?? '';
                        final on = m == 'default' ? cur.isEmpty : cur == m;
                        return ChoiceChip(
                          label: Text(m),
                          selected: on,
                          onSelected: _busy
                              ? null
                              : (_) => _pinEngineModel(
                                  _model, m == 'default' ? '' : m),
                        );
                      }),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  (_engineModels[_model] ?? '').isEmpty
                      ? 'Using the CLI default model.'
                      : '${_ChatScreenState._modelLabel(_model)} → ${_engineModels[_model]}',
                  style: TextStyle(fontSize: 11.5, color: pal.textDim),
                ),
                const SizedBox(height: 16),
                _sectionLabel(context, 'BACKUP MODEL (IF PRIMARY FAILS)'),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8, runSpacing: 8,
                  children: [
                    for (final m in ['none', ...?_presets[_model]])
                      Builder(builder: (_) {
                        final cur = _engineBackups[_model] ?? '';
                        final on = m == 'none' ? cur.isEmpty : cur == m;
                        return ChoiceChip(
                          label: Text(m),
                          selected: on,
                          onSelected: _busy
                              ? null
                              : (_) => _pinEngineBackup(
                                  _model, m == 'none' ? '' : m),
                        );
                      }),
                  ],
                ),
                const SizedBox(height: 6),
                Text(
                  (_engineBackups[_model] ?? '').isEmpty
                      ? 'No backup — a failed run just errors.'
                      : 'If ${_engineModels[_model].toString().isEmpty ? "the primary" : _engineModels[_model]} fails, retry on ${_engineBackups[_model]}.',
                  style: TextStyle(fontSize: 11.5, color: pal.textDim),
                ),
              ],
              if (resumable.isNotEmpty) ...[
                const SizedBox(height: 16),
                _sectionLabel(context, 'CONTINUE ON MAC'),
                const SizedBox(height: 6),
                for (final s in resumable)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 6),
                    child: InkWell(
                      onTap: () {
                        Clipboard.setData(
                            ClipboardData(text: s['resume_cmd'].toString()));
                        ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                            content: Text('Copied ${s['engine']} resume command')));
                      },
                      child: Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(10),
                        decoration: BoxDecoration(
                            color: pal.surfaceAlt,
                            borderRadius: BorderRadius.circular(8)),
                        child: Row(children: [
                          Expanded(
                            child: Text(s['resume_cmd'].toString(),
                                style: const TextStyle(
                                    fontFamily: 'monospace', fontSize: 12)),
                          ),
                          Icon(Icons.copy, size: 16, color: pal.textDim),
                        ]),
                      ),
                    ),
                  ),
              ],
              const SizedBox(height: 16),
              _sectionLabel(context, 'DIRECTORY'),
              const SizedBox(height: 4),
              ConstrainedBox(
                constraints: BoxConstraints(
                    maxHeight: MediaQuery.of(context).size.height * 0.35),
                child: _projects.isEmpty
                    ? Padding(
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        child: Text('No projects found.',
                            style: TextStyle(color: pal.textDim)),
                      )
                    : ListView(
                        shrinkWrap: true,
                        children: _projects.map((p) {
                          final name = p['name']?.toString() ?? '';
                          final active = name == _dir;
                          return ListTile(
                            dense: true,
                            contentPadding: EdgeInsets.zero,
                            leading: Icon(
                                active ? Icons.folder : Icons.folder_outlined,
                                color: active ? GajalaColors.accent : pal.textDim,
                                size: 20),
                            title: Text(name),
                            trailing: active
                                ? const Icon(Icons.check, color: GajalaColors.accent, size: 18)
                                : null,
                            onTap: () => _switchDir(name),
                          );
                        }).toList(),
                      ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _sectionLabel(BuildContext context, String t) => Text(t,
      style: TextStyle(
          fontSize: 11,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.6,
          color: context.pal.textDim));
}
