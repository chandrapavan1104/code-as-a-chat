import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'core/push.dart';
import 'core/state.dart';
import 'core/theme.dart';
import 'screens/connect_screen.dart';
import 'screens/dashboard_screen.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  SystemChrome.setSystemUIOverlayStyle(const SystemUiOverlayStyle(
    statusBarColor: Colors.transparent,
    statusBarIconBrightness: Brightness.light,
  ));
  await Push.init();
  runApp(const ProviderScope(child: GajalaApp()));
}

class GajalaApp extends ConsumerWidget {
  const GajalaApp({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
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
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      themeMode: mode,
      home: config == null ? const ConnectScreen() : const DashboardScreen(),
    );
  }
}
