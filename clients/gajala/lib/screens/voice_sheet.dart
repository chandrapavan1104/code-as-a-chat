// Hands-free Gajala: listen → route → answer → speak.
//
// Phone-native requests run as Android intents; everything else goes to the
// same per-project Gajala thread the chat screen shows, so voice turns share its
// memory and appear there afterwards. When the Mac is unreachable, the
// on-phone model answers general questions instead.

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/chat_controller.dart';
import '../core/offline_brain.dart';
import '../core/push.dart';
import '../core/state.dart';
import '../core/theme.dart';
import '../core/voice.dart';
import '../core/voice_logic.dart';
import 'chat_screen.dart';

/// Open the voice sheet over whatever is on screen.
Future<void> showVoiceSheet([BuildContext? context]) async {
  final ctx = context ?? Push.navigatorKey.currentContext;
  if (ctx == null) return;
  await showModalBottomSheet(
    context: ctx,
    isScrollControlled: true,
    useSafeArea: true,
    backgroundColor: ctx.pal.surface,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (_) => const VoiceSheet(),
  );
  await Voice.instance.cancelListening();
  await Voice.instance.stopSpeaking();
}

enum _Phase { idle, listening, thinking, speaking }

class _Exchange {
  final String heard;
  String reply;
  final String via; // 'Phone', 'Gajala', 'Offline'
  _Exchange(this.heard, this.reply, this.via);
}

class VoiceSheet extends ConsumerStatefulWidget {
  const VoiceSheet({super.key});
  @override
  ConsumerState<VoiceSheet> createState() => _VoiceSheetState();
}

class _VoiceSheetState extends ConsumerState<VoiceSheet> {
  _Phase _phase = _Phase.idle;
  String _partial = '';
  String? _error;
  String? _dir;
  final _log = <_Exchange>[];
  bool _speakReplies = true;
  bool _settingsOpen = false;

