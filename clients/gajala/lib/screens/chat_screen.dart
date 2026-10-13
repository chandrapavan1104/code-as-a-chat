import 'dart:async';
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
import '../core/storage.dart';
import '../core/theme.dart';
import '../core/voice.dart';
import '../widgets/run_trace.dart';
import '../widgets/chat_content.dart';
import '../widgets/quoted_reply.dart';
import '../widgets/swipe_reply.dart';
import '../widgets/chat_composer.dart';
import 'diff_screen.dart';
import 'voice_sheet.dart';

class ChatScreen extends ConsumerStatefulWidget {
  final String command; // 'shell' = Gajala agent; else a specific skill
  final String title;
  final String? sessionId;
  final String? project;
  final bool active;
  const ChatScreen({
    super.key,
    this.command = 'shell',
    this.title = 'Gajala',
    this.sessionId,
    this.project,
    this.active = true,
  });
  @override
  ConsumerState<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends ConsumerState<ChatScreen>
    with WidgetsBindingObserver {
  final _input = TextEditingController();
  final _inputFocus = FocusNode();
  final _scroll = ScrollController();
  String? _installId; // stable per-install id
  String? _sid; // per-directory conversation id (installId::dir)
  XFile? _pending; // image picked but not yet sent
  String? _dir; // active project name (for the header)
  String _model = 'auto'; // pinned coding engine
  String? _lastUserText; // last thing the user typed (to resend on "move")
  int _lastMsgCount = 0; // to auto-scroll only when something new lands
  // Re-entrancy guard: following a workspace signal swaps which controller the
  // screen watches, which rebuilds — this stops that rebuild starting another
  // follow before the first has finished.
  bool _followingWorkspace = false;
  Timer? _researchPoll;
  bool _foreground = true;
  bool _appResumed = true;
  bool _dictating = false;
  bool _dictated = false; // this draft came from the mic → speak the reply
  ChatMessage? _replyTarget;
  bool _independentConversation = false;
  String? _conversationProject;
  // The server's default project as this screen last saw it. Resume follows a
  // change to it (an explicit switch made on the Mac or Telegram), not a
  // mismatch: the agent's mid-turn switches never move the default, so
  // comparing against the open chat dragged you back to the old project.
  String? _serverProject;
  StreamSubscription<String>? _projectRequests;
  String? _pendingProject; // a request that arrived before bootstrap finished
  final Map<int, GlobalKey> _messageKeys = {};

  /// The conversation this screen is showing. State lives in the controller so
  /// it survives navigating away (a running turn keeps running and stays visible).
  ChatKey? get _key => _sid == null ? null : ChatKey(widget.command, _sid!);
  ChatController? get _chat =>
      _key == null ? null : ref.read(chatControllerProvider(_key!).notifier);

  @override
  void didUpdateWidget(covariant ChatScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.active == widget.active) return;
    _foreground = widget.active && _appResumed;
    if (!_foreground) {
      if (Push.activeSession == _sid) Push.activeSession = null;
      _stopResearchPolling();
      return;
    }
    Push.activeSession = _sid;
    _startResearchPolling();
    _chat?.refreshBackgroundResearch();
    _chat?.replayOutbox();
  }

  String get _welcome => widget.command == 'shell'
      ? 'Em sangathi mava! Gajala ikkada 🔥\nCheppu — em kavali?'
      : 'Send a /${widget.command} request, or just type.';

  @override
  void initState() {
    super.initState();
    _foreground = widget.active;
    WidgetsBinding.instance.addObserver(this);
    if (widget.command == 'shell') {
      _projectRequests = projectChatRequests.stream.listen(_followProjectRequest);
    }
    _bootstrap();
    if (_foreground) _startResearchPolling();
  }

  void _startResearchPolling() {
    if (_researchPoll != null || !_foreground) return;
    _researchPoll = Timer.periodic(const Duration(seconds: 15), (_) {
      if (_foreground && mounted) _chat?.refreshBackgroundResearch();
    });
  }

  void _stopResearchPolling() {
    _researchPoll?.cancel();
    _researchPoll = null;
  }

  /// Resolve the install id + active directory/model, then open THAT
  /// directory's conversation. Each directory is its own thread; the model is
  /// not part of the thread (switching models keeps the same conversation).
  Future<void> _bootstrap() async {
    _installId = await ref.read(sessionIdProvider.future);
    if (widget.command != 'shell') {
      setState(() => _sid = _sidFor(null)); // per-engine thread (persisted)
      if (_foreground) Push.activeSession = _sid;
      await _chat?.ensureLoaded(welcome: _welcome);
      _restoreDraft();
      if (_foreground) unawaited(_chat?.refreshBackgroundResearch());
      return;
    }
    final saved = await Storage.selectedConversation();
    final savedSession = widget.sessionId ?? saved.sessionId;
    _independentConversation =
        widget.sessionId != null ||
        (saved.sessionId != null &&
            saved.sessionId == savedSession &&
            saved.independent);
    _conversationProject =
        widget.project ??
        (savedSession == saved.sessionId ? saved.project : null);
    final api = ref.read(apiProvider);
    String? dir;
    var model = 'auto';
    if (api != null) {
      try {
        final projects = await api.projects();
        dir = projects['current_name']?.toString();
        _serverProject = dir;
      } catch (_) { /* retain the saved conversation context */ }
      try {
        final settings = await api.model(includeCatalog: false).timeout(const Duration(seconds: 3));
        model = settings['engine']?.toString() ?? 'auto';
      } catch (_) { /* projects remain usable if model settings fail */ }
    }
    if (savedSession != null && _conversationProject != null) {
      dir = _conversationProject;
    }
    if (!mounted) return;
    var session = savedSession;
    if (session == null && api != null) {
      try {
        final created = await api.createConversation(
          _installId!,
          'General',
          dir,
        );
        session = created['session_id']?.toString();
        _conversationProject = created['project']?.toString() ?? dir;
        if (session != null && session.isNotEmpty) {
          _independentConversation = true;
          await Storage.setSelectedConversation(
            session,
            _conversationProject,
            independent: true,
          );
        }
      } catch (_) {
        // Older servers keep opening the existing per-project thread.
      }
    }
    if (!mounted) return;
    setState(() {
      _dir = dir;
      _model = model;
      _sid = session ?? _sidFor(dir);
      if (_conversationProject == null) _conversationProject = dir;
    });
    if (_foreground) Push.activeSession = _sid;
    if (_independentConversation && _sid != null) {
      await Storage.setSelectedConversation(
        _sid!,
        _conversationProject ?? dir,
        independent: true,
      );
    }
    _chat?.setProjectContext(_conversationProject ?? dir);
    await _chat?.ensureLoaded(welcome: _welcome);
    _restoreDraft();
    if (_foreground) unawaited(_chat?.refreshBackgroundResearch());
    final pending = _pendingProject;
    _pendingProject = null;
    if (pending != null && mounted) {
      await _switchConversation(pending, openProjectChat: true, announce: false);
    }
  }

