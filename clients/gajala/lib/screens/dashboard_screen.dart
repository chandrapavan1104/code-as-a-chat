import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';
import '../core/update.dart';
import 'chat_screen.dart';
import 'notes_screen.dart';
import 'system_screen.dart';
import 'reminders_screen.dart';
import 'diary_screen.dart';
import 'projects_screen.dart';
import 'usage_screen.dart';
import 'mac_screen.dart';

// Display-label overrides for skill tiles (the screen behind is unchanged).
const _tileLabels = {'usage': 'codaur', 'notes': 'brain dump'};

const _icons = {
  'claude': Icons.code, 'codex': Icons.terminal, 'antigravity': Icons.auto_awesome,
  'sysmon': Icons.memory, 'filemanager': Icons.folder, 'sessions': Icons.history,
  'projects': Icons.work, 'ports': Icons.lan, 'firebase': Icons.local_fire_department,
  'usage': Icons.bar_chart, 'notes': Icons.sticky_note_2, 'context': Icons.description,
  'reminders': Icons.alarm, 'mac': Icons.desktop_mac, 'diary': Icons.menu_book,
};

class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final skills = ref.watch(skillsProvider);
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 16,
        title: Row(children: [
          const Text('Gajala', style: TextStyle(fontWeight: FontWeight.w800)),
          const SizedBox(width: 8),
          const _StatusDot(),
        ]),
        actions: [
          _ThemeToggle(ref),
          IconButton(
            icon: const Icon(Icons.logout),
            tooltip: 'Disconnect',
            onPressed: () => ref.read(configProvider.notifier).disconnect(),
          ),
        ],
      ),
      // Samsung-launcher style: scrollable icon grid filling the screen,
      // with a fixed "Ask Gajala" input bar pinned at the bottom.
      body: Column(
        children: [
          Expanded(
            child: RefreshIndicator(
              onRefresh: () async {
                ref.invalidate(systemProvider);
                ref.invalidate(skillsProvider);
              },
              child: ListView(
                padding: const EdgeInsets.all(14),
                children: [
                  const _UpdateBanner(),
                  const _SystemCard(),
                  skills.when(
                    data: (list) => _SkillSections(list),
                    loading: () => const Padding(
                        padding: EdgeInsets.all(40), child: Center(child: CircularProgressIndicator())),
                    error: (e, _) => Padding(
                        padding: const EdgeInsets.all(20),
                        child: Text('Couldn\'t load skills: $e',
                            style: const TextStyle(color: GajalaColors.danger))),
                  ),
                  const SizedBox(height: 8),
                ],
              ),
            ),
          ),
          const _AskBar(),
        ],
      ),
    );
  }
}

/// Cycles theme mode: system → light → dark → system.
class _ThemeToggle extends StatelessWidget {
  final WidgetRef ref;
  const _ThemeToggle(this.ref);
  @override
  Widget build(BuildContext context) {
    final mode = ref.watch(themeModeProvider);
    final (icon, next) = switch (mode) {
      ThemeMode.system => (Icons.brightness_auto, ThemeMode.light),
      ThemeMode.light => (Icons.light_mode, ThemeMode.dark),
      ThemeMode.dark => (Icons.dark_mode, ThemeMode.system),
    };
    return IconButton(
      icon: Icon(icon),
      tooltip: 'Theme: ${mode.name}',
      onPressed: () => ref.read(themeModeProvider.notifier).set(next),
    );
  }
}

/// Fixed bottom bar styled like a chat input — tap to open the Gajala chat.
class _AskBar extends StatelessWidget {
  const _AskBar();
  @override
  Widget build(BuildContext context) {
    return SafeArea(
      top: false,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(12, 6, 12, 10),
        child: Material(
          color: context.pal.surface,
          borderRadius: BorderRadius.circular(26),
          child: InkWell(
            borderRadius: BorderRadius.circular(26),
            onTap: () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => const ChatScreen(command: 'shell', title: 'Gajala'))),
            child: Container(
              decoration: BoxDecoration(
                borderRadius: BorderRadius.circular(26),
                border: Border.all(color: context.pal.border),
              ),
              padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
              child: Row(children: [
                const Icon(Icons.auto_awesome, color: GajalaColors.accent, size: 20),
                const SizedBox(width: 12),
                Expanded(child: Text('Ask Gajala…',
                    style: TextStyle(color: context.pal.textDim, fontSize: 15))),
                CircleAvatar(radius: 16, backgroundColor: GajalaColors.accent,
                    child: const Icon(Icons.arrow_upward, color: Colors.white, size: 18)),
              ]),
            ),
          ),
        ),
      ),
    );
  }
}

/// "Update available" banner — checks the server for a newer build and installs
/// it in-app with a tap (download → system installer).
class _UpdateBanner extends ConsumerStatefulWidget {
  const _UpdateBanner();
  @override
  ConsumerState<_UpdateBanner> createState() => _UpdateBannerState();
}

