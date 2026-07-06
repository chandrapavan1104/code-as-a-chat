import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:image_picker/image_picker.dart';
import '../core/api.dart';
import '../core/models.dart';
import '../core/push.dart';
import '../core/state.dart';
import '../core/theme.dart';

/// Pulls "[image: /path]" markers out of an agent reply, returning the cleaned
/// text plus the full /api/file URLs to render.
final _imgMarker = RegExp(r'\[image:\s*([^\]]+?)\s*\]');
(String, List<String>) _splitImages(String raw, GajalaApi api) {
  final urls = <String>[];
  final clean = raw.replaceAllMapped(_imgMarker, (m) {
    urls.add(api.fileUrl(m.group(1)!.trim()));
    return '';
  }).trim();
  return (clean, urls);
}

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
  final List<ChatMessage> _msgs = [];
  bool _sending = false;
  String? _sid;
  XFile? _pending;   // image picked but not yet sent
  String? _dir;               // active project name (for the header)
  String _model = 'auto';     // pinned coding engine

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _initSession();
    _loadHistory();
    if (widget.command == 'shell') _loadContext();
  }

  /// Load the active directory + pinned model for the header.
  Future<void> _loadContext() async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    try {
      final results = await Future.wait([api.projects(), api.model()]);
      if (!mounted) return;
      setState(() {
        _dir = results[0]['current_name']?.toString();
        _model = results[1]['engine']?.toString() ?? 'auto';
      });
    } catch (_) {/* header just stays blank */}
  }

  Future<void> _initSession() async {
    _sid = await ref.read(sessionIdProvider.future);
    // Mark this chat as "being viewed" so completion pushes for it are muted.
    Push.activeSession = _sid;
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
    } else if (Push.activeSession == _sid) {
      Push.activeSession = null;
    }
  }

  Future<void> _loadHistory() async {
    final api = ref.read(apiProvider);
    if (api != null && widget.command == 'shell') {
      try {
        final sid = await ref.read(sessionIdProvider.future);
        final history = await api.chatHistory(sid);
        if (mounted && history.isNotEmpty) {
          // Rebuild any images the agent sent from their stored [image:] markers.
          final rebuilt = history.map((m) {
            if (m.role != 'bot') return m;
            final (clean, urls) = _splitImages(m.text, api);
            if (urls.isEmpty) return m;
            return ChatMessage('bot', clean, remoteImages: urls);
          }).toList();
          setState(() => _msgs.addAll(rebuilt));
          _scrollEnd();
          return;
        }
      } catch (_) { /* fall through to welcome */ }
    }
    if (mounted && _msgs.isEmpty) {
      setState(() => _msgs.add(ChatMessage('bot',
          widget.command == 'shell'
              ? 'Em sangathi mava! Gajala ikkada 🔥\nCheppu — em kavali?'
              : 'Send a /${widget.command} request, or just type.')));
    }
  }

  Future<void> _pickImage() async {
    if (_sending) return;
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

  Future<void> _send() async {
    final text = _input.text.trim();
    final attach = _pending;
    if ((text.isEmpty && attach == null) || _sending) return;
    final api = ref.read(apiProvider);
    if (api == null) return;
    final sid = await ref.read(sessionIdProvider.future);

    // Explicit /command overrides the screen's command.
    var command = widget.command;
    var prompt = text;
    if (text.startsWith('/')) {
      final sp = text.indexOf(' ');
      command = (sp == -1 ? text.substring(1) : text.substring(1, sp)).toLowerCase();
      prompt = sp == -1 ? '' : text.substring(sp + 1);
    }

    // Live 'status' bubble that accumulates progress steps, replaced by the
    // final reply when it lands.
    final live = ChatMessage('status', 'Gajala typing…');
    setState(() {
      _msgs.add(ChatMessage('user', text.isEmpty ? '📷 Photo' : text,
          localImage: attach?.path));
      _msgs.add(live);
      _sending = true;
      _pending = null;
      _input.clear();
    });
    _scrollEnd();

    final steps = <String>[];
    var replaced = false;
    void finish(ChatMessage msg) {
      setState(() {
        final i = _msgs.indexOf(live);
        if (i >= 0) {
          _msgs[i] = msg;
        } else {
          _msgs.add(msg);
        }
      });
      replaced = true;
    }

    try {
      // Upload the attachment first, then prepend a marker the agent understands
      // (its claude tool reads the image from that path).
      if (attach != null) {
        setState(() => live.text = 'Uploading image…');
        final bytes = await File(attach.path).readAsBytes();
        final serverPath = await api.uploadImage(bytes, attach.name);
        final marker = '[User sent an image, saved at: $serverPath]';
        prompt = prompt.isEmpty ? marker : '$marker\n$prompt';
      }

      // notify:true → server also pushes a "reply ready" ping on completion as a
      // safety net if the stream drops (app backgrounded/killed). It's suppressed
      // while we're still foregrounded on this chat.
      await for (final ev in api.runStream(command, prompt, sid, notify: true)) {
        switch (ev['type']) {
          case 'step':
            final label = ev['label']?.toString() ?? '';
            if (label.isNotEmpty) steps.add(label);
            setState(() => live.text = steps.join('\n'));
            _scrollEnd();
            break;
          case 'final':
            final (clean, urls) = _splitImages(ev['result']?.toString() ?? '', api);
            finish(ChatMessage('bot', clean.isEmpty && urls.isNotEmpty ? '' : (clean.isEmpty ? '(no result)' : clean),
                remoteImages: urls));
            break;
          case 'error':
            finish(ChatMessage('error', ev['message']?.toString() ?? 'Server error'));
            break;
        }
      }
      // Stream ended without a terminal frame (rare) — don't leave a dangling
      // status bubble.
      if (!replaced) {
        finish(ChatMessage('error', 'Connection ended before a reply'));
      }
    } catch (e) {
      finish(ChatMessage('error', friendlyError(e)));
    } finally {
      setState(() => _sending = false);
      _scrollEnd();
    }
  }

  void _scrollEnd() => WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) {
          _scroll.animateTo(_scroll.position.maxScrollExtent,
              duration: const Duration(milliseconds: 250), curve: Curves.easeOut);
        }
      });

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
          if (dir == null && model == null) return;
          setState(() {
            if (dir != null) _dir = dir;
            if (model != null) _model = model;
            // A centered note so the chat records the context change.
            _msgs.add(ChatMessage('system', 'Context → $_dir · ${_modelLabel(_model)}'));
          });
          _scrollEnd();
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final imgHeaders = ref.read(apiProvider)?.authHeaders;
    return Scaffold(
      appBar: AppBar(title: _buildTitle(context)),
      body: Column(children: [
        Expanded(
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.all(12),
            itemCount: _msgs.length,
            itemBuilder: (_, i) => _Bubble(_msgs[i], imgHeaders),
          ),
        ),
        Container(
          color: context.pal.surface,
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
            Row(children: [
              IconButton(
                icon: Icon(Icons.add_photo_alternate_outlined, color: context.pal.textDim),
                onPressed: _sending ? null : _pickImage,
                tooltip: 'Attach image',
              ),
              Expanded(
                child: TextField(
                  controller: _input,
                  minLines: 1, maxLines: 5,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _send(),
                  decoration: const InputDecoration(hintText: 'Message or /command…'),
                ),
              ),
              const SizedBox(width: 8),
              CircleAvatar(
                backgroundColor: GajalaColors.accent,
                child: IconButton(
                  icon: const Icon(Icons.send, color: Colors.white, size: 20),
                  onPressed: _sending ? null : _send,
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
  const _Bubble(this.m, this.imgHeaders);
  @override
  Widget build(BuildContext context) {
    if (m.role == 'status') return _StatusBubble(m.text);
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
      if (!mounted) return;
      setState(() {
        _projects = List<Map<String, dynamic>>.from(proj['projects'] ?? []);
        _dir = proj['current_name']?.toString() ?? _dir;
        _sessions = List<Map<String, dynamic>>.from(sess['sessions'] ?? []);
      });
    } catch (_) {/* leave lists empty */}
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
    final claudeSession = _sessions.firstWhere(
        (s) => s['engine'] == 'claude', orElse: () => const {});
    final resumeCmd = claudeSession['resume_cmd']?.toString();
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
                children: ['auto', 'claude', 'codex', 'gemini'].map((e) {
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
              if (resumeCmd != null) ...[
                const SizedBox(height: 16),
                _sectionLabel(context, 'CONTINUE ON MAC'),
                const SizedBox(height: 6),
                InkWell(
                  onTap: () {
                    Clipboard.setData(ClipboardData(text: resumeCmd));
                    ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('Copied resume command')));
                  },
                  child: Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                        color: pal.surfaceAlt,
                        borderRadius: BorderRadius.circular(8)),
                    child: Row(children: [
                      Expanded(
                        child: Text(resumeCmd,
                            style: const TextStyle(
                                fontFamily: 'monospace', fontSize: 12)),
                      ),
                      Icon(Icons.copy, size: 16, color: pal.textDim),
                    ]),
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
