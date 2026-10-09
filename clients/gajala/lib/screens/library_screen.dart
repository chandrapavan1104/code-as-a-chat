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
          padding: const EdgeInsets.all(12),
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
              Padding(
                padding: const EdgeInsets.fromLTRB(12, 16, 12, 4),
                child: Text(
                  group.key,
                  style: Theme.of(context).textTheme.titleMedium,
                ),
              ),
              for (final skill in items.where(
                (s) => group.value.contains(s.name),
              ))
                _entry(context, skill),
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
    title: Text(switch (skill.name) {
      'notes' => 'Notes',
      'sysmon' => 'System',
      'usage' => 'Usage',
      'filemanager' => 'Files',
      _ => skill.name[0].toUpperCase() + skill.name.substring(1),
    }),
    trailing: const Icon(Icons.chevron_right),
    onTap: () => Navigator.of(
      context,
    ).push(MaterialPageRoute(builder: (_) => screenForSkill(skill))),
  );
}
