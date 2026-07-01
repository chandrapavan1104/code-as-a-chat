import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/state.dart';
import '../core/theme.dart';

class ProjectsScreen extends ConsumerWidget {
  const ProjectsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final data = ref.watch(projectsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Projects')),
      body: data.when(
        data: (d) {
          final projects = (d['projects'] as List?) ?? [];
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(projectsProvider),
            child: ListView(
              padding: const EdgeInsets.all(12),
              children: [
                Text('Active workspace — all Gajala/Claude/file actions run here.',
                    style: TextStyle(color: context.pal.textDim, fontSize: 13)),
                const SizedBox(height: 12),
                ...projects.map((p) => _ProjectTile(p['name'], p['active'] == true, ref)),
              ],
            ),
          );
        },
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(child: Text(friendlyError(e),
            style: const TextStyle(color: GajalaColors.danger))),
      ),
    );
  }
}

class _ProjectTile extends StatelessWidget {
  final String name;
  final bool active;
  final WidgetRef ref;
  const _ProjectTile(this.name, this.active, this.ref);

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      color: active ? GajalaColors.accentDim.withValues(alpha: .25) : null,
      child: ListTile(
        leading: Icon(active ? Icons.folder_open : Icons.folder,
            color: active ? GajalaColors.accent : context.pal.textDim),
        title: Text(name,
            style: TextStyle(fontWeight: active ? FontWeight.w700 : FontWeight.w400)),
        trailing: active
            ? const Icon(Icons.check_circle, color: GajalaColors.ok, size: 20)
            : const Icon(Icons.chevron_right, color: GajalaColors.accent),
        onTap: active ? null : () async {
          final api = ref.read(apiProvider);
          try {
            final cur = await api!.switchProject(name);
            ref.invalidate(projectsProvider);
            if (context.mounted) {
              ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text('Switched to $cur'), backgroundColor: GajalaColors.ok));
            }
          } catch (e) {
            if (context.mounted) {
              ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(content: Text(friendlyError(e)), backgroundColor: GajalaColors.danger));
            }
          }
        },
      ),
    );
  }
}
