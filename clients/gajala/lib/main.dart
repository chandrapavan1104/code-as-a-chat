import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/app_lock.dart';
import 'core/chat_controller.dart';
import 'core/errors.dart';
import 'core/outbox.dart';
import 'core/push.dart';
import 'core/state.dart';
import 'core/storage.dart';
import 'core/theme.dart';
import 'core/voice.dart';
import 'core/widget_bridge.dart';
import 'screens/chat_screen.dart';
import 'screens/connect_screen.dart';
import 'screens/home_shell.dart';
import 'screens/voice_sheet.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  ErrorReporter.install();   // capture crashes → server → the fix agent
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.light,
  ));
  await Push.init();
  await initHomeWidgets();   // register the widget background callback
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
    _outboxTimer?.cancel();
    _lifecycle?.dispose();
    super.dispose();
  }

  @override
  void initState() {
    super.initState();
    _outboxTimer = Timer.periodic(
        const Duration(seconds: 30), (_) => _replayOutbox());
    _lifecycle = AppLifecycleListener(onResume: _replayOutbox);
    // Tapping a reply notification (foreground, background, or cold launch)
    // deep-links into the chat.
    Push.onOpenChat = (_) {
      final nav = Push.navigatorKey.currentState;
      if (nav == null) return;
      nav.push(MaterialPageRoute(
        builder: (_) => const ChatScreen(command: 'shell', title: 'Gajala'),
      ));
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
      handleWidgetLaunch();   // route Ask / Dump widget deep-links
      if (await AssistLaunch.consumeInitial()) await _openVoice();
    });
  }

  Future<void> _openVoice() async {
    // Voice needs a paired Mac for anything but phone actions; before pairing
    // the connect screen is the right place to land.
    if (await Storage.loadConfig() == null) return;
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
