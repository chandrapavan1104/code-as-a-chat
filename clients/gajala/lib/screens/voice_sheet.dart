// Hands-free Gajala: listen → route → answer → speak.
//
// Phone-native requests run as Android intents; everything else goes to the
// same per-project Gajala thread the chat screen shows, so voice turns share its
// memory and appear there afterwards. When the Mac is unreachable, the
// on-phone model answers general questions instead.

import 'dart:async';
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
import '../core/phone_actions.dart';
import '../core/device_actions.dart';
import '../core/phone_abilities.dart';
import '../widgets/voice_picker.dart';
import '../widgets/music_access_tile.dart';
import '../core/wake_word.dart';
import 'chat_screen.dart';

/// Open the voice sheet over whatever is on screen.
bool _sheetOpen = false;

Future<void> showVoiceSheet([BuildContext? context]) async {
  final ctx = context ?? Push.navigatorKey.currentContext;
  // A second "Hey Gajala" while voice mode is open must not stack another sheet.
  if (ctx == null || _sheetOpen) return;
  _sheetOpen = true;
  unawaited(WakeWord.hold());
  try {
    await _showSheet(ctx);
  } finally {
    _sheetOpen = false;
    await WakeWord.release();
  }
}

Future<void> _showSheet(BuildContext ctx) async {
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

class _VoiceSheetState extends ConsumerState<VoiceSheet>
    with WidgetsBindingObserver {
  _Phase _phase = _Phase.idle;
  String _partial = '';
  String? _error;
  String? _dir;
  final _log = <_Exchange>[];
  bool _speakReplies = true;
  bool _settingsOpen = false;
  bool _busy = false;
  bool _foreground = true;
  bool _handedOff = false;
  int _generation = 0;
  Timer? _followUp;
  ChatController? _voiceLog;
  String? _voiceLogId;
  static const _phone = MethodChannel('gajala/phone');

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    Voice.instance.speakReplies().then((v) {
      if (mounted) setState(() => _speakReplies = v);
    });
    WidgetsBinding.instance.addPostFrameCallback((_) => _turn());
  }

  Future<ChatKey> _shellKey(GajalaApi? api) async {
    final install = await ref.read(sessionIdProvider.future);
    if (_dir == null) {
      try {
        _dir = (await api?.projects())?['current_name']?.toString();
      } catch (_) {
        /* default thread */
      }
    }
    return ChatKey('shell', shellSessionId(install, _dir));
  }

  @override
  void dispose() {
    _generation++;
    _followUp?.cancel();
    WidgetsBinding.instance.removeObserver(this);
    unawaited(Voice.instance.cancelListening());
    unawaited(Voice.instance.stopSpeaking());
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused ||
        state == AppLifecycleState.detached) {
      _foreground = false;
      _generation++;
      _followUp?.cancel();
      unawaited(Voice.instance.cancelListening());
      unawaited(Voice.instance.stopSpeaking());
    } else if (state == AppLifecycleState.resumed) {
      _foreground = true;
    }
  }

  bool _active(int generation) =>
      mounted && _foreground && !_settingsOpen && generation == _generation;

  Future<void> _stop() async {
    _generation++;
    _followUp?.cancel();
    await Voice.instance.cancelListening();
    await Voice.instance.stopSpeaking();
    if (mounted) Navigator.of(context).pop();
  }

  Future<String> _askPhone(String question, int generation) async {
    if (!_active(generation)) return '';
    var log = _voiceLog;
    var id = _voiceLogId;
    final ownsLog = log == null || id == null;
    if (ownsLog) {
      final key = await _shellKey(ref.read(apiProvider));
      if (!_active(generation)) return '';
      log = ref.read(chatControllerProvider(key).notifier);
      id = await log!.beginLocalVoiceTurn(question, role: 'assistant');
    } else {
      await log!.appendLocalVoiceMessage(id, 'assistant', question);
    }
    try {
      if (!_active(generation)) return '';
      setState(() {
        _phase = _Phase.speaking;
        _partial = question;
      });
      await Voice.instance.speak(question);
      if (!_active(generation)) return '';
      setState(() => _phase = _Phase.listening);
      final answer = await Voice.instance
          .listen(
            onPartial: (p) {
              if (_active(generation)) setState(() => _partial = p);
            },
          )
          .timeout(
            const Duration(seconds: 15),
            onTimeout: () {
              unawaited(Voice.instance.cancelListening());
              return '';
            },
          );
      await log!.appendLocalVoiceMessage(
        id!,
        answer.isEmpty ? 'assistant' : 'user',
        answer.isEmpty ? 'No confirmation was received.' : answer,
      );
      return answer;
    } finally {
      await Voice.instance.cancelListening();
      if (ownsLog) await log!.finishLocalVoiceTurn(id!);
      if (_active(generation)) setState(() => _phase = _Phase.thinking);
    }
  }

  Future<void> _turn() async {
    if (_busy || !_foreground || _settingsOpen || !mounted) return;
    _busy = true;
    _handedOff = false;
    final generation = ++_generation;
    try {
      await Voice.instance.stopSpeaking();
      if (!_active(generation)) return;
      setState(() {
        _phase = _Phase.listening;
        _partial = '';
        _error = null;
      });
      final heard = await Voice.instance.listen(
        onPartial: (p) {
          if (_active(generation)) setState(() => _partial = p);
        },
      );
      if (!_active(generation)) return;
      if (heard.isEmpty ||
          RegExp(
            r'^(stop|cancel|goodbye|bye|stop listening)[.!?]*$',
            caseSensitive: false,
          ).hasMatch(heard.trim())) {
        await _stop();
        return;
      }
      setState(() {
        _phase = _Phase.thinking;
        _partial = heard;
      });
      final exchange = await _answer(heard, generation);
      if (!mounted) return;
      setState(() {
        _log.add(exchange);
        _partial = '';
      });
      // A successful phone/music handoff owns the audio and screen now.
      if (_speakReplies && mounted && !_settingsOpen && !_handedOff) {
        setState(() => _phase = _Phase.speaking);
        await Voice.instance.speak(exchange.reply);
      }
      if (_handedOff) {
        if (mounted) Navigator.of(context).pop();
        return;
      }
      if (_active(generation)) {
        _followUp = Timer(const Duration(milliseconds: 500), () {
          if (_active(generation)) unawaited(_turn());
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      _busy = false;
      if (mounted) setState(() => _phase = _Phase.idle);
    }
  }

  Future<void> _appendVoiceLog(String role, String text) async {
    final log = _voiceLog;
    final id = _voiceLogId;
    if (log != null && id != null)
      await log.appendLocalVoiceMessage(id, role, text);
  }

  Future<_Exchange> _answer(String heard, int generation) async {
    final local = parseLocalIntent(heard) != null;
    if (local) {
      final key = await _shellKey(ref.read(apiProvider));
      if (!mounted) return _Exchange(heard, 'Voice session ended.', 'Phone');
      _voiceLog = ref.read(chatControllerProvider(key).notifier);
      _voiceLogId = await _voiceLog!.beginLocalVoiceTurn(heard);
    }
    try {
      if (!_active(generation)) {
        const message = 'Voice session ended before the action started.';
        await _appendVoiceLog('assistant', message);
        return _Exchange(heard, message, 'Phone');
      }
      final exchange = await _answerUnlogged(heard, generation);
      if (exchange.via == 'Offline' && mounted) {
        final key = await _shellKey(ref.read(apiProvider));
        if (mounted) {
          _voiceLog = ref.read(chatControllerProvider(key).notifier);
          _voiceLogId = await _voiceLog!.beginLocalVoiceTurn(heard);
        }
      }
      await _appendVoiceLog('assistant', exchange.reply);
      return exchange;
    } catch (error) {
      await _appendVoiceLog(
        'assistant',
        'Voice action did not complete: $error',
      );
      rethrow;
    } finally {
      final log = _voiceLog;
      final id = _voiceLogId;
      _voiceLog = null;
      _voiceLogId = null;
      if (log != null && id != null) await log.finishLocalVoiceTurn(id);
    }
  }

  Future<_Exchange> _answerUnlogged(String heard, int generation) async {
    final local = parseLocalIntent(heard);
    if (local != null) {
      try {
        if (local is DialNumber || local is CallContact || local is PlayMusic) {
          final actions = PhoneActions(
            invoke: (method, args) async => Map<String, dynamic>.from(
              await _phone.invokeMapMethod<String, dynamic>(method, args) ?? {},
            ),
            ask: (question) => _askPhone(question, generation),
            isActive: () => _active(generation),
          );
          final result = await actions.execute(local);
          _handedOff = result.handedOff;
          return _Exchange(heard, result.reply, 'Phone');
        }
        final reply = await runLocalIntent(local);
        _handedOff =
            local is Navigate ||
            local is WebSearch ||
            local is AskGoogle ||
            (local is PhoneAction &&
                const {
                  'action.open_app',
                  'action.play',
                }.contains(local.command));
        return _Exchange(heard, reply, 'Phone');
      } on PlatformException catch (e) {
        return _Exchange(
          heard,
          e.message ?? 'No app on this phone can do that.',
          'Phone',
        );
      }
    }

    final api = ref.read(apiProvider);
    if (api != null && await api.reachable()) {
      return _Exchange(
        heard,
        await _askGajala(api, heard, generation),
        'Gajala',
      );
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
      return _Exchange(
        heard,
        "I can't reach your Mac and the offline model failed: $e",
        'Offline',
      );
    }
  }

  Future<String> _askGajala(GajalaApi api, String heard, int generation) async {
    final key = await _shellKey(api);
    final ctrl = ref.read(chatControllerProvider(key).notifier);
    await ctrl.ensureLoaded();
    if (ref.read(chatControllerProvider(key)).sending) {
      await ctrl.send(heard);
      return "Gajala's still working on your last request, so I queued this. "
          'The answer will show in the chat.';
    }
    final before = ref.read(chatControllerProvider(key)).messages.length;
    await ctrl.send(
      heard,
      phone: (command, args, api) async {
        if (!_active(generation) || _handedOff) {
          return {
            'ok': false,
            'error': 'Voice session ended; ask again to authorize this action.',
          };
        }
        if (command == 'action.call' ||
            (command == 'action.play' &&
                (args['app'] == null || args['app'] == 'youtube_music'))) {
          final target = '${args['to'] ?? ''}'.trim();
          final numeric = RegExp(r'^\+?[0-9][0-9 .()\-]{2,}$').hasMatch(target);
          final intent = command == 'action.call'
              ? (numeric ? DialNumber(target) : CallContact(target))
              : PlayMusic('${args['query'] ?? ''}');
          final result = await PhoneActions(
            invoke: (method, values) async => Map<String, dynamic>.from(
              await _phone.invokeMapMethod<String, dynamic>(method, values) ??
                  {},
            ),
            ask: (question) => _askPhone(question, generation),
            isActive: () => _active(generation),
          ).execute(intent);
          _handedOff = result.handedOff;
          return result.handedOff
              ? {
                  'ok': true,
                  'data': {'done': result.reply},
                }
              : {'ok': false, 'error': result.reply};
        }
        return command.startsWith('action.')
            ? DeviceActions.instance.run(command, args)
            : PhoneAbilities.instance.handle(command, args, api);
      },
    );
    final state = ref.read(chatControllerProvider(key));
    // A mid-turn project switch moves the thread; follow it for the next turn.
    if (state.workspace != null) _dir = state.workspace;
    final reply = state.messages
        .skip(before)
        .lastWhere(
          (m) => const {'bot', 'error', 'system'}.contains(m.role),
          orElse: () => state.messages.last,
        );
    return reply.text.isEmpty
        ? 'Done — the details are in the chat.'
        : reply.text;
  }

  void _openChat() {
    Navigator.of(context).pop();
    Push.navigatorKey.currentState?.push(
      MaterialPageRoute(
        builder: (_) => const ChatScreen(command: 'shell', title: 'Gajala'),
      ),
    );
  }

  String get _status => switch (_phase) {
    _Phase.listening => 'Listening…',
    _Phase.thinking => 'Thinking…',
    _Phase.speaking => 'Speaking…',
    _Phase.idle => 'Hands-free · say stop to finish',
  };

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    return Padding(
      padding: EdgeInsets.fromLTRB(
        20,
        12,
        20,
        20 + MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                IconButton(
                  tooltip: 'End voice session',
                  onPressed: _stop,
                  icon: const Icon(Icons.close),
                ),
                Text(
                  'Gajala',
                  style: TextStyle(
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                    color: pal.text,
                  ),
                ),
                const Spacer(),
                IconButton(
                  tooltip: 'Open chat',
                  icon: Icon(Icons.chat_bubble_outline, color: pal.textDim),
                  onPressed: _openChat,
                ),
                IconButton(
                  tooltip: 'Voice settings',
                  icon: Icon(
                    _settingsOpen ? Icons.expand_less : Icons.tune,
                    color: pal.textDim,
                  ),
                  onPressed: () async {
                    _generation++;
                    _followUp?.cancel();
                    await Voice.instance.cancelListening();
                    await Voice.instance.stopSpeaking();
                    if (!mounted) return;
                    setState(() => _settingsOpen = !_settingsOpen);
                    if (!_settingsOpen) unawaited(_turn());
                  },
                ),
              ],
            ),
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
              constraints: BoxConstraints(
                maxHeight: MediaQuery.sizeOf(context).height * 0.4,
              ),
              child: ListView(
                shrinkWrap: true,
                reverse: true,
                children: [for (final e in _log.reversed) _ExchangeView(e)],
              ),
            ),
            const SizedBox(height: 12),
            if (_partial.isNotEmpty)
              Text(
                '“$_partial”',
                textAlign: TextAlign.center,
                style: TextStyle(
                  fontSize: 16,
                  color: pal.text,
                  fontStyle: FontStyle.italic,
                ),
              ),
            if (_error != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  _error!,
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: GajalaColors.danger),
                ),
              ),
            const SizedBox(height: 16),
            Center(
              child: GestureDetector(
                onTap: _busy ? _stop : _turn,
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 250),
                  width: _phase == _Phase.listening ? 84 : 72,
                  height: _phase == _Phase.listening ? 84 : 72,
                  decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: _phase == _Phase.listening
                        ? GajalaColors.danger
                        : GajalaColors.accent,
                    boxShadow: [
                      if (_phase == _Phase.listening)
                        BoxShadow(
                          color: GajalaColors.danger.withValues(alpha: 0.4),
                          blurRadius: 24,
                          spreadRadius: 4,
                        ),
                    ],
                  ),
                  child: _phase == _Phase.thinking
                      ? const Padding(
                          padding: EdgeInsets.all(24),
                          child: CircularProgressIndicator(
                            strokeWidth: 2.5,
                            color: Colors.white,
                          ),
                        )
                      : Icon(
                          _phase == _Phase.listening
                              ? Icons.stop
                              : _phase == _Phase.speaking
                              ? Icons.graphic_eq
                              : Icons.mic,
                          color: Colors.white,
                          size: 34,
                          semanticLabel: _phase == _Phase.listening
                              ? 'Stop listening'
                              : 'Talk to Gajala',
                        ),
                ),
              ),
            ),
            const SizedBox(height: 10),
            Text(
              _status,
              textAlign: TextAlign.center,
              style: TextStyle(color: pal.textDim),
            ),
          ],
        ),
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
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
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
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  e.via.toUpperCase(),
                  style: TextStyle(
                    fontSize: 10,
                    letterSpacing: 1.2,
                    color: pal.textDim,
                  ),
                ),
                const SizedBox(height: 4),
                SelectableText(e.reply, style: TextStyle(color: pal.text)),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _VoiceSettings extends ConsumerStatefulWidget {
  final bool speakReplies;
  final ValueChanged<bool> onSpeakReplies;
  const _VoiceSettings({
    required this.speakReplies,
    required this.onSpeakReplies,
  });
  @override
  ConsumerState<_VoiceSettings> createState() => _VoiceSettingsState();
}

