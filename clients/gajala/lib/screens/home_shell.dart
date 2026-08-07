import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/push.dart';
import '../core/state.dart';
import 'dashboard_screen.dart';
import 'notifications_screen.dart';
import 'tasks_screen.dart';

/// The app shell: Home / Tasks / Alerts as a persistent bottom nav. An
/// IndexedStack keeps each tab's state alive across switches. The Alerts icon
/// carries a live unread badge. Push taps flip to the right tab via the hooks
/// wired in main.dart (onOpenTasks / onOpenNotifications).
class HomeShell extends ConsumerStatefulWidget {
  const HomeShell({super.key});
  @override
  ConsumerState<HomeShell> createState() => HomeShellState();
}

class HomeShellState extends ConsumerState<HomeShell> {
  int _index = 0;

  void go(int i) {
    if (mounted) setState(() => _index = i);
    if (i == 2) _seenAlerts();
  }

  /// Opening Alerts marks everything seen (clears the badge); the list still
  /// shows each item, and needs-input questions keep their reply affordance.
  Future<void> _seenAlerts() async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    await api.markAllRead();
    ref.invalidate(notificationsProvider);
    ref.invalidate(unreadCountProvider);
  }

  @override
  void initState() {
    super.initState();
    // Let push deep-links select a tab.
    Push.onOpenTasks = () => go(1);
    Push.onOpenNotifications = () => go(2);
  }

  @override
  Widget build(BuildContext context) {
    final unread = ref.watch(unreadCountProvider).valueOrNull ?? 0;
    return Scaffold(
      body: IndexedStack(
        index: _index,
        children: const [DashboardScreen(), TasksScreen(), NotificationsScreen()],
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: go,
        destinations: [
          const NavigationDestination(
              icon: Icon(Icons.home_outlined),
              selectedIcon: Icon(Icons.home),
              label: 'Home'),
          const NavigationDestination(
              icon: Icon(Icons.checklist_outlined),
              selectedIcon: Icon(Icons.checklist),
              label: 'Tasks'),
          NavigationDestination(
              icon: Badge.count(
                count: unread,
                isLabelVisible: unread > 0,
                child: const Icon(Icons.notifications_outlined),
              ),
              selectedIcon: Badge.count(
                count: unread,
                isLabelVisible: unread > 0,
                child: const Icon(Icons.notifications),
              ),
              label: 'Alerts'),
        ],
      ),
    );
  }
}