  /// Another screen explicitly opened a project's chat; show it here too.
  void _followProjectRequest(String dir) {
    if (!mounted) return;
    if (_sid == null) {
      _pendingProject = dir;
      return;
    }
    unawaited(_switchConversation(dir, openProjectChat: true, announce: false));
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
    return shellSessionId(_installId ?? '', dir);
  }

  /// The server owns the active project. If it changed — the agent used the
  /// projects tool mid-turn, or it moved while we were backgrounded — follow it
  /// so the header + thread key stay in sync and reopening lands on the right
  /// thread. [reload] pulls the destination thread's history (used after we've
  /// been away); mid-turn we keep the visible messages and just re-key + note it.
  Future<void> _syncWorkspace(String? name, {bool reload = false}) async {
    // Acknowledge the signal FIRST, on every path including the early returns.
    // A skill tab has no directory to follow, so it used to return here without
    // clearing — and build() then re-scheduled this call on every single frame.
    _chat?.consumeWorkspace();
    if (widget.command != 'shell' || name == null || name.isEmpty) return;
    if (_independentConversation) {
      setState(() {
        _dir = name;
        _conversationProject = name;
      });
      _chat?.setProjectContext(name);
      if (_sid != null) {
        unawaited(
          Storage.setSelectedConversation(_sid!, name, independent: true),
        );
      }
      return;
    }
    if (name == _dir) return;
    final sid = _sidFor(name);
    setState(() {
      _dir = name;
      _sid = sid;
    });
    if (_foreground) Push.activeSession = sid;
    _conversationProject = name;
    // Reopening the app must land on the thread we followed, not the old one.
    unawaited(Storage.setSelectedConversation(sid, name, independent: false));
    await _chat?.ensureLoaded(welcome: _welcome);
    _chat?.setProjectContext(name);
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
      final current = proj['current_name']?.toString();
      if (current == null || current == _serverProject) return;
      _serverProject = current;
      await _syncWorkspace(current, reload: true);
    } catch (_) {
      /* keep showing the current thread */
    }
  }

  /// Swap the visible conversation to another directory's thread.
  ///
  /// [openProjectChat] is an explicit pick: it leaves an independent
  /// conversation for the project's own thread. [announce] tells the other open
  /// chat screens to follow (false when this call is itself following one).
  Future<void> _switchConversation(
    String dir, {
    bool openProjectChat = false,
    bool announce = true,
  }) async {
    if (openProjectChat) _serverProject = dir;
    if (dir == _dir && (!openProjectChat || !_independentConversation)) return;
    if (_independentConversation && !openProjectChat) {
      setState(() {
        _dir = dir;
        _conversationProject = dir;
      });
      _chat?.setProjectContext(dir);
      if (_sid != null) {
        unawaited(
          Storage.setSelectedConversation(_sid!, dir, independent: true),
        );
      }
      return;
    }
    _chat?.setDraft(_input.text);
    final sid = _sidFor(dir);
    setState(() {
      _dir = dir;
      _sid = sid;
      _independentConversation = false;
      _conversationProject = dir;
      _replyTarget = null;
      _pending = null;
      _input.clear();
    });
    if (_foreground) Push.activeSession = sid;
    _chat?.consumeWorkspace();
    _chat?.setProjectContext(dir);
    if (announce && _installId != null) {
      await requestProjectChat(_installId!, dir);
    } else {
      await Storage.setSelectedConversation(sid, dir, independent: false);
    }
    await _chat?.ensureLoaded(welcome: _welcome);
    _restoreDraft();
  }

  Future<void> _openConversation(Map<String, dynamic> item) async {
    final session = item['session_id']?.toString();
    if (session == null || session.isEmpty) return;
    final project = item['project']?.toString();
    setState(() {
      _sid = session;
      _dir = project ?? _dir;
      _conversationProject = project ?? _dir;
      _independentConversation = item['legacy'] != true;
      _replyTarget = null;
    });
    if (_foreground) Push.activeSession = session;
    await Storage.setSelectedConversation(
      session,
      _conversationProject,
      independent: _independentConversation,
    );
    _chat?.setProjectContext(project ?? _dir);
    await _chat?.ensureLoaded(welcome: _welcome);
    _restoreDraft();
  }

  Future<void> _newConversation() async {
    final api = ref.read(apiProvider);
    if (api == null || _installId == null) return;
    final title = await showDialog<String>(
      context: context,
      builder: (context) {
        final controller = TextEditingController(text: 'New conversation');
        return AlertDialog(
          title: const Text('New conversation'),
          content: TextField(
            controller: controller,
            autofocus: true,
            decoration: const InputDecoration(labelText: 'Name'),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, controller.text.trim()),
              child: const Text('Create'),
            ),
          ],
        );
      },
    );
    if (title == null || title.isEmpty || !mounted) return;
    try {
      final created = await api.createConversation(_installId!, title, _dir);
      await _openConversation({...created, 'legacy': false});
    } catch (e) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not create conversation: $e')),
        );
    }
  }

  Future<void> _chooseConversation() async {
    final api = ref.read(apiProvider);
    if (api == null || _installId == null) return;
    try {
      final items = await api.conversations(_installId!);
      if (!mounted) return;
      final chosen = await showModalBottomSheet<Map<String, dynamic>>(
        context: context,
        builder: (context) => SafeArea(
          child: ListView(
            shrinkWrap: true,
            children: [
              for (final item in items)
                ListTile(
                  title: Text(item['title']?.toString() ?? 'Conversation'),
                  subtitle: Text(item['project']?.toString() ?? 'No project'),
                  onTap: () => Navigator.pop(context, item),
                ),
            ],
          ),
        ),
      );
      if (chosen != null) await _openConversation(chosen);
    } catch (e) {
      if (mounted)
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Could not load conversations: $e')),
        );
    }
  }

  /// Confirm-to-move: switch to [dir] (server workspace + thread), then re-ask
  /// the question there. Wired to the "Ask in {dir} chat" action on a move reply.
  Future<void> _moveAndAsk(String dir) async {
    final prompt = _lastUserText;
    final api = ref.read(apiProvider);
    if (api != null) {
      try {
        await api.switchProject(dir); // keep server workspace in lock-step
      } catch (_) {}
    }
    await _switchConversation(dir, openProjectChat: true);
    if (prompt != null && prompt.isNotEmpty) {
      _input.text = prompt;
      await _send();
    }
  }

  @override
  void dispose() {
    _projectRequests?.cancel();
    _stopResearchPolling();
    WidgetsBinding.instance.removeObserver(this);
    if (Push.activeSession == _sid) Push.activeSession = null;
    _inputFocus.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // Only "viewing" this chat while it's on top AND the app is foregrounded.
    _appResumed = state == AppLifecycleState.resumed;
    _foreground = _appResumed && widget.active;
    if (state == AppLifecycleState.resumed) {
      if (_foreground) {
        _startResearchPolling();
        _chat?.refreshBackgroundResearch();
        Push.activeSession = _sid;
        _refreshWorkspace(); // catch a project switch made while we were away
        _chat?.replayOutbox(); // the connection may be back
      }
    } else {
      _stopResearchPolling();
      if (Push.activeSession == _sid) Push.activeSession = null;
    }
  }

  /// Load a specific conversation's history (per-directory), rebuilding any
  /// images, and show a welcome line if the thread is empty.
  Future<void> _pickImage() async {
    try {
      final x = await ImagePicker().pickImage(
        source: ImageSource.gallery,
        maxWidth: 2000,
        imageQuality: 85,
      );
      if (x != null) setState(() => _pending = x);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Could not pick image: $e')));
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
    final spoken = _dictated;
    _dictated = false;
    setState(() => _pending = null);
    _input.clear();
    chat.setDraft('');
    final key = _key!;
    final wasBusy = ref.read(chatControllerProvider(key)).sending;
    final before = ref.read(chatControllerProvider(key)).messages.length;
    final reply = _replyTarget;
    setState(() => _replyTarget = null);
    await chat.send(
      text,
      imagePath: attach?.path,
      replyToMessageId: reply?.messageId,
      replyToContent: reply?.text,
      replyToRole: reply?.role,
    );
    if (!spoken || wasBusy || !await Voice.instance.speakReplies()) return;
    final msgs = ref.read(chatControllerProvider(key)).messages.skip(before);
    final replies = msgs.where((m) => m.role == 'bot');
    if (replies.isNotEmpty) await Voice.instance.speak(replies.last.text);
  }

  /// A choice tapped on a question card is sent as the next message.
  Future<void> _answer(String text) async {
    _input.text = text;
    await _send();
  }

  void _replyTo(ChatMessage message) {
    if (message.messageId == null) return;
    setState(() => _replyTarget = message);
    _inputFocus.requestFocus();
  }

  void _jumpToReply(int? id) {
    if (id == null) return;
    final key = _messageKeys[id];
    final ctx = key?.currentContext;
    if (ctx != null)
      Scrollable.ensureVisible(
        ctx,
        duration: const Duration(milliseconds: 250),
        alignment: .35,
      );
  }

  /// Review the project's uncommitted changes; lines sent "to chat" land in
  /// the composer so they can be edited before sending.
  Future<void> _reviewChanges() async {
    final snippet = await Navigator.of(context).push<String>(
      MaterialPageRoute(builder: (_) => DiffScreen(project: _dir)),
    );
    if (snippet == null || !mounted) return;
    final current = _input.text.trimRight();
    _input.text = current.isEmpty ? snippet : '$current\n\n$snippet';
    _input.selection = TextSelection.collapsed(offset: _input.text.length);
    _chat?.setDraft(_input.text);
  }

  /// Mic in the composer: dictate into the text box so it can be checked
  /// before sending. Tapping again stops early.
  Future<void> _dictate() async {
    if (_dictating) {
      await Voice.instance.stopListening();
      return;
    }
    final prefix = _input.text.trim().isEmpty ? '' : '${_input.text.trim()} ';
    setState(() => _dictating = true);
    try {
      final heard = await Voice.instance.listen(
        onPartial: (p) {
          if (mounted) _input.text = '$prefix$p';
        },
      );
      if (!mounted) return;
      _input.text = '$prefix$heard'.trim();
      _input.selection = TextSelection.collapsed(offset: _input.text.length);
      _chat?.setDraft(_input.text);
      if (heard.isNotEmpty) _dictated = true;
    } on VoiceUnavailable catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) setState(() => _dictating = false);
    }
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
        _scroll.animateTo(
          target,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        );
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

  static String _modelLabel(String e) =>
      e == 'auto' ? 'auto' : e[0].toUpperCase() + e.substring(1);

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
          Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(widget.title),
              const SizedBox(width: 3),
              const Icon(Icons.expand_more, size: 18),
            ],
          ),
          Text(
            _dir == null
                ? 'tap to set context'
                : '$_dir · ${_modelLabel(_model)}',
            style: TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.w400,
              color: context.pal.textDim,
            ),
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
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (_) => ChatContextSheet(
        api: api,
        currentDir: _dir,
        currentModel: _model,
        onChanged: (dir, model) async {
          if (!mounted) return;
          if (model != null) setState(() => _model = model);
          if (dir != null) {
            // Switching directory swaps the whole conversation to that thread.
            await _switchConversation(dir, openProjectChat: true);
            try {
              final settings = await api.model(includeCatalog: false).timeout(const Duration(seconds: 3));
              if (mounted) setState(() => _model = settings['engine']?.toString() ?? 'auto');
            } catch (_) { /* history switching does not depend on model settings */ }
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
    final lastBot = msgs.lastIndexWhere((m) => m.role == 'bot');
    final answered =
        lastBot >= 0 &&
        msgs
            .skip(lastBot + 1)
            .any((m) => const {'user', 'queued', 'outbox'}.contains(m.role));
    final openQuestion = lastBot >= 0 && msgs[lastBot].ask != null && !answered
        ? lastBot
        : -1;

    // Auto-scroll only when something new arrives (not on every rebuild).
    if (msgs.length != _lastMsgCount) {
      _lastMsgCount = msgs.length;
      _scrollEnd();
    }
    // Follow a project switch the agent made during a turn. `workspace` is a
    // one-shot that _syncWorkspace consumes, so this cannot re-arm itself; the
    // guard below is the second line of defence against a rebuild storm.
    if (chat.workspace != null && !_followingWorkspace) {
      _followingWorkspace = true;
      final target = chat.workspace;
      WidgetsBinding.instance.addPostFrameCallback((_) async {
        try {
          await _syncWorkspace(target);
        } finally {
          if (mounted) _followingWorkspace = false;
        }
      });
    }

    return Scaffold(
      appBar: AppBar(
        title: _buildTitle(context),
        actions: [
          if (widget.command == 'shell')
            IconButton(
              tooltip: 'Conversations',
              icon: const Icon(Icons.forum_outlined),
              onPressed: _chooseConversation,
            ),
          if (widget.command == 'shell')
            IconButton(
              tooltip: 'New conversation',
              icon: const Icon(Icons.add_comment_outlined),
              onPressed: _newConversation,
            ),
          if (widget.command == 'shell')
            IconButton(
              tooltip: 'Review changes',
              icon: const Icon(Icons.difference_outlined),
              onPressed: _reviewChanges,
            ),
          if (widget.command == 'shell')
            IconButton(
              tooltip: 'Voice mode',
              icon: const Icon(Icons.graphic_eq),
              onPressed: () => showVoiceSheet(context, _key),
            ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              controller: _scroll,
              padding: const EdgeInsets.all(12),
              itemCount: msgs.length,
              itemBuilder: (_, i) => _Bubble(
                msgs[i],
                imgHeaders,
                api: ref.read(apiProvider),
                onMove: sending ? null : _moveAndAsk,
                // Only the newest unanswered question can be answered.
                onAnswer: i == openQuestion && !sending ? _answer : null,
                onOther: i == openQuestion && !sending
                    ? () => _inputFocus.requestFocus()
                    : null,
                onReply: msgs[i].messageId == null
                    ? null
                    : () => _replyTo(msgs[i]),
                onJump: msgs[i].replyToMessageId == null
                    ? null
                    : () => _jumpToReply(msgs[i].replyToMessageId),
                key: msgs[i].messageId == null
                    ? null
                    : (_messageKeys[msgs[i].messageId!] ??= GlobalKey()),
              ),
            ),
          ),
          if (chat.backgroundResearch.isNotEmpty)
            ConstrainedBox(
              constraints: const BoxConstraints(maxHeight: 160),
              child: SingleChildScrollView(
                child: Column(
                  children: [
                    for (final job in chat.backgroundResearch)
                      _WorkCard(
                        job,
                        onStop: () async {
                          try {
                            await _chat?.cancelBackgroundResearch(job.id);
                          } catch (_) {
                            if (mounted)
                              ScaffoldMessenger.of(context).showSnackBar(
                                const SnackBar(
                                  content: Text(
                                    'Could not stop research. Try again when connected.',
                                  ),
                                ),
                              );
                          }
                        },
                      ),
                  ],
                ),
              ),
            ),
          if (widget.command == 'shell' && chat.work != null)
            _WorkCard(
              chat.work!,
              onStop: chat.work!.isActive && sending
                  ? () => _chat?.stopWork()
                  : null,
            ),
          ChatComposer(
            controller: _input,
            focusNode: _inputFocus,
            sending: sending,
            dictating: _dictating,
            replyPreview: _replyTarget == null ? null : _replyTarget!.text,
            replySender: _replyTarget?.role == 'user' ? 'You' : 'Gajala',
            onCancelReply: () => setState(() => _replyTarget = null),
            attachmentPreview: _pending == null
                ? null
                : Stack(
                    clipBehavior: Clip.none,
                    children: [
                      ClipRRect(
                        borderRadius: BorderRadius.circular(10),
                        child: Image.file(
                          File(_pending!.path),
                          width: 64,
                          height: 64,
                          fit: BoxFit.cover,
                        ),
                      ),
                      Positioned(
                        top: -8,
                        right: -8,
                        child: GestureDetector(
                          onTap: () => setState(() => _pending = null),
                          child: CircleAvatar(
                            radius: 11,
                            backgroundColor: context.pal.surfaceAlt,
                            child: Icon(
                              Icons.close,
                              size: 14,
                              color: context.pal.text,
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
            onAddPhoto: () => _pickImage(),
            onDictate: _dictate,
            onSend: _send,
            onDraftChanged: (text) => _chat?.setDraft(text),
          ),
        ],
      ),
    );
  }
}

class _WorkCard extends StatelessWidget {
  final AssistantWork work;
  final VoidCallback? onStop;
  const _WorkCard(this.work, {this.onStop});

  @override
  Widget build(BuildContext context) {
    final failed = work.status == 'failed' || work.status == 'recovering';
    final detail = work.blocker.isNotEmpty
        ? work.blocker
        : (work.nextAction.isNotEmpty ? work.nextAction : work.summary);
    final color = failed ? GajalaColors.danger : GajalaColors.accent;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.fromLTRB(10, 4, 10, 4),
      padding: const EdgeInsets.fromLTRB(12, 9, 8, 9),
      decoration: BoxDecoration(
        color: color.withValues(alpha: .10),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withValues(alpha: .35)),
      ),
      child: Row(
        children: [
          Icon(
            work.status == 'completed'
                ? Icons.check_circle_outline
                : failed
                ? Icons.error_outline
                : Icons.pending_outlined,
            size: 18,
            color: color,
          ),
          const SizedBox(width: 9),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${work.command == 'research' ? 'RESEARCH · ' : ''}${work.status.replaceAll('_', ' ').toUpperCase()}',
                  style: TextStyle(
                    fontSize: 10.5,
                    fontWeight: FontWeight.w700,
                    color: color,
                  ),
                ),
                if (detail.isNotEmpty)
                  Text(
                    detail,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: TextStyle(fontSize: 12, color: context.pal.textDim),
                  ),
              ],
            ),
          ),
          if (onStop != null)
            TextButton(onPressed: onStop, child: const Text('Stop')),
        ],
      ),
    );
  }
}