class _UpdateBannerState extends ConsumerState<_UpdateBanner> {
  UpdateInfo? _update;
  bool _downloading = false;
  double _progress = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _check());
  }

  Future<void> _check() async {
    final api = ref.read(apiProvider);
    if (api == null) return;
    final u = await UpdateService.check(api);
    if (mounted) setState(() => _update = u);
  }

  Future<void> _install() async {
    final api = ref.read(apiProvider);
    final u = _update;
    if (api == null || u == null || _downloading) return;
    setState(() { _downloading = true; _progress = 0; });
    final err = await UpdateService.downloadAndInstall(api, u, (p) {
      if (mounted) setState(() => _progress = p);
    });
    if (!mounted) return;
    setState(() => _downloading = false);
    if (err != null) {
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Update failed: $err')));
    }
  }

  @override
  Widget build(BuildContext context) {
    final u = _update;
    if (u == null) return const SizedBox.shrink();
    final pal = context.pal;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 12),
      decoration: BoxDecoration(
        color: GajalaColors.accentDim.withValues(alpha: .18),
        borderRadius: BorderRadius.circular(14),
        border: Border.all(color: GajalaColors.accent.withValues(alpha: .5)),
      ),
      child: Row(children: [
        const Icon(Icons.system_update, color: GajalaColors.accent, size: 22),
        const SizedBox(width: 12),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            const Text('Update available',
                style: TextStyle(fontWeight: FontWeight.w700)),
            Text(
                _downloading
                    ? 'Downloading ${(_progress * 100).toStringAsFixed(0)}%…'
                    : 'A newer Gajala build is ready',
                style: TextStyle(fontSize: 12, color: pal.textDim)),
          ]),
        ),
        _downloading
            ? SizedBox(
                width: 22, height: 22,
                child: CircularProgressIndicator(
                    value: _progress > 0 ? _progress : null, strokeWidth: 2.5))
            : FilledButton(onPressed: _install, child: const Text('Update')),
      ]),
    );
  }
}

/// Rich native screen for skills that have one; chat fallback for the rest.
Widget _screenFor(Skill s) {
  switch (s.name) {
    case 'notes': return const NotesScreen();
    case 'sysmon': return const SystemScreen();
    case 'reminders': return const RemindersScreen();
    case 'diary': return const DiaryScreen();
    case 'projects': return const ProjectsScreen();
    case 'usage': return const UsageScreen();
    case 'mac': return const MacScreen();
    default: return ChatScreen(command: s.command, title: s.name);
  }
}

String _labelFor(Skill s) => _tileLabels[s.name] ?? s.command;

/// Home = the tiles you pinned; everything else lives behind "All skills".
/// Long-press any tile to pin/unpin; "Edit" opens a drag-to-reorder sheet.
class _SkillSections extends ConsumerStatefulWidget {
  final List<Skill> all;
  const _SkillSections(this.all);
  @override
  ConsumerState<_SkillSections> createState() => _SkillSectionsState();
}

class _SkillSectionsState extends ConsumerState<_SkillSections> {
  bool _showAll = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      ref.read(favoritesProvider.notifier)
          .seedIfEmpty(widget.all.map((s) => s.name).toList());
    });
  }

  @override
  Widget build(BuildContext context) {
    final favs = ref.watch(favoritesProvider);
    final byName = {for (final s in widget.all) s.name: s};
    final pinned = [for (final n in favs) if (byName[n] != null) byName[n]!];
    final rest = widget.all.where((s) => !favs.contains(s.name)).toList();

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      _header(context, 'PINNED', action: pinned.isEmpty ? null : 'Edit',
          onAction: () => _editSheet(context)),
      if (pinned.isEmpty)
        Padding(
          padding: const EdgeInsets.symmetric(vertical: 10),
          child: Text('Long-press any skill below to pin it here.',
              style: Theme.of(context).textTheme.bodySmall),
        )
      else
        _grid(pinned, favs),
      _header(context, 'ALL SKILLS',
          action: _showAll ? 'Hide' : 'Show ${rest.length}',
          onAction: () => setState(() => _showAll = !_showAll)),
      if (_showAll) _grid(rest, favs),
    ]);
  }

  Widget _header(BuildContext context, String title,
      {String? action, VoidCallback? onAction}) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(2, 18, 2, 10),
      child: Row(children: [
        Text(title, style: Theme.of(context).textTheme.labelSmall),
        const Spacer(),
        if (action != null)
          InkWell(
            onTap: onAction,
            borderRadius: BorderRadius.circular(6),
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
              child: Text(action,
                  style: const TextStyle(
                      fontSize: 12, color: GajalaColors.accent, fontWeight: FontWeight.w600)),
            ),
          ),
      ]),
    );
  }

  Widget _grid(List<Skill> items, List<String> favs) => GridView.count(
        crossAxisCount: 4,
        shrinkWrap: true,
        physics: const NeverScrollableScrollPhysics(),
        mainAxisSpacing: 10, crossAxisSpacing: 10, childAspectRatio: .88,
        children: [
          for (final s in items)
            _SkillCard(s,
                pinned: favs.contains(s.name),
                onLongPress: () async {
                  final messenger = ScaffoldMessenger.of(context);
                  final wasPinned = favs.contains(s.name);
                  await ref.read(favoritesProvider.notifier).toggle(s.name);
                  messenger.showSnackBar(SnackBar(
                    duration: const Duration(seconds: 2),
                    content: Text(wasPinned
                        ? 'Unpinned ${_labelFor(s)}'
                        : 'Pinned ${_labelFor(s)}'),
                  ));
                }),
        ],
      );

  void _editSheet(BuildContext context) {
    showModalBottomSheet(
      context: context,
      backgroundColor: context.pal.surface,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
      builder: (_) => Consumer(builder: (c, r, _) {
        final favs = r.watch(favoritesProvider);
        final byName = {for (final s in widget.all) s.name: s};
        return SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 14, 16, 16),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Row(children: [
                Text('Pinned skills', style: Theme.of(c).textTheme.titleMedium),
                const Spacer(),
                Text('drag to reorder', style: Theme.of(c).textTheme.bodySmall),
              ]),
              const SizedBox(height: 10),
              Flexible(
                child: ReorderableListView(
                  shrinkWrap: true,
                  onReorderItem: (a, b) => r.read(favoritesProvider.notifier).reorder(a, b),
                  children: [
                    for (final n in favs)
                      ListTile(
                        key: ValueKey(n),
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(_icons[n] ?? Icons.bolt,
                            color: GajalaColors.accent, size: 20),
                        title: Text(byName[n] == null ? n : _labelFor(byName[n]!)),
                        trailing: IconButton(
                          icon: Icon(Icons.remove_circle_outline,
                              size: 20, color: c.pal.textDim),
                          onPressed: () =>
                              r.read(favoritesProvider.notifier).toggle(n),
                        ),
                      ),
                  ],
                ),
              ),
            ]),
          ),
        );
      }),
    );
  }
}

