import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
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
  final List<ChatMessage> _msgs = [];
  bool _sending = false;
  String? _sid;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _initSession();
    _loadHistory();
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
          setState(() => _msgs.addAll(history));
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

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty || _sending) return;
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

    setState(() {
      _msgs.add(ChatMessage('user', text));
      _sending = true;
      _input.clear();
    });
    _scrollEnd();
    try {
      // notify:true → server pushes a "reply ready" ping on completion; it's
      // suppressed app-side while we're still viewing this chat, so it only
      // surfaces if you've left the chat or backgrounded the app.
      final result = await api.run(command, prompt, sid, notify: true);
      setState(() => _msgs.add(ChatMessage('bot', result)));
    } catch (e) {
      setState(() => _msgs.add(ChatMessage('error', friendlyError(e))));
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: Column(children: [
        Expanded(
          child: ListView.builder(
            controller: _scroll,
            padding: const EdgeInsets.all(12),
            itemCount: _msgs.length,
            itemBuilder: (_, i) => _Bubble(_msgs[i]),
          ),
        ),
        if (_sending)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
              const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
              const SizedBox(width: 8),
              Text('Gajala typing…', style: TextStyle(color: context.pal.textDim, fontSize: 12)),
            ]),
          ),
        Container(
          color: context.pal.surface,
          padding: const EdgeInsets.fromLTRB(8, 8, 8, 12),
          child: Row(children: [
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
        ),
      ]),
    );
  }
}

class _Bubble extends StatelessWidget {
  final ChatMessage m;
  const _Bubble(this.m);
  @override
  Widget build(BuildContext context) {
    final isUser = m.role == 'user';
    final isError = m.role == 'error';
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.symmetric(vertical: 4),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
        constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * .82),
        decoration: BoxDecoration(
          color: isUser ? GajalaColors.userBubble : context.pal.botBubble,
          borderRadius: BorderRadius.only(
            topLeft: const Radius.circular(16), topRight: const Radius.circular(16),
            bottomLeft: Radius.circular(isUser ? 16 : 4),
            bottomRight: Radius.circular(isUser ? 4 : 16),
          ),
          border: isError ? Border.all(color: GajalaColors.danger) : null,
        ),
        child: SelectableText(m.text,
            style: TextStyle(
                color: isError ? GajalaColors.danger : context.pal.text, height: 1.35)),
      ),
    );
  }
}
