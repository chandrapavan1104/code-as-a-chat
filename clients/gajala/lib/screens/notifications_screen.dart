import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';
import '../core/update.dart';
import 'home_shell.dart';

/// Alerts tab — the durable inbox of everything Gajala tells you: needs-input
/// questions, job status, night reports, "new build ready", reminders.
class NotificationsScreen extends ConsumerWidget {
  const NotificationsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final data = ref.watch(notificationsProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Alerts'),
        actions: [
          TextButton(
            onPressed: () async {
              await ref.read(apiProvider)?.markAllRead();
              ref.invalidate(notificationsProvider);
              ref.invalidate(unreadCountProvider);
            },
            child: const Text('Mark all read'),
          ),
        ],
      ),
      body: data.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
            child: Text(friendlyError(e),
                style: const TextStyle(color: GajalaColors.danger))),
        data: (d) {
          final items = d.items
              .where((n) => n.status != 'dismissed')
              .toList();
          if (items.isEmpty) {
            return Center(
                child: Text('No alerts.',
                    style: TextStyle(color: context.pal.textDim)));
          }
          return RefreshIndicator(
            onRefresh: () async {
              ref.invalidate(notificationsProvider);
              ref.invalidate(unreadCountProvider);
            },
            child: ListView.builder(
              padding: const EdgeInsets.all(12),
              itemCount: items.length,
              itemBuilder: (_, i) => _NotifTile(items[i]),
            ),
          );
        },
      ),
    );
  }
}

({Color color, IconData icon}) _typeStyle(String type) {
  switch (type) {
    case 'queue_input':
      return (color: GajalaColors.amber, icon: Icons.help_outline);
    case 'queue_status':
      return (color: GajalaColors.violet, icon: Icons.task_alt);
    case 'night_report':
      return (color: GajalaColors.blue, icon: Icons.nightlight_round);
    case 'gajala_update':
      return (color: GajalaColors.green, icon: Icons.system_update);
    case 'reminder':
      return (color: GajalaColors.pink, icon: Icons.alarm);
    default:
      return (color: GajalaColors.teal, icon: Icons.notifications);
  }
}

class _NotifTile extends ConsumerWidget {
  final AppNotification n;
  const _NotifTile(this.n);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final st = _typeStyle(n.type);
    final pal = context.pal;
    final answered = n.status == 'answered';
    return Dismissible(
      key: ValueKey('notif-${n.id}'),
      direction: DismissDirection.endToStart,
      background: Container(
        alignment: Alignment.centerRight,
        padding: const EdgeInsets.symmetric(horizontal: 20),
        margin: const EdgeInsets.only(bottom: 8),
        decoration: BoxDecoration(
            color: GajalaColors.danger.withValues(alpha: .16),
            borderRadius: BorderRadius.circular(12)),
        child: const Icon(Icons.delete, color: GajalaColors.danger),
      ),
      onDismissed: (_) async {
        await ref.read(apiProvider)?.dismissNotification(n.id);
        ref.invalidate(notificationsProvider);
        ref.invalidate(unreadCountProvider);
      },
      child: Container(
        margin: const EdgeInsets.only(bottom: 8),
        decoration: BoxDecoration(
          color: n.isUnread ? st.color.withValues(alpha: .08) : pal.surface,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
              color: n.isUnread ? st.color.withValues(alpha: .35) : pal.border),
        ),
        child: ListTile(
          leading: Icon(st.icon, color: st.color),
          title: Text(n.title,
              style: TextStyle(
                  fontWeight: n.isUnread ? FontWeight.w700 : FontWeight.w500,
                  fontSize: 14)),
          subtitle: Text(
              answered && n.response != null && n.response!.isNotEmpty
                  ? 'You answered: ${n.response}'
                  : n.body,
              maxLines: 4, overflow: TextOverflow.ellipsis,
              style: TextStyle(fontSize: 12.5, color: pal.textDim)),
          trailing: n.needsResponse && !answered
              ? Icon(Icons.reply, color: st.color, size: 20)
              : null,
          isThreeLine: true,
          onTap: () => _onTap(context, ref),
        ),
      ),
    );
  }

  Future<void> _onTap(BuildContext context, WidgetRef ref) async {
    final api = ref.read(apiProvider);
    if (n.isUnread) {
      await api?.markRead(n.id);
      ref.invalidate(notificationsProvider);
      ref.invalidate(unreadCountProvider);
    }
    if (!context.mounted) return;
    switch (n.type) {
      case 'queue_input':
        if (n.status != 'answered') await _answerSheet(context, ref);
        break;
      case 'queue_status':
      case 'night_report':
        context.findAncestorStateOfType<HomeShellState>()?.go(1); // Tasks
        break;
      case 'gajala_update':
        await _runUpdate(context, ref);
        break;
    }
  }

  Future<void> _answerSheet(BuildContext context, WidgetRef ref) async {
    final reply = TextEditingController();
    await showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      backgroundColor: context.pal.surface,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
      builder: (ctx) => Padding(
        padding: EdgeInsets.fromLTRB(
            20, 18, 20, MediaQuery.of(ctx).viewInsets.bottom + 20),
        child: Column(mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(n.title, style: Theme.of(ctx).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text(n.body, style: TextStyle(color: context.pal.textDim, fontSize: 13)),
          const SizedBox(height: 14),
          TextField(controller: reply, autofocus: true, minLines: 1, maxLines: 4,
              decoration: const InputDecoration(
                  hintText: 'Your answer — the agent continues with it')),
          const SizedBox(height: 16),
          SizedBox(width: double.infinity, child: FilledButton.icon(
            icon: const Icon(Icons.send),
            label: const Text('Send & re-run'),
            onPressed: () async {
              if (reply.text.trim().isEmpty) return;
              try {
                await ref.read(apiProvider)
                    ?.respondNotification(n.id, reply.text.trim());
                ref.invalidate(notificationsProvider);
                ref.invalidate(unreadCountProvider);
                ref.invalidate(queueProvider);
                if (ctx.mounted) Navigator.pop(ctx);
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(const SnackBar(
                      content: Text('Sent — the task is running with your answer.')));
                }
              } catch (e) {
                if (ctx.mounted) {
                  ScaffoldMessenger.of(ctx).showSnackBar(SnackBar(
                      content: Text(friendlyError(e)),
                      backgroundColor: GajalaColors.danger));
                }
              }
            },
          )),
        ]),
      ),
    );
  }

  Future<void> _runUpdate(BuildContext context, WidgetRef ref) async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    final messenger = ScaffoldMessenger.of(context);
    final info = await UpdateService.check(api);
    if (info == null) {
      messenger.showSnackBar(const SnackBar(content: Text('Already up to date.')));
      return;
    }
    messenger.showSnackBar(const SnackBar(content: Text('Downloading update…')));
    final err = await UpdateService.downloadAndInstall(api, info, (_) {});
    if (err != null) {
      messenger.showSnackBar(SnackBar(
          content: Text('Update failed: $err'),
          backgroundColor: GajalaColors.danger));
    }
  }
}