class _SkillCard extends StatelessWidget {
  final Skill skill;
  final bool pinned;
  final VoidCallback? onLongPress;
  const _SkillCard(this.skill, {this.pinned = false, this.onLongPress});
  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    return Material(
      color: pal.surface,
      borderRadius: BorderRadius.circular(14),
      child: InkWell(
        borderRadius: BorderRadius.circular(14),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => _screenFor(skill))),
        onLongPress: onLongPress,
        child: Ink(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(14),
            border: Border.all(color: pal.border),
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 12),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(_icons[skill.name] ?? Icons.bolt,
                    color: GajalaColors.accent, size: 22),
                const SizedBox(height: 8),
                Text(_labelFor(skill),
                    maxLines: 1, overflow: TextOverflow.ellipsis,
                    textAlign: TextAlign.center,
                    style: const TextStyle(fontSize: 11.5, fontWeight: FontWeight.w500)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _SystemCard extends ConsumerWidget {
  const _SystemCard();
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final sys = ref.watch(systemProvider);
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: sys.when(
          data: (s) => Row(
            mainAxisAlignment: MainAxisAlignment.spaceAround,
            children: [
              _Metric('CPU', '${s.cpu.toStringAsFixed(0)}%', s.cpu / 100),
              _Metric('RAM', '${s.ramPct}%', s.ramPct / 100),
              _Metric('DISK', '${s.diskPct}%', s.diskPct / 100),
              if (s.batteryPct != null)
                _Metric('BATT', '${s.batteryPct}%', s.batteryPct! / 100),
            ],
          ),
          loading: () => const SizedBox(height: 56, child: Center(child: CircularProgressIndicator())),
          error: (e, _) => SizedBox(height: 56, child: Center(
              child: Text('system offline', style: TextStyle(color: context.pal.textDim)))),
        ),
      ),
    );
  }
}

class _Metric extends StatelessWidget {
  final String label, value;
  final double frac;
  const _Metric(this.label, this.value, this.frac);
  @override
  Widget build(BuildContext context) {
    final color = frac > .85 ? GajalaColors.danger : frac > .6 ? GajalaColors.warn : GajalaColors.ok;
    return Column(children: [
      SizedBox(
        height: 46, width: 46,
        child: Stack(alignment: Alignment.center, children: [
          CircularProgressIndicator(
              value: frac.clamp(0, 1), strokeWidth: 4,
              backgroundColor: context.pal.surfaceAlt, color: color),
          Text(value, style: const TextStyle(fontSize: 11, fontWeight: FontWeight.w700)),
        ]),
      ),
      const SizedBox(height: 6),
      Text(label, style: TextStyle(color: context.pal.textDim, fontSize: 11)),
    ]);
  }
}

class _StatusDot extends ConsumerWidget {
  const _StatusDot();
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final ok = ref.watch(systemProvider).hasValue;
    return Container(width: 9, height: 9, decoration: BoxDecoration(
        shape: BoxShape.circle, color: ok ? GajalaColors.ok : context.pal.textDim));
  }
}