class _Bubble extends StatelessWidget {
  final ChatMessage m;
  final GajalaApi? api;
  final Map<String, String>? imgHeaders; // auth headers for /api/file images
  final void Function(String dir)? onMove; // confirm-to-move action
  final void Function(String answer)? onAnswer; // tap a question-card choice
  final VoidCallback? onOther;
  final VoidCallback? onReply;
  final VoidCallback? onJump;
  const _Bubble(
    this.m,
    this.imgHeaders, {
    this.onMove,
    this.api,
    this.onAnswer,
    this.onOther,
    this.onReply,
    this.onJump,
    super.key,
  });
  @override
  Widget build(BuildContext context) {
    if (m.role == 'status') {
      // Once steps start arriving, the live bubble IS the trace widget — so the
      // thing you watch is the thing that stays under the reply afterwards.
      if (m.steps.isNotEmpty) {
        return RunTraceStrip(steps: m.steps, project: m.project, live: true);
      }
      return _StatusBubble(m.text);
    }
    // Typed while a turn was running (queued), or could not reach the Mac yet
    // (outbox) — both are kept and sent automatically.
    if (m.role == 'queued' || m.role == 'outbox') {
      final offline = m.role == 'outbox';
      return Align(
        alignment: Alignment.centerRight,
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 4),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          constraints: const BoxConstraints(maxWidth: 300),
          decoration: BoxDecoration(
            color: GajalaColors.userBubble.withValues(alpha: .35),
            borderRadius: BorderRadius.circular(14),
            border: Border.all(
              color: context.pal.textDim.withValues(alpha: .4),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              if (m.replyToContent?.isNotEmpty == true)
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: QuotedReply(
                    sender: m.replyToRole == 'user' ? 'You' : 'Gajala',
                    text: m.replyToContent!,
                    onTap: onJump,
                  ),
                ),
              Text(
                m.text,
                style: TextStyle(
                  color: context.pal.text.withValues(alpha: .75),
                ),
              ),
              const SizedBox(height: 3),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    offline ? Icons.cloud_off : Icons.schedule,
                    size: 11,
                    color: context.pal.textDim,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    offline
                        ? 'waiting for connection · sends automatically'
                        : 'queued',
                    style: TextStyle(
                      fontSize: 10.5,
                      color: context.pal.textDim,
                    ),
                  ),
                ],
              ),
            ],
          ),
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
            child: Text(
              m.text,
              style: TextStyle(fontSize: 11.5, color: context.pal.textDim),
            ),
          ),
        ),
      );
    }
    final isUser = m.role == 'user';
    final isError = m.role == 'error';
    final presentation = replyPresentation(m);
    final hasText = presentation.body.trim().isNotEmpty;
    final radius = BorderRadius.only(
      topLeft: const Radius.circular(16),
      topRight: const Radius.circular(16),
      bottomLeft: Radius.circular(isUser ? 16 : 4),
      bottomRight: Radius.circular(isUser ? 4 : 16),
    );
    final bubble = Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * .82,
        ),
        decoration: BoxDecoration(
          color: isUser ? GajalaColors.userBubble : context.pal.botBubble,
          borderRadius: radius,
          border: isError ? Border.all(color: GajalaColors.danger) : null,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            if (presentation.quote?.isNotEmpty == true) ...[
              QuotedReply(
                sender: presentation.sender,
                text: presentation.quote!,
                onTap: onJump,
              ),
              const SizedBox(height: 8),
            ],
            // Image the user attached (rendered from the local file).
            if (m.localImage != null)
              Padding(
                padding: EdgeInsets.only(bottom: hasText ? 8 : 0),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(10),
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 320),
                    child: Image.file(
                      File(m.localImage!),
                      fit: BoxFit.contain,
                      errorBuilder: (_, _, _) => const SizedBox(),
                    ),
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
                    child: Image.network(
                      url,
                      headers: imgHeaders,
                      fit: BoxFit.contain,
                      loadingBuilder: (c, w, p) => p == null
                          ? w
                          : const SizedBox(
                              height: 120,
                              child: Center(
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              ),
                            ),
                      errorBuilder: (_, _, _) => Text(
                        '[image unavailable]',
                        style: TextStyle(color: context.pal.textDim),
                      ),
                    ),
                  ),
                ),
              ),
            if (hasText)
              ChatContent(
                text: presentation.body,
                api: isUser ? null : api,
                style: TextStyle(
                  color: isError ? GajalaColors.danger : context.pal.text,
                  height: 1.35,
                ),
              ),
            if (m.moveTo != null && onMove != null) ...[
              const SizedBox(height: 8),
              TextButton.icon(
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 2,
                  ),
                  backgroundColor: GajalaColors.accent.withValues(alpha: 0.15),
                  foregroundColor: GajalaColors.accent,
                  visualDensity: VisualDensity.compact,
                ),
                icon: const Icon(Icons.arrow_forward, size: 16),
                label: Text('Ask in ${m.moveTo} chat'),
                onPressed: () => onMove!(m.moveTo!),
              ),
            ],
            if (m.ask != null) ...[
              const SizedBox(height: 10),
              _AskCardView(m.ask!, onAnswer: onAnswer, onOther: onOther),
            ],
          ],
        ),
      ),
    );

    // What the agent did to produce this reply, collapsed under it. Steps we
    // already have (the turn just ran) render immediately; a reply restored from
    // history carries only its run id and fetches on demand.
    final messageContent = bubble;
    final hasCodeBlock = presentation.body.contains('```');
    final decorated = onReply == null
        ? messageContent
        : hasCodeBlock
        ? Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Align(
                alignment: isUser
                    ? Alignment.centerRight
                    : Alignment.centerLeft,
                child: IconButton(
                  tooltip: 'Reply to message',
                  visualDensity: VisualDensity.compact,
                  icon: const Icon(Icons.reply_rounded, size: 18),
                  onPressed: onReply,
                ),
              ),
              messageContent,
            ],
          )
        : SwipeReply(onReply: onReply!, child: messageContent);
    if (isUser) return decorated;
    if (m.steps.isNotEmpty) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          decorated,
          RunTraceStrip(
            steps: m.steps,
            project: m.project,
            stopLabel: m.stopLabel,
            hitStepLimit: m.hitStepLimit,
          ),
        ],
      );
    }
    if (m.runId != null && m.runId!.isNotEmpty) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [decorated, _LazyTrace(m.runId!)],
      );
    }
    return decorated;
  }
}

