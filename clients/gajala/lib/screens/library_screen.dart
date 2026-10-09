import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/models.dart';
import '../core/api.dart';
import '../core/state.dart';
import 'dashboard_screen.dart';
import 'settings_screen.dart';

/// A stable home for saved information and less frequent controls.
class LibraryScreen extends ConsumerWidget {
  const LibraryScreen({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final skills = ref.watch(skillsProvider);
    return Scaffold(
      appBar: AppBar(
        title: const Text('Library'),
        actions: [
          IconButton(
            tooltip: 'Settings',
            icon: const Icon(Icons.settings_outlined),
            onPressed: () => Navigator.of(
              context,
            ).push(MaterialPageRoute(builder: (_) => const SettingsScreen())),
          ),
        ],
      ),
      body: skills.when(
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Text(friendlyError(e)),
              TextButton(
                onPressed: () => ref.invalidate(skillsProvider),
                child: const Text('Retry'),
              ),
            ],
          ),
        ),
        data: (items) => ListView(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 12),
          children: [
            for (final group in <String, Set<String>>{
              'Saved information': {
                'notes',
                'diary',
                'reminders',
                'filemanager',
              },
              'Projects and agents': {
                'projects',
                'sessions',
                'claude',
                'codex',
                'antigravity',
              },
              'Devices and system': {'mac', 'sysmon', 'usage', 'ports'},
            }.entries) ...[
              if (items.any((s) => group.value.contains(s.name)))
                Padding(
                  padding: const EdgeInsets.fromLTRB(0, 12, 0, 6),
                  child: Text(
                    group.key,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              if (items.any((s) => group.value.contains(s.name)))
                Card(
                  margin: EdgeInsets.zero,
                  clipBehavior: Clip.antiAlias,
                  child: Column(
                    children: [
                      for (final name in group.value)
                        for (final skill in items.where((s) => s.name == name))
                          _entry(context, skill),
                    ],
                  ),
                ),
            ],
            ListTile(
              leading: const Icon(Icons.dashboard_outlined),
              title: const Text('Quick actions'),
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(builder: (_) => const DashboardScreen()),
              ),
            ),
            ListTile(
              leading: const Icon(Icons.apps),
              title: const Text('All skills'),
              onTap: () => showAllSkillsSheet(context, items),
            ),
          ],
        ),
      ),
    );
  }

  Widget _entry(BuildContext context, Skill skill) => ListTile(
    dense: true,
    minTileHeight: 48,
    visualDensity: VisualDensity.standard,
    contentPadding: const EdgeInsets.symmetric(horizontal: 12),
    leading: Icon(switch (skill.name) {
      'notes' => Icons.sticky_note_2_outlined,
      'diary' => Icons.menu_book_outlined,
      'reminders' => Icons.notifications_outlined,
      'filemanager' => Icons.folder_outlined,
      'projects' => Icons.work_outline,
      'sessions' => Icons.history,
      'claude' => Icons.code,
      'codex' => Icons.terminal,
      'antigravity' => Icons.auto_awesome_outlined,
      'ports' => Icons.lan_outlined,
      'mac' => Icons.desktop_mac_outlined,
      'usage' => Icons.bar_chart,
      _ => Icons.memory_outlined,
    }, size: 20),
    title: Text(switch (skill.name) {
      'notes' => 'Notes',
      'sysmon' => 'System',
      'usage' => 'Usage',
      'filemanager' => 'Files',
      _ => skill.name[0].toUpperCase() + skill.name.substring(1),
    }),
    trailing: const Icon(Icons.chevron_right, size: 20),
    onTap: () => Navigator.of(
      context,
    ).push(MaterialPageRoute(builder: (_) => screenForSkill(skill))),
  );
}
