import 'package:flutter/material.dart';
import '../screens/terminal_screen.dart';
import '../screens/trading_screen.dart';

/// Shared entry points; pushing a route preserves the current tab and draft.
class QuickAccessButton extends StatelessWidget {
  const QuickAccessButton({super.key});

  @override
  Widget build(BuildContext context) => IconButton(
    tooltip: 'Quick access',
    icon: const Icon(Icons.apps_outlined),
    onPressed: () async {
      final target = await showModalBottomSheet<String>(
        context: context,
        showDragHandle: true,
        builder: (sheet) => SafeArea(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            const Padding(
              padding: EdgeInsets.symmetric(horizontal: 16, vertical: 8),
              child: Text('Quick access', style: TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            ),
            ListTile(leading: const Icon(Icons.terminal), title: const Text('Terminal'),
              subtitle: const Text('Your Mac sessions'),
              onTap: () => Navigator.pop(sheet, 'terminal')),
            ListTile(leading: const Icon(Icons.show_chart), title: const Text('Trading'),
              subtitle: const Text('Your trading desk'),
              onTap: () => Navigator.pop(sheet, 'trading')),
          ]),
        ),
      );
      if (!context.mounted || target == null) return;
      Navigator.of(context).push(MaterialPageRoute(builder: (_) =>
        target == 'terminal' ? const TerminalScreen() : const TradingScreen()));
    },
  );
}
