import 'dart:async';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/app_lock.dart';
import 'core/chat_controller.dart';
import 'core/errors.dart';
import 'core/outbox.dart';
import 'core/push.dart';
import 'screens/phone_inbox_screen.dart';
import 'core/state.dart';
import 'core/storage.dart';
import 'core/theme.dart';
import 'core/voice.dart';
import 'core/voice_journal.dart';
import 'core/widget_bridge.dart';
import 'screens/chat_screen.dart';
import 'screens/connect_screen.dart';
import 'screens/home_shell.dart';
import 'screens/voice_sheet.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  ErrorReporter.install(); // capture crashes → server → the fix agent
  SystemChrome.setSystemUIOverlayStyle(
    const SystemUiOverlayStyle(
      statusBarColor: Colors.transparent,
      statusBarIconBrightness: Brightness.light,
    ),
  );
  await Push.init();
  await initHomeWidgets(); // register the widget background callback
  runApp(const ProviderScope(child: GajalaApp()));
}

class GajalaApp extends ConsumerStatefulWidget {
  const GajalaApp({super.key});
  @override
  ConsumerState<GajalaApp> createState() => _GajalaAppState();
}

class _GajalaAppState extends ConsumerState<GajalaApp> {
  Timer? _outboxTimer;
  AppLifecycleListener? _lifecycle;
  bool _importingLockedVoice = false;
  bool _openingPhoneInbox = false;

  Future<void> _openPhoneShare() async {
    final api = ref.read(apiProvider);
    final nav = Push.navigatorKey.currentState;
    if (_openingPhoneInbox || api == null || nav == null) return;
    _openingPhoneInbox = true;
    var opened = false;
    try {
      final files = await phoneImportChannel
          .invokeListMethod<Map<Object?, Object?>>('consume');
      if (!mounted || files == null || files.isEmpty) return;
      opened = true;
      await nav.push(MaterialPageRoute<void>(
        builder: (_) => PhoneInboxScreen(api: api, shared: files),
      ));
    } on MissingPluginException {
      // Android only.
    } finally {
      _openingPhoneInbox = false;
      if (opened && mounted) unawaited(_openPhoneShare());
    }
  }

  Future<void> _importLockedVoice() async {
    if (_importingLockedVoice) return;
    _importingLockedVoice = true;
    try {
      final data = await EverydayVoice.channel.invokeMethod<String>('journal');
      if (data == null || data == '[]' || !mounted) return;
      final install = await ref.read(sessionIdProvider.future);
      final selected = await Storage.selectedConversation();
      if (!mounted) return;
      final sid = selected.sessionId?.startsWith('$install:') == true
          ? selected.sessionId! : shellSessionId(install, null);
      final ctrl = ref.read(chatControllerProvider(ChatKey('shell', sid)).notifier);
      for (final item in jsonDecode(data) as List) {
        final turn = VoiceJournalTurn(
          id: item['id'] as String,
          sessionId: sid,
          createdAt: DateTime.fromMillisecondsSinceEpoch(item['time'] as int),
          completed: true,
          messages: [
            VoiceJournalMessage('user', item['request'] as String),
            VoiceJournalMessage('assistant', item['reply'] as String),
          ],
        );
        await VoiceJournal.instance.importCompleted(turn);
        await EverydayVoice.channel.invokeMethod<void>('acknowledge', turn.id);
        if (mounted) unawaited(ctrl.syncImportedVoiceTurn(turn));
      }
    } on MissingPluginException {
      // Android-only feature.
    } on PlatformException {
      // Device locked or the native activity had not resumed; retry on resume.
    } finally {
      _importingLockedVoice = false;
    }
  }

  /// Deliver unsent messages for every conversation, not only the open one.
  Future<void> _replayOutbox() async {
    if (ref.read(apiProvider) == null) return;
    final (:pending, expired: _) = await Outbox.instance.load();
    final keys = {for (final e in pending) ChatKey(e.command, e.sid)};
    for (final key in keys) {
      await ref.read(chatControllerProvider(key).notifier).replayOutbox();
    }
  }

  @override
  void dispose() {
    phoneImportChannel.setMethodCallHandler(null);
    _outboxTimer?.cancel();
    _lifecycle?.dispose();
    super.dispose();
  }

  @override
  void initState() {
    super.initState();
    phoneImportChannel.setMethodCallHandler((call) async {
      if (call.method == 'incoming' && mounted) unawaited(_openPhoneShare());
    });
    _outboxTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => _replayOutbox(),
    );
    _lifecycle = AppLifecycleListener(onResume: () {
      unawaited(_importLockedVoice());
      unawaited(_replayOutbox());
    });
    // Tapping a reply notification (foreground, background, or cold launch)
    // deep-links into the chat.
    Push.onOpenChat = (sessionId) {
      final nav = Push.navigatorKey.currentState;
      if (nav == null) return;
      nav.push(
        MaterialPageRoute(
          builder: (_) => ChatScreen(
            command: 'shell',
            title: 'Gajala',
            sessionId: sessionId,
          ),
        ),
      );
    };
    // A foreground push refreshes the Tasks list + Alerts badge live.
    Push.onPush = () {
      ref.invalidate(notificationsProvider);
      ref.invalidate(unreadCountProvider);
      ref.invalidate(queueProvider);
    };
    // Long-press home / the assistant button opens the voice sheet — on a cold
    // start and when Gajala is already running.
    AssistLaunch.listen(_openVoice);
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      Push.handleLaunchMessage();
      unawaited(_importLockedVoice());
      handleWidgetLaunch(); // route Ask / Dump widget deep-links
      if (await AssistLaunch.consumeInitial()) await _openVoice();
    });
  }

  Future<void> _openVoice() async {
    await showVoiceSheet();
  }

  @override
  Widget build(BuildContext context) {
    final config = ref.watch(configProvider);
    final mode = ref.watch(themeModeProvider);
    // Register this device for push whenever a live API client is available.
    ref.listen(apiProvider, (_, api) {
      if (api != null) Push.registerWith(api);
    });
    final api = ref.read(apiProvider);
    if (api != null) Push.registerWith(api);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      flushWidgetLaunch();
      if (mounted && api != null) unawaited(_openPhoneShare());
    });
    return MaterialApp(
      title: 'Gajala',
      debugShowCheckedModeBanner: false,
      navigatorKey: Push.navigatorKey,
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      themeMode: mode,
      home: config == null ? const ConnectScreen() : const HomeShell(),
      builder: (context, child) =>
          AppLockGate(child: child ?? const SizedBox.shrink()),
    );
  }
}
