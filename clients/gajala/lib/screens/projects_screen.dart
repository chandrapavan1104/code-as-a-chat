import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';

/// The active workspace picker.
///
/// Rows show the real path and git state, not just a folder name: ~/Projects
/// holds thirty directories, several of which are not repos at all, and a bare
/// name gave no way to tell which one you were about to point every coding
/// agent at.
class ProjectsScreen extends ConsumerStatefulWidget {
  const ProjectsScreen({super.key});
  @override
  ConsumerState<ProjectsScreen> createState() => _ProjectsScreenState();
}

class _ProjectsScreenState extends ConsumerState<ProjectsScreen> {
  String _filter = '';

  @override
  Widget build(BuildContext context) {
    final data = ref.watch(projectsProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Projects')),
      body: data.when(
        data: (d) {
          final all = ((d['projects'] as List?) ?? [])
              .map((p) => Project.fromJson(Map<String, dynamic>.from(p)))
              .toList();
          final q = _filter.trim().toLowerCase();
          final shown = q.isEmpty
              ? all
              : all
                  .where((p) =>
                      p.name.toLowerCase().contains(q) ||
                      p.displayPath.toLowerCase().contains(q))
                  .toList();

          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(projectsProvider),
            child: ListView(
              padding: const EdgeInsets.all(12),
              children: [
                Text(
                    'Active workspace — all Gajala/Claude/file actions run here.\n'
                    'Looking in ${d['parent_dir'] ?? '~/Projects'}',
                    style: TextStyle(color: context.pal.textDim, fontSize: 13)),
                const SizedBox(height: 10),
                if (all.length > 8)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: TextField(
                      onChanged: (v) => setState(() => _filter = v),
                      decoration: const InputDecoration(
                        isDense: true,
                        prefixIcon: Icon(Icons.search, size: 18),
                        hintText: 'Filter projects',
                      ),
                    ),
                  ),
                ...shown.map((p) => _ProjectTile(p, ref)),
                if (shown.isEmpty)
                  Padding(
                    padding: const EdgeInsets.symmetric(vertical: 24),
                    child: Center(
                      child: Text('No project matches "$_filter"',
                          style: TextStyle(color: context.pal.textDim)),
                    ),
                  ),
              ],
            ),
          );
        },
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
            child: Text(friendlyError(e),
                style: const TextStyle(color: GajalaColors.danger))),
      ),
    );
  }
}

class _ProjectTile extends StatelessWidget {
  final Project p;
  final WidgetRef ref;
  const _ProjectTile(this.p, this.ref);

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      color: p.active ? GajalaColors.accentDim.withValues(alpha: .25) : null,
      child: ListTile(
        isThreeLine: true,
        leading: Icon(p.active ? Icons.folder_open : Icons.folder,
            color: p.active ? GajalaColors.accent : pal.textDim),
        title: Row(children: [
          Flexible(
            child: Text(p.name,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                    fontWeight: p.active ? FontWeight.w700 : FontWeight.w400)),
          ),
          if (p.active)
            Padding(
              padding: const EdgeInsets.only(left: 8),
              child: Text('ACTIVE',
                  style: TextStyle(
                      fontSize: 10,
                      letterSpacing: .8,
                      fontWeight: FontWeight.w700,
                      color: GajalaColors.ok)),
            ),
        ]),
        subtitle: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          const SizedBox(height: 2),
          Text(p.displayPath,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                  fontSize: 12, color: pal.textDim, fontFamily: 'monospace')),
          const SizedBox(height: 2),
          Text(p.gitLine,
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: TextStyle(
                  fontSize: 11.5,
                  color: p.isGit ? pal.textDim : GajalaColors.warn)),
        ]),
        trailing: p.active
            ? const Icon(Icons.check_circle, color: GajalaColors.ok, size: 20)
            : const Icon(Icons.chevron_right, color: GajalaColors.accent),
        onTap: p.active ? null : () => _switch(context),
      ),
    );
  }

  Future<void> _switch(BuildContext context) async {
    final api = ref.read(apiProvider);
    final messenger = ScaffoldMessenger.of(context);
    try {
      final cur = await api!.switchProject(p.name);
      ref.invalidate(projectsProvider);
      messenger.showSnackBar(SnackBar(
          content: Text('Switched to $cur'), backgroundColor: GajalaColors.ok));
    } on ProjectSwitchError catch (e) {
      // A switch that did not happen must not look like one that did — this
      // endpoint used to answer 200 with the OLD project name.
      messenger.showSnackBar(SnackBar(
          content: Text(e.toString()), backgroundColor: GajalaColors.danger));
    } catch (e) {
      messenger.showSnackBar(SnackBar(
          content: Text(friendlyError(e)), backgroundColor: GajalaColors.danger));
    }
  }
}
