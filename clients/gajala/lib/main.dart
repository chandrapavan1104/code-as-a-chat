import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/push.dart';
import 'core/state.dart';
import 'core/theme.dart';
import 'core/widget_bridge.dart';
import 'screens/chat_screen.dart';
import 'screens/connect_screen.dart';
import 'screens/dashboard_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
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
  @override
  void initState() {
    super.initState();
    // Tapping a reply notification (foreground, background, or cold launch)
    // deep-links into the chat.
    Push.onOpenChat = (_) {
      final nav = Push.navigatorKey.currentState;
      if (nav == null) return;
      nav.push(MaterialPageRoute(
        builder: (_) => const ChatScreen(command: 'shell', title: 'Gajala'),
      ));
    };
    WidgetsBinding.instance.addPostFrameCallback((_) {
      Push.handleLaunchMessage();
      handleWidgetLaunch();   // route Ask / Dump widget deep-links
    });
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
      home: config == null ? const ConnectScreen() : const DashboardScreen(),
    );
  }
}
