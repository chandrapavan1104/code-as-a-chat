import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/state.dart';
import '../core/theme.dart';

class MacScreen extends ConsumerWidget {
  const MacScreen({super.key});

  Future<void> _run(BuildContext context, WidgetRef ref, String args, {String? busyLabel}) async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    final sid = await ref.read(sessionIdProvider.future);
    final messenger = ScaffoldMessenger.of(context);
    messenger.showSnackBar(SnackBar(
        content: Text(busyLabel ?? 'Running $args…'), duration: const Duration(seconds: 2)));
    try {
      final res = await api.run('mac', args, sid);
      messenger.showSnackBar(SnackBar(content: Text(res, maxLines: 3),
          backgroundColor: res.startsWith('[mac') ? GajalaColors.danger : GajalaColors.ok));
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text(friendlyError(e)),
          backgroundColor: GajalaColors.danger));
    }
  }

  Future<void> _textThen(BuildContext context, WidgetRef ref, String verb, String hint) async {
    final ctrl = TextEditingController();
    final ok = await showDialog<bool>(context: context, builder: (c) => AlertDialog(
      backgroundColor: context.pal.surface,
      title: Text(verb),
      content: TextField(controller: ctrl, autofocus: true,
          decoration: InputDecoration(hintText: hint)),
      actions: [
        TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Send')),
      ],
    ));
    if (ok == true && ctrl.text.trim().isNotEmpty && context.mounted) {
      await _run(context, ref, '${verb.toLowerCase()} ${ctrl.text.trim()}');
    }
  }

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final actions = <(IconData, String, VoidCallback)>[
      (Icons.lock, 'Lock', () => _run(context, ref, 'lock')),
      (Icons.bedtime, 'Sleep', () => _run(context, ref, 'sleep')),
      (Icons.bluetooth, 'BT On', () => _run(context, ref, 'bluetooth on')),
      (Icons.bluetooth_disabled, 'BT Off', () => _run(context, ref, 'bluetooth off')),
      (Icons.record_voice_over, 'Say…', () => _textThen(context, ref, 'Say', 'Text to speak aloud')),
      (Icons.notifications, 'Notify…', () => _textThen(context, ref, 'Notify', 'On-screen banner text')),
      (Icons.screenshot_monitor, 'Screenshot', () => _run(context, ref, 'screenshot', busyLabel: 'Capturing → Telegram…')),
    ];
    return Scaffold(
      appBar: AppBar(title: const Text('Mac Control')),
      body: ListView(
        padding: const EdgeInsets.all(14),
        children: [
          Text('Control the Mac remotely. Screenshots are pushed to your Telegram.',
              style: TextStyle(color: context.pal.textDim, fontSize: 13)),
          const SizedBox(height: 14),
          GridView.count(
            crossAxisCount: 3,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            mainAxisSpacing: 10, crossAxisSpacing: 10, childAspectRatio: 1,
            children: actions.map((a) => Card(
              child: InkWell(
                borderRadius: BorderRadius.circular(18),
                onTap: a.$3,
                child: Column(mainAxisAlignment: MainAxisAlignment.center, children: [
                  Icon(a.$1, color: GajalaColors.accent, size: 30),
                  const SizedBox(height: 8),
                  Text(a.$2, style: const TextStyle(fontWeight: FontWeight.w600)),
                ]),
              ),
            )).toList(),
          ),
        ],
      ),
    );
  }
}