  @override
  void initState() {
    super.initState();
    Voice.instance.speakReplies().then((v) {
      if (mounted) setState(() => _speakReplies = v);
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => _turn());
  }

  Future<ChatKey> _shellKey(GajalaApi api) async {
    final install = await ref.read(sessionIdProvider.future);
    if (_dir == null) {
      try {
        _dir = (await api.projects())['current_name']?.toString();
      } catch (_) {/* default thread */}
    }
    return ChatKey('shell', shellSessionId(install, _dir));
  }

  Future<void> _turn() async {
    if (_phase == _Phase.listening) {
      await Voice.instance.stopListening();
      return;
    }
    await Voice.instance.stopSpeaking();
    setState(() {
      _phase = _Phase.listening;
      _partial = '';
      _error = null;
    });
    String heard;
    try {
      heard = await Voice.instance.listen(
        onPartial: (p) {
          if (mounted) setState(() => _partial = p);
        },
      );
    } on VoiceUnavailable catch (e) {
      if (mounted) {
        setState(() {
          _phase = _Phase.idle;
          _error = e.message;
        });
      }
      return;
    }
    if (!mounted) return;
    if (heard.isEmpty) {
      setState(() => _phase = _Phase.idle);
      return;
    }
    setState(() {
      _phase = _Phase.thinking;
      _partial = heard;
    });
    final exchange = await _answer(heard);
    if (!mounted) return;
    setState(() {
      _log.add(exchange);
      _partial = '';
    });
    if (_speakReplies) {
      setState(() => _phase = _Phase.speaking);
      await Voice.instance.speak(exchange.reply);
    }
    if (mounted) setState(() => _phase = _Phase.idle);
  }

  Future<_Exchange> _answer(String heard) async {
    final local = parseLocalIntent(heard);
    if (local != null) {
      try {
        return _Exchange(heard, await runLocalIntent(local), 'Phone');
      } on PlatformException {
        return _Exchange(heard, 'No app on this phone can do that.', 'Phone');
      }
    }

    final api = ref.read(apiProvider);
    if (api != null && await api.reachable()) {
      return _Exchange(heard, await _askGajala(api, heard), 'Gajala');
    }

    final brain = OfflineBrain.instance;
    if (!await brain.isInstalled()) {
      return _Exchange(
        heard,
        "I can't reach your Mac, and there's no offline model on this phone "
            'yet. Download it from voice settings next time you are connected.',
        'Offline',
      );
    }
    try {
      return _Exchange(heard, await brain.answer(heard), 'Offline');
    } catch (e) {
      return _Exchange(heard, "I can't reach your Mac and the offline model failed: $e", 'Offline');
    }
  }

  Future<String> _askGajala(GajalaApi api, String heard) async {
    final key = await _shellKey(api);
    final ctrl = ref.read(chatControllerProvider(key).notifier);
    await ctrl.ensureLoaded();
    if (ref.read(chatControllerProvider(key)).sending) {
      await ctrl.send(heard);
      return "Gajala's still working on your last request, so I queued this. "
          'The answer will show in the chat.';
    }
    final before = ref.read(chatControllerProvider(key)).messages.length;
    await ctrl.send(heard);
    final state = ref.read(chatControllerProvider(key));
    // A mid-turn project switch moves the thread; follow it for the next turn.
    if (state.workspace != null) _dir = state.workspace;
    final reply = state.messages
        .skip(before)
        .lastWhere(
          (m) => const {'bot', 'error', 'system'}.contains(m.role),
          orElse: () => state.messages.last,
        );
    return reply.text.isEmpty ? 'Done — the details are in the chat.' : reply.text;
  }

  void _openChat() {
    Navigator.of(context).pop();
    Push.navigatorKey.currentState?.push(
      MaterialPageRoute(builder: (_) => const ChatScreen(command: 'shell', title: 'Gajala')),
    );
  }

  String get _status => switch (_phase) {
        _Phase.listening => 'Listening…',
        _Phase.thinking => 'Thinking…',
        _Phase.speaking => 'Speaking…',
        _Phase.idle => _log.isEmpty ? 'Tap the mic and talk' : 'Tap the mic to follow up',
      };

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    return Padding(
      padding: EdgeInsets.fromLTRB(20, 12, 20, 20 + MediaQuery.viewInsetsOf(context).bottom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(children: [
            Text('Gajala', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600, color: pal.text)),
            const Spacer(),
            IconButton(
              tooltip: 'Open chat',
              icon: Icon(Icons.chat_bubble_outline, color: pal.textDim),
              onPressed: _openChat,
            ),
            IconButton(
              tooltip: 'Voice settings',
              icon: Icon(_settingsOpen ? Icons.expand_less : Icons.tune, color: pal.textDim),
              onPressed: () => setState(() => _settingsOpen = !_settingsOpen),
            ),
          ]),
          if (_settingsOpen)
            _VoiceSettings(
              speakReplies: _speakReplies,
              onSpeakReplies: (v) {
                setState(() => _speakReplies = v);
                Voice.instance.setSpeakReplies(v);
                if (!v) Voice.instance.stopSpeaking();
              },
            ),
          ConstrainedBox(
            constraints: BoxConstraints(maxHeight: MediaQuery.sizeOf(context).height * 0.4),
            child: ListView(
              shrinkWrap: true,
              reverse: true,
              children: [
                for (final e in _log.reversed) _ExchangeView(e),
              ],
            ),
          ),
          const SizedBox(height: 12),
          if (_partial.isNotEmpty)
            Text('“$_partial”',
                textAlign: TextAlign.center,
                style: TextStyle(fontSize: 16, color: pal.text, fontStyle: FontStyle.italic)),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(_error!, textAlign: TextAlign.center,
                  style: const TextStyle(color: GajalaColors.danger)),
            ),
          const SizedBox(height: 16),
          Center(
            child: GestureDetector(
              onTap: _phase == _Phase.thinking ? null : _turn,
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 250),
                width: _phase == _Phase.listening ? 84 : 72,
                height: _phase == _Phase.listening ? 84 : 72,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: _phase == _Phase.listening ? GajalaColors.danger : GajalaColors.accent,
                  boxShadow: [
                    if (_phase == _Phase.listening)
                      BoxShadow(color: GajalaColors.danger.withValues(alpha: 0.4), blurRadius: 24, spreadRadius: 4),
                  ],
                ),
                child: _phase == _Phase.thinking
                    ? const Padding(
                        padding: EdgeInsets.all(24),
                        child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.white),
                      )
                    : Icon(
                        _phase == _Phase.listening
                            ? Icons.stop
                            : _phase == _Phase.speaking
                                ? Icons.graphic_eq
                                : Icons.mic,
                        color: Colors.white,
                        size: 34,
                        semanticLabel: _phase == _Phase.listening ? 'Stop listening' : 'Talk to Gajala',
                      ),
              ),
            ),
          ),
          const SizedBox(height: 10),
          Text(_status, textAlign: TextAlign.center, style: TextStyle(color: pal.textDim)),
        ],
      ),
    );
  }
}

