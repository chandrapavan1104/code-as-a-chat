import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';

/// Diary = a conversation with "Anna" (the strict elder-brother mentor).
/// History loads from /api/diary; new entries go through /run command=diary,
/// which returns Anna's reply and persists both sides server-side.
class DiaryScreen extends ConsumerStatefulWidget {
  const DiaryScreen({super.key});
  @override
  ConsumerState<DiaryScreen> createState() => _DiaryScreenState();
}

class _DiaryScreenState extends ConsumerState<DiaryScreen> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final List<ChatMessage> _msgs = [];
  bool _sending = false;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final api = ref.read(apiProvider);
    if (api != null) {
      try {
        final data = await api.diary();
        final entries = (data['entries'] as List?) ?? [];
        if (mounted) {
          setState(() => _msgs.addAll(entries.map((e) =>
              ChatMessage(e['role'] == 'anna' ? 'bot' : 'user', e['content']?.toString() ?? ''))));
        }
      } catch (_) {/* ignore, show intro */}
    }
    if (mounted) {
      if (_msgs.isEmpty) {
        _msgs.add(ChatMessage('bot',
            'This is your private diary — Anna is listening.\nHealth, money, love, plans… write anything.'));
      }
      setState(() => _loading = false);
      _scrollEnd();
    }
  }

  Future<void> _send() async {
    final text = _input.text.trim();
    if (text.isEmpty || _sending) return;
    final api = ref.read(apiProvider);
    if (api == null) return;
    final sid = await ref.read(sessionIdProvider.future);
    setState(() {
      _msgs.add(ChatMessage('user', text));
      _sending = true;
      _input.clear();
    });
    _scrollEnd();
    try {
      final reply = await api.run('diary', text, sid);
      setState(() => _msgs.add(ChatMessage('bot', reply)));
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
      appBar: AppBar(
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const Text('Anna', style: TextStyle(fontWeight: FontWeight.w700, fontSize: 17)),
          Text('your mentor', style: TextStyle(color: context.pal.textDim, fontSize: 11)),
        ]),
      ),
      body: Column(children: [
        Expanded(
          child: _loading
              ? const Center(child: CircularProgressIndicator())
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.all(12),
                  itemCount: _msgs.length,
                  itemBuilder: (_, i) => _AnnaBubble(_msgs[i]),
                ),
        ),
        if (_sending)
          Padding(
            padding: const EdgeInsets.only(bottom: 6),
            child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
              const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2)),
              const SizedBox(width: 8),
              Text('Anna is thinking…', style: TextStyle(color: context.pal.textDim, fontSize: 12)),
            ]),
          ),
        Container(
          color: context.pal.surface,
          padding: const EdgeInsets.fromLTRB(8, 8, 8, 12),
          child: Row(children: [
            Expanded(
              child: TextField(
                controller: _input,
                minLines: 1, maxLines: 6,
                textInputAction: TextInputAction.newline,
                decoration: const InputDecoration(hintText: 'Write to Anna…'),
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

class _AnnaBubble extends StatelessWidget {
  final ChatMessage m;
  const _AnnaBubble(this.m);
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
          border: isUser
              ? null
              : Border(left: BorderSide(color: GajalaColors.warn.withValues(alpha: .7), width: 3)),
        ),
        child: SelectableText(m.text,
            style: TextStyle(
                color: isError ? GajalaColors.danger : context.pal.text, height: 1.4)),
      ),
    );
  }
}