class _VoiceSettingsState extends ConsumerState<_VoiceSettings> {
  int _bytes = 0;
  double? _progress;
  String? _modelError;
  WakeWordState _wake = WakeWordState.off;
  String? _wakeError;

  @override
  void initState() {
    super.initState();
    _refresh();
  }

  Future<void> _refresh() async {
    final b = await OfflineBrain.instance.installedBytes();
    final w = await WakeWord.state();
    if (mounted) {
      setState(() {
        _bytes = b;
        _wake = w;
      });
    }
  }

  Future<void> _toggleWake(bool on) async {
    setState(() => _wakeError = null);
    if (on) {
      final why = await WakeWord.enable();
      if (why != null && mounted) setState(() => _wakeError = why);
    } else {
      await WakeWord.disable();
    }
    // The service reports "listening" once its engine has loaded the model.
    await Future.delayed(const Duration(milliseconds: 600));
    await _refresh();
  }

  String get _wakeSubtitle =>
      _wakeError ??
      switch (_wake) {
        WakeWordState.unsupported => 'Not available on this phone',
        WakeWordState.off =>
          'Say it anytime the screen is on; a notification shows while it listens',
        WakeWordState.listening =>
          'Listening on this phone only — nothing is recorded or sent',
        WakeWordState.paused =>
          'On · paused while the screen is off, Battery Saver is on, or Gajala is using the mic',
      };

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
      if (mounted)
        setState(
          () => _modelError = e is StateError ? e.message : friendlyError(e),
        );
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
      child: Column(
        children: [
          VoicePicker(preferences: Voice.instance.preferences),
          const MusicAccessTile(),
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
          SwitchListTile(
            secondary: const Icon(Icons.hearing),
            title: const Text('Hands-free “Hey Gajala”'),
            subtitle: Text(_wakeSubtitle),
            value:
                _wake == WakeWordState.listening ||
                _wake == WakeWordState.paused,
            onChanged: _wake == WakeWordState.unsupported ? null : _toggleWake,
          ),
          ListTile(
            leading: const Icon(Icons.cloud_off_outlined),
            title: const Text('Offline model'),
            subtitle: Text(
              _modelError ??
                  (_progress != null
                      ? 'Downloading from your Mac… ${(_progress! * 100).toStringAsFixed(0)}%'
                      : installed
                      ? 'Qwen3 0.6B on this phone · ${(_bytes / 1e6).toStringAsFixed(0)} MB'
                      : 'Answers basic questions when the Mac is unreachable (~590 MB)'),
            ),
            trailing: _progress != null
                ? SizedBox(
                    width: 22,
                    height: 22,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      value: _progress,
                    ),
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
        ],
      ),
    );
  }
}
