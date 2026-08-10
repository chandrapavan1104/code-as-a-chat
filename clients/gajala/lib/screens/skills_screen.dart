import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';

// Icon map for skills (shared with dashboard)
const _icons = {
  'claude': Icons.code, 'codex': Icons.terminal, 'antigravity': Icons.auto_awesome,
  'sysmon': Icons.memory, 'filemanager': Icons.folder, 'sessions': Icons.history,
  'projects': Icons.work, 'ports': Icons.lan, 'firebase': Icons.local_fire_department,
  'usage': Icons.bar_chart, 'notes': Icons.sticky_note_2, 'context': Icons.description,
  'reminders': Icons.alarm, 'mac': Icons.desktop_mac, 'diary': Icons.menu_book,
  'qwen': Icons.hub, 'auth': Icons.lock_outline, 'build': Icons.build_circle,
  'fix': Icons.bug_report, 'model': Icons.tune, 'queue': Icons.queue,
};

class SkillsScreen extends ConsumerWidget {
  const SkillsScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final skills = ref.watch(skillsProvider);
    final pal = context.pal;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Skills'),
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: () => ref.invalidate(skillsProvider),
          ),
        ],
      ),
      body: skills.when(
        data: (list) => _SkillsList(list: list),
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Padding(
            padding: const EdgeInsets.all(20),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(Icons.error_outline, size: 48, color: pal.danger),
                const SizedBox(height: 16),
                Text('Failed to load skills: $e',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: pal.danger)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _SkillsList extends ConsumerWidget {
  final List<Skill> list;
  const _SkillsList({required this.list});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    // Group skills by expose_to_agent status for clarity
    final exposed = list.where((s) => s.exposeToAgent).toList();
    final notExposed = list.where((s) => !s.exposeToAgent).toList();

    return RefreshIndicator(
      onRefresh: () async => ref.refresh(skillsProvider),
      child: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
          if (exposed.isNotEmpty) ...[
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
              child: Text('Agent-enabled skills',
                  style: Theme.of(context).textTheme.titleSmall),
            ),
            for (final skill in exposed)
              _SkillTile(skill: skill, ref: ref),
          ],
          if (notExposed.isNotEmpty) ...[
            if (exposed.isNotEmpty) const SizedBox(height: 12),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
              child: Text('Command-only skills',
                  style: Theme.of(context).textTheme.titleSmall),
            ),
            for (final skill in notExposed)
              _SkillTile(skill: skill, ref: ref),
          ],
        ],
      ),
    );
  }
}

class _SkillTile extends ConsumerStatefulWidget {
  final Skill skill;
  final WidgetRef ref;

  const _SkillTile({required this.skill, required this.ref});

  @override
  ConsumerState<_SkillTile> createState() => _SkillTileState();
}

class _SkillTileState extends ConsumerState<_SkillTile> {
  late bool _enabled;
  bool _toggling = false;

  @override
  void initState() {
    super.initState();
    _enabled = widget.skill.enabled;
  }

  Future<void> _toggleSkill(bool newValue) async {
    if (_toggling) return;
    setState(() {
      _toggling = true;
      _enabled = newValue;
    });

    try {
      final api = ref.read(apiProvider);
      if (api == null) throw 'API not available';

      await api.toggleSkill(widget.skill.name, newValue);
      // Refresh the skills list to confirm the change
      ref.invalidate(skillsProvider);

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              '${widget.skill.name} is now ${newValue ? 'enabled' : 'disabled'}',
              style: const TextStyle(color: Colors.white),
            ),
            backgroundColor: newValue ? Colors.green : Colors.orange,
            duration: const Duration(seconds: 2),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        setState(() => _enabled = !newValue);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text('Failed to toggle ${widget.skill.name}: $e'),
            backgroundColor: Colors.red,
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _toggling = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    return ListTile(
      contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
      leading: Icon(
        _icons[widget.skill.name] ?? Icons.bolt,
        color: _enabled
            ? GajalaColors.forSkill(widget.skill.name)
            : pal.textDim,
        size: 22,
      ),
      title: Text(
        widget.skill.name,
        style: TextStyle(
          fontWeight: FontWeight.w500,
          color: _enabled ? pal.text : pal.textDim,
        ),
      ),
      subtitle: Text(
        widget.skill.helpLine,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(color: _enabled ? pal.textDim : pal.textDim2),
      ),
      trailing: Switch(
        value: _enabled,
        onChanged: _toggling ? null : _toggleSkill,
        activeColor: GajalaColors.accent,
      ),
      enabled: !_toggling,
    );
  }
}
