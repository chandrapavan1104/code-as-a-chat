import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/push.dart';
import '../core/state.dart';
import 'chat_screen.dart';
import 'dashboard_screen.dart' show UpdateBanner;
import 'library_screen.dart';
import 'notifications_screen.dart';
import 'tasks_screen.dart';
import 'work_result_screen.dart';

/// The app shell: Chats / Work / Library / Alerts as a persistent bottom nav. An
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
  }

  @override
  void initState() {
    super.initState();
    // Let push deep-links select a tab.
    Push.onOpenTasks = () => go(1);
    Push.onOpenWork = (id) {
      go(1);
      showWorkResult(context, id);
    };
    Push.onOpenNotifications = () => go(3);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) Push.flushPendingRoute();
    });
  }

  @override
  void dispose() {
    Push.onOpenWork = null;
    Push.onOpenTasks = null;
    Push.onOpenNotifications = null;
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final unread = ref.watch(unreadCountProvider).valueOrNull ?? 0;
    return Scaffold(
      body: SafeArea(
        bottom: false,
        child: Column(
          children: [
            const UpdateBanner(),
            Expanded(
              child: IndexedStack(
                index: _index,
                children: [
                  ChatScreen(active: _index == 0),
                  const TasksScreen(),
                  const LibraryScreen(),
                  const NotificationsScreen(),
                ],
              ),
            ),
          ],
        ),
      ),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: go,
        destinations: [
          const NavigationDestination(
            icon: Icon(Icons.chat_bubble_outline),
            selectedIcon: Icon(Icons.chat_bubble),
            label: 'Chats',
          ),
          const NavigationDestination(
            icon: Icon(Icons.checklist_outlined),
            selectedIcon: Icon(Icons.checklist),
            label: 'Work',
          ),
          const NavigationDestination(
            icon: Icon(Icons.grid_view_outlined),
            selectedIcon: Icon(Icons.grid_view),
            label: 'Library',
          ),
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
            label: 'Alerts',
          ),
        ],
      ),
    );
  }
}