class _ExchangeView extends StatelessWidget {
  final _Exchange e;
  const _ExchangeView(this.e);
  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Align(
          alignment: Alignment.centerRight,
          child: Text(e.heard, style: TextStyle(color: pal.textDim)),
        ),
        const SizedBox(height: 6),
        Container(
          padding: const EdgeInsets.all(12),
          decoration: BoxDecoration(
            color: pal.surfaceAlt,
            borderRadius: BorderRadius.circular(12),
          ),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(e.via.toUpperCase(),
                style: TextStyle(fontSize: 10, letterSpacing: 1.2, color: pal.textDim)),
            const SizedBox(height: 4),
            SelectableText(e.reply, style: TextStyle(color: pal.text)),
          ]),
        ),
      ]),
    );
  }
}

class _VoiceSettings extends ConsumerStatefulWidget {
  final bool speakReplies;
  final ValueChanged<bool> onSpeakReplies;
  const _VoiceSettings({required this.speakReplies, required this.onSpeakReplies});
  @override
  ConsumerState<_VoiceSettings> createState() => _VoiceSettingsState();
}

class _VoiceSettingsState extends ConsumerState<_VoiceSettings> {
  int _bytes = 0;
  double? _progress;
  String? _modelError;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    final b = await OfflineBrain.instance.installedBytes();
    if (mounted) setState(() => _bytes = b);
  }

  Future<void> _download() async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    setState(() {
      _progress = 0;
      _modelError = null;
    });
    try {
      await OfflineBrain.instance.download(api, (p) {
        if (mounted) setState(() => _progress = p);
      });
    } catch (e) {
      if (mounted) setState(() => _modelError = e is StateError ? e.message : friendlyError(e));
    }
    if (mounted) setState(() => _progress = null);
    await _refresh();
  }

  Future<void> _remove() async {
    await OfflineBrain.instance.remove();
    await _refresh();
  }

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    final installed = _bytes > 0;
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      decoration: BoxDecoration(
        border: Border.all(color: pal.border),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(children: [
        SwitchListTile(
          title: const Text('Speak replies'),
          value: widget.speakReplies,
          onChanged: widget.onSpeakReplies,
        ),
        ListTile(
          leading: const Icon(Icons.assistant_outlined),
          title: const Text('Make Gajala the phone assistant'),
          subtitle: const Text('Pick Gajala under “Digital assistant app”'),
          onTap: openAssistantSettings,
        ),
        ListTile(
          leading: const Icon(Icons.cloud_off_outlined),
          title: const Text('Offline model'),
          subtitle: Text(_modelError ??
              (_progress != null
                  ? 'Downloading from your Mac… ${(_progress! * 100).toStringAsFixed(0)}%'
                  : installed
                      ? 'Qwen3 0.6B on this phone · ${(_bytes / 1e6).toStringAsFixed(0)} MB'
                      : 'Answers basic questions when the Mac is unreachable (~590 MB)')),
          trailing: _progress != null
              ? SizedBox(
                  width: 22,
                  height: 22,
                  child: CircularProgressIndicator(strokeWidth: 2, value: _progress),
                )
              : installed
                  ? IconButton(
                      tooltip: 'Remove offline model',
                      icon: const Icon(Icons.delete_outline),
                      onPressed: _remove,
                    )
                  : IconButton(
                      tooltip: 'Download from Mac',
                      icon: const Icon(Icons.download),
                      onPressed: _download,
                    ),
        ),
      ]),
    );
  }
}
