import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/state.dart';
import '../core/theme.dart';

class RemindersScreen extends ConsumerWidget {
  const RemindersScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final rem = ref.watch(remindersProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Reminders')),
      body: rem.when(
        data: (list) {
          if (list.isEmpty) {
            return Center(child: Text('No reminders ra ⏰',
                style: TextStyle(color: context.pal.textDim)));
          }
          list.sort((a, b) => (a['due_at'] as num).compareTo(b['due_at'] as num));
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(remindersProvider),
            child: ListView.builder(
              padding: const EdgeInsets.all(12),
              itemCount: list.length,
              itemBuilder: (_, i) => _ReminderTile(list[i], ref),
            ),
          );
        },
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text(friendlyError(e),
            style: const TextStyle(color: GajalaColors.danger))),
      ),
      floatingActionButton: FloatingActionButton(
        backgroundColor: GajalaColors.accent,
        onPressed: () => _add(context, ref),
        child: const Icon(Icons.add, color: Colors.white),
      ),
    );
  }

  static String _fmt(num epoch) {
    final d = DateTime.fromMillisecondsSinceEpoch((epoch * 1000).toInt());
    final now = DateTime.now();
    final diff = d.difference(now);
    final when = '${d.day}/${d.month} ${d.hour.toString().padLeft(2,'0')}:${d.minute.toString().padLeft(2,'0')}';
    if (diff.isNegative) return '$when (overdue)';
    if (diff.inHours < 24) return '$when (in ${diff.inHours}h ${diff.inMinutes % 60}m)';
    return '$when (in ${diff.inDays}d)';
  }

  Future<void> _add(BuildContext context, WidgetRef ref) async {
    final text = TextEditingController();
    final ok = await showDialog<bool>(context: context, builder: (c) => AlertDialog(
      backgroundColor: context.pal.surface,
      title: const Text('New reminder'),
      content: TextField(controller: text, autofocus: true,
          decoration: const InputDecoration(hintText: 'What to remind you about')),
      actions: [
        TextButton(onPressed: () => Navigator.pop(c, false), child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(c, true), child: const Text('Pick time')),
      ],
    ));
    if (ok != true || text.text.trim().isEmpty || !context.mounted) return;

    final date = await showDatePicker(context: context, initialDate: DateTime.now(),
        firstDate: DateTime.now(), lastDate: DateTime.now().add(const Duration(days: 365)));
    if (date == null || !context.mounted) return;
    final time = await showTimePicker(context: context, initialTime: TimeOfDay.now());
    if (time == null) return;

    final due = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    final api = ref.read(apiProvider);
    try {
      await api!.createReminder(text.text.trim(), due.millisecondsSinceEpoch / 1000);
      ref.invalidate(remindersProvider);
    } catch (e) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text(friendlyError(e)), backgroundColor: GajalaColors.danger));
      }
    }
  }
}

class _ReminderTile extends StatelessWidget {
  final Map<String, dynamic> r;
  final WidgetRef ref;
  const _ReminderTile(this.r, this.ref);
  @override
  Widget build(BuildContext context) {
    return Dismissible(
      key: ValueKey(r['id']),
      direction: DismissDirection.endToStart,
      background: Container(
        color: GajalaColors.danger.withValues(alpha: .25),
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.symmetric(horizontal: 20),
        child: const Icon(Icons.delete, color: GajalaColors.danger),
      ),
      onDismissed: (_) async {
        await ref.read(apiProvider)?.deleteReminder(r['id']);
        ref.invalidate(remindersProvider);
      },
      child: Card(
        margin: const EdgeInsets.only(bottom: 10),
        child: ListTile(
          leading: const Icon(Icons.alarm, color: GajalaColors.accent),
          title: Text(r['text']?.toString() ?? ''),
          subtitle: Text(RemindersScreen._fmt(r['due_at']),
              style: TextStyle(color: context.pal.textDim, fontSize: 12)),
        ),
      ),
    );
  }
}