/// A reply restored from chat history knows its run id but not its steps.
/// Fetches the trace the first time you open it, so scrolling old history stays
/// cheap.
/// The agent's multiple-choice question. Single choice sends on tap; multi
/// choice collects ticks and sends them together. Disabled once answered.
class _AskCardView extends StatefulWidget {
  final AskCard card;
  final void Function(String answer)? onAnswer;
  final VoidCallback? onOther;
  const _AskCardView(this.card, {this.onAnswer, this.onOther});
  @override
  State<_AskCardView> createState() => _AskCardViewState();
}

class _AskCardViewState extends State<_AskCardView> {
  final Set<String> _picked = {};

  @override
  Widget build(BuildContext context) {
    final card = widget.card;
    final live = widget.onAnswer != null;
    return Container(
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: GajalaColors.accent.withValues(alpha: live ? .10 : .04),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(
          color: GajalaColors.accent.withValues(alpha: live ? .45 : .15),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            card.question,
            style: const TextStyle(fontWeight: FontWeight.w600),
          ),
          if (card.multi && live)
            Text(
              'Pick any, then Send',
              style: TextStyle(fontSize: 11, color: context.pal.textDim),
            ),
          const SizedBox(height: 6),
          Wrap(
            spacing: 6,
            runSpacing: 6,
            children: [
              for (final o in card.options)
                card.multi
                    ? FilterChip(
                        label: Text(o),
                        selected: _picked.contains(o),
                        onSelected: live
                            ? (v) => setState(
                                () => v ? _picked.add(o) : _picked.remove(o),
                              )
                            : null,
                      )
                    : ActionChip(
                        label: Text(o),
                        onPressed: live ? () => widget.onAnswer!(o) : null,
                      ),
              if (live)
                ActionChip(
                  avatar: const Icon(Icons.edit_outlined, size: 16),
                  label: const Text('Other…'),
                  onPressed: widget.onOther,
                ),
            ],
          ),
          if (card.multi && live) ...[
            const SizedBox(height: 6),
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton(
                onPressed: _picked.isEmpty
                    ? null
                    : () => widget.onAnswer!(
                        [
                          for (final o in card.options)
                            if (_picked.contains(o)) o,
                        ].join(', '),
                      ),
                child: const Text('Send'),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _LazyTrace extends ConsumerStatefulWidget {
  final String runId;
  const _LazyTrace(this.runId);
  @override
  ConsumerState<_LazyTrace> createState() => _LazyTraceState();
}

class _LazyTraceState extends ConsumerState<_LazyTrace> {
  RunTrace? _trace;
  bool _loading = false;
  bool _failed = false;

  Future<void> _load() async {
    if (_loading || _trace != null) return;
    setState(() => _loading = true);
    try {
      final api = ref.read(apiProvider);
      final t = await api?.runTrace(widget.runId);
      if (mounted) setState(() => _trace = t);
    } catch (_) {
      if (mounted) setState(() => _failed = true);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_trace != null) return RunTraceStrip.fromTrace(_trace!);
    if (_failed) return const SizedBox.shrink();
    return Align(
      alignment: Alignment.centerLeft,
      child: TextButton.icon(
        onPressed: _load,
        icon: _loading
            ? const SizedBox(
                width: 12,
                height: 12,
                child: CircularProgressIndicator(strokeWidth: 2),
              )
            : const Icon(Icons.chevron_right, size: 16),
        label: const Text('What it did'),
        style: TextButton.styleFrom(
          foregroundColor: context.pal.textDim,
          visualDensity: VisualDensity.compact,
          textStyle: const TextStyle(fontSize: 12.5),
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
        constraints: BoxConstraints(
          maxWidth: MediaQuery.of(context).size.width * .82,
        ),
        decoration: BoxDecoration(
          color: context.pal.botBubble,
          borderRadius: const BorderRadius.only(
            topLeft: Radius.circular(16),
            topRight: Radius.circular(16),
            bottomLeft: Radius.circular(4),
            bottomRight: Radius.circular(16),
          ),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.only(top: 3, right: 10),
              child: SizedBox(
                width: 13,
                height: 13,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: context.pal.textDim,
                ),
              ),
            ),
            Flexible(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final l in lines)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 1),
                      child: Text(
                        l,
                        style: TextStyle(
                          color: context.pal.textDim,
                          fontSize: 13,
                          fontStyle: FontStyle.italic,
                          height: 1.3,
                        ),
                      ),
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

/// Bottom sheet to switch the active directory and pin the coding model, plus a
/// "continue on Mac" handoff for the active Claude session.
class ChatContextSheet extends StatefulWidget {
  final GajalaApi api;
  final String? currentDir;
  final String currentModel;
  final Future<void> Function(String? dir, String? model) onChanged;
  const ChatContextSheet({
    super.key,
    required this.api,
    required this.currentDir,
    required this.currentModel,
    required this.onChanged,
  });
  @override
  State<ChatContextSheet> createState() => ChatContextSheetState();
}

class ChatContextSheetState extends State<ChatContextSheet> {
  List<Map<String, dynamic>> _projects = [];
  List<Map<String, dynamic>> _sessions = [];
  String? _dir;
  late String _model;
  Map<String, String> _engineModels =
      {}; // engine → pinned model ('' = default)
  Map<String, String> _engineBackups = {}; // engine → backup model ('' = none)
  Map<String, dynamic> _catalogs = {};
  String? _catalogError;
  Map<String, List<String>> _presets = {}; // engine → selectable models
  List<String> _engines = const ['auto', 'claude', 'codex', 'gemini', 'qwen'];
  bool _busy = false;
  bool _projectsLoading = true;
  bool _modelsLoading = false;
  String? _projectsError;
  int _sessionLoad = 0;

  @override
  void initState() {
    super.initState();
    _dir = widget.currentDir;
    _model = widget.currentModel;
    _load();
  }

  Future<void> _load() async {
    await Future.wait([_loadProjects(), _loadSessions(), _loadModels()]);
  }

  Future<void> _loadProjects() async {
    if (mounted) setState(() { _projectsLoading = true; _projectsError = null; });
    try {
      final proj = await widget.api.projects();
      if (!mounted) return;
      setState(() {
        _projects = List<Map<String, dynamic>>.from(proj['projects'] ?? []);
        _dir ??= proj['current_name']?.toString();
      });
    } catch (_) {
      if (mounted) setState(() => _projectsError = 'Could not load projects.');
    } finally {
      if (mounted) setState(() => _projectsLoading = false);
    }
  }

  Future<void> _loadSessions() async {
    final request = ++_sessionLoad;
    try {
      final sess = await widget.api.activeSessions();
      if (!mounted || request != _sessionLoad) return;
      setState(() => _sessions = List<Map<String, dynamic>>.from(sess['sessions'] ?? []));
    } catch (_) { /* session discovery cannot hide project choices */ }
  }

  Future<void> _loadModels({bool refresh = false}) async {
    if (mounted) setState(() => _modelsLoading = true);
    try {
      final mdl = await widget.api.model(refresh: refresh);
      if (!mounted) return;
      setState(() {
        _engineModels =
            (mdl['models'] as Map?)?.map(
              (k, v) => MapEntry(k.toString(), (v ?? '').toString()),
            ) ??
            {};
        _engineBackups =
            (mdl['backup_models'] as Map?)?.map(
              (k, v) => MapEntry(k.toString(), (v ?? '').toString()),
            ) ??
            {};
        _engines =
            (mdl['options'] as List?)?.map((e) => e.toString()).toList() ??
            _engines;
        _catalogError = null;
        _catalogs = Map<String, dynamic>.from(mdl['catalogs'] ?? {});
        _presets =
            (mdl['presets'] as Map?)?.map(
              (k, v) =>
                  MapEntry(k.toString(), List<String>.from(v ?? const [])),
            ) ??
            {};
      });
    } catch (_) {
      if (mounted) setState(() => _catalogError = 'Could not load model choices.');
    } finally {
      if (mounted) setState(() => _modelsLoading = false);
    }
  }

  String _catalogLabel(String id) {
    final rows = (_catalogs[_model] as Map?)?['models'] as List? ?? [];
    for (final row in rows) {
      if (row['id'] == id) {
        return row['resolved_model']?.toString() ?? row['label']?.toString() ?? id;
      }
    }
    return id;
  }

  /// Pin a model for the currently selected engine.
  Future<void> _pinEngineModel(String engine, String model) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final now = await widget.api.setEngineModel(engine, model);
      if (mounted) {
        setState(
          () => _engineModels = now.map(
            (k, v) => MapEntry(k.toString(), (v ?? '').toString()),
          ),
        );
      }
    } catch (_) {
    } finally {
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
        setState(
          () => _engineBackups = now.map(
            (k, v) => MapEntry(k.toString(), (v ?? '').toString()),
          ),
        );
      }
    } catch (_) {
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _switchDir(String name) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final now = await widget.api.switchProject(name);
      await widget.onChanged(now, null);
      if (!mounted) return;
      setState(() => _dir = now);
      Navigator.of(context).pop();
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not switch project. Please retry.')));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _pinModel(String engine) async {
    if (_busy || engine == _model) return;
    setState(() => _busy = true);
    try {
      final now = await widget.api.setModel(engine);
      await widget.onChanged(null, now);
      if (mounted) setState(() => _model = now);
    } catch (_) {
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    final resumable = _sessions.where((s) => s['resume_cmd'] != null).toList();
    final engineLabel = _ChatScreenState._modelLabel(_model);
    final catalog = _catalogs[_model] is Map ? _catalogs[_model] as Map : null;
    final status = _catalogError ??
        (catalog?['stale'] == true
            ? (catalog?['error'] ?? 'Model list may be out of date.').toString()
            : null);
    return SafeArea(
      child: Padding(
        padding: EdgeInsets.only(
          left: 16,
          right: 16,
          top: 14,
          bottom: 16 + MediaQuery.of(context).viewInsets.bottom,
        ),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            mainAxisSize: MainAxisSize.min,
            children: [
              Center(
                child: Container(
                  width: 36,
                  height: 4,
                  margin: const EdgeInsets.only(bottom: 14),
                  decoration: BoxDecoration(
                    color: pal.textDim,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              _projectField(pal),
              const SizedBox(height: 14),
              _dropdown<String>(
                key: 'engine-$_model',
                label: 'Coding engine',
                value: _model,
                items: {
                  for (final e in _engines) e: _ChatScreenState._modelLabel(e),
                },
                onChanged: _pinModel,
                helper: _model == 'auto' ? 'Gajala picks per request' : null,
              ),
              if (_model != 'auto') ...[
                const SizedBox(height: 14),
                Row(
                  children: [
                    Expanded(
                      child: _dropdown<String>(
                        key: 'model-$_model-${_engineModels[_model]}',
                        label: '$engineLabel model',
                        value: _engineModels[_model] ?? '',
                        items: _modelChoices(_engineModels[_model], 'Default'),
                        onChanged: (m) => _pinEngineModel(_model, m),
                      ),
                    ),
                    IconButton(
                      tooltip: 'Refresh model list',
                      onPressed: _modelsLoading
                          ? null
                          : () => _loadModels(refresh: true),
                      icon: _modelsLoading
                          ? const SizedBox(
                              width: 18,
                              height: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                          : const Icon(Icons.refresh, size: 20),
                    ),
                  ],
                ),
                const SizedBox(height: 14),
                _dropdown<String>(
                  key: 'backup-$_model-${_engineBackups[_model]}',
                  label: 'Backup if it fails',
                  value: _engineBackups[_model] ?? '',
                  items: _modelChoices(_engineBackups[_model], 'None'),
                  onChanged: (m) => _pinEngineBackup(_model, m),
                ),
              ],
              if (status != null) ...[
                const SizedBox(height: 8),
                Text(status, style: TextStyle(fontSize: 11.5, color: pal.textDim)),
              ],
              if (resumable.isNotEmpty)
                Theme(
                  data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
                  child: ExpansionTile(
                    tilePadding: EdgeInsets.zero,
                    childrenPadding: EdgeInsets.zero,
                    title: Text(
                      'Continue on Mac',
                      style: TextStyle(fontSize: 13, color: pal.textDim),
                    ),
                    children: [
                      for (final s in resumable)
                        ListTile(
                          dense: true,
                          contentPadding: EdgeInsets.zero,
                          title: Text(
                            s['resume_cmd'].toString(),
                            style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
                          ),
                          trailing: Icon(Icons.copy, size: 16, color: pal.textDim),
                          onTap: () {
                            Clipboard.setData(
                              ClipboardData(text: s['resume_cmd'].toString()),
                            );
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(content: Text('Copied ${s['engine']} resume command')),
                            );
                          },
                        ),
                    ],
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  /// Project dropdown, or its loading / error state. Picking one opens that
  /// project's chat and closes the sheet.
  Widget _projectField(Pal pal) {
    if (_projectsLoading) {
      return const Padding(
        padding: EdgeInsets.all(12),
        child: Text('Loading projects…'),
      );
    }
    if (_projectsError != null) {
      return Row(
        children: [
          Expanded(child: Text(_projectsError!)),
          TextButton(onPressed: _loadProjects, child: const Text('Retry projects')),
        ],
      );
    }
    if (_projects.isEmpty) {
      return Text('No projects found.', style: TextStyle(color: pal.textDim));
    }
    final names = [
      for (final p in _projects)
        if ((p['name']?.toString() ?? '').isNotEmpty) p['name'].toString(),
    ];
    if (_dir != null && !names.contains(_dir)) names.insert(0, _dir!);
    return _dropdown<String>(
      key: 'project-$_dir',
      label: 'Project',
      value: _dir,
      items: {for (final n in names) n: n},
      // Re-picking the current project still opens its chat.
      onChanged: _switchDir,
      allowSame: true,
    );
  }

  /// Model choices for the selected engine: the first entry ('' = [blank])
  /// plus the live catalog, keeping a saved model that dropped out of it.
  Map<String, String> _modelChoices(String? saved, String blank) {
    final listed = _presets[_model] ?? const <String>[];
    return {
      '': blank,
      for (final m in listed)
        if (m != 'default' && m != 'none') m: _catalogLabel(m),
      if (saved != null && saved.isNotEmpty && !listed.contains(saved))
        saved: '$saved (not in current list)',
    };
  }

  Widget _dropdown<T>({
    required String key,
    required String label,
    required T? value,
    required Map<T, String> items,
    required Future<void> Function(T) onChanged,
    String? helper,
    bool allowSame = false,
  }) {
    return DropdownButtonFormField<T>(
      key: ValueKey(key),
      initialValue: items.containsKey(value) ? value : null,
      isExpanded: true,
      menuMaxHeight: MediaQuery.of(context).size.height * 0.5,
      decoration: InputDecoration(
        labelText: label,
        helperText: helper,
        border: const OutlineInputBorder(),
        isDense: true,
      ),
      items: [
        for (final e in items.entries)
          DropdownMenuItem(
            value: e.key,
            child: Text(e.value, overflow: TextOverflow.ellipsis),
          ),
      ],
      onChanged: _busy
          ? null
          : (v) {
              if (v != null && (allowSame || v != value)) onChanged(v);
            },
    );
  }
}
