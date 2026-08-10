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
import 'skills_screen.dart';

// Display-label overrides for skill tiles (the screen behind is unchanged).
const _tileLabels = {'usage': 'codaur', 'notes': 'brain dump'};

const _icons = {
  'claude': Icons.code, 'codex': Icons.terminal, 'antigravity': Icons.auto_awesome,
  'sysmon': Icons.memory, 'filemanager': Icons.folder, 'sessions': Icons.history,
  'projects': Icons.work, 'ports': Icons.lan, 'firebase': Icons.local_fire_department,
  'usage': Icons.bar_chart, 'notes': Icons.sticky_note_2, 'context': Icons.description,
  'reminders': Icons.alarm, 'mac': Icons.desktop_mac, 'diary': Icons.menu_book,
  'qwen': Icons.hub, 'auth': Icons.lock_outline,
};

class DashboardScreen extends ConsumerWidget {
  const DashboardScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final skills = ref.watch(skillsProvider);
    final pal = context.pal;
    return Scaffold(
      appBar: AppBar(
        titleSpacing: 18,
        title: Row(children: [
          Text('Gajala', style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(width: 8),
          const _StatusDot(),
        ]),
        actions: [
          // Mac lives in the corner as a plain icon, not a tile.
          IconButton(
            icon: Icon(Icons.desktop_mac_outlined, color: pal.textDim),
            tooltip: 'Mac',
            onPressed: () => Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => const MacScreen())),
          ),
          // Everything else hides behind the overflow.
          _OverflowMenu(skills.valueOrNull ?? const []),
          const SizedBox(width: 4),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          ref.invalidate(systemProvider);
          ref.invalidate(skillsProvider);
          ref.invalidate(notesProvider);
          ref.invalidate(projectsProvider);
        },
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 4, 16, 24),
          children: [
            const _UpdateBanner(),
            const _AskHero(),
            const SizedBox(height: 18),
            skills.when(
              data: (list) => _Features(list),
              loading: () => const Padding(
                  padding: EdgeInsets.all(40),
                  child: Center(child: CircularProgressIndicator())),
              error: (e, _) => Padding(
                  padding: const EdgeInsets.all(20),
                  child: Text('Couldn\'t load skills: $e',
                      style: const TextStyle(color: GajalaColors.danger))),
            ),
            const SizedBox(height: 18),
            const _SystemStrip(),
          ],
        ),
      ),
    );
  }
}

/// The one thing you do most — a full-width, unmistakable entry to the agent.
class _AskHero extends StatelessWidget {
  const _AskHero();
  @override
  Widget build(BuildContext context) {
    return Material(
      color: Colors.transparent,
      child: InkWell(
        borderRadius: BorderRadius.circular(20),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => const ChatScreen(command: 'shell', title: 'Gajala'))),
        child: Ink(
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(20),
            gradient: const LinearGradient(
              begin: Alignment.topLeft, end: Alignment.bottomRight,
              colors: [GajalaColors.indigo, GajalaColors.violet],
            ),
          ),
          child: Padding(
            padding: const EdgeInsets.fromLTRB(20, 20, 16, 20),
            child: Row(children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(children: const [
                      Icon(Icons.auto_awesome, color: Colors.white, size: 18),
                      SizedBox(width: 8),
                      Text('Ask Gajala',
                          style: TextStyle(
                              color: Colors.white, fontSize: 17,
                              fontWeight: FontWeight.w700, letterSpacing: -0.2)),
                    ]),
                    const SizedBox(height: 6),
                    Text('Anything — code, notes, your Mac',
                        style: TextStyle(
                            color: Colors.white.withValues(alpha: .82), fontSize: 13)),
                  ],
                ),
              ),
              Container(
                width: 40, height: 40,
                decoration: BoxDecoration(
                    shape: BoxShape.circle,
                    color: Colors.white.withValues(alpha: .22)),
                child: const Icon(Icons.arrow_forward, color: Colors.white, size: 20),
              ),
            ]),
          ),
        ),
      ),
    );
  }
}

/// Overflow: the long tail of skills + settings, out of the way.
class _OverflowMenu extends ConsumerWidget {
  final List<Skill> skills;
  const _OverflowMenu(this.skills);

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final mode = ref.watch(themeModeProvider);
    return PopupMenuButton<String>(
      icon: Icon(Icons.more_vert, color: context.pal.textDim),
      color: context.pal.surface,
      shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: BorderSide(color: context.pal.border)),
      onSelected: (v) {
        switch (v) {
          case 'all':
            showAllSkillsSheet(context, skills);
            break;
          case 'skills':
            Navigator.of(context)
                .push(MaterialPageRoute(builder: (_) => const SkillsScreen()));
            break;
          case 'theme':
            final next = switch (mode) {
              ThemeMode.system => ThemeMode.light,
              ThemeMode.light => ThemeMode.dark,
              ThemeMode.dark => ThemeMode.system,
            };
            ref.read(themeModeProvider.notifier).set(next);
            break;
          case 'logout':
            ref.read(configProvider.notifier).disconnect();
            break;
        }
      },
      itemBuilder: (_) => [
        const PopupMenuItem(
            value: 'all',
            child: ListTile(
                dense: true, contentPadding: EdgeInsets.zero,
                leading: Icon(Icons.apps, size: 20), title: Text('All skills'))),
        const PopupMenuItem(
            value: 'skills',
            child: ListTile(
                dense: true, contentPadding: EdgeInsets.zero,
                leading: Icon(Icons.toggle_on, size: 20), title: Text('Skills marketplace'))),
        PopupMenuItem(
            value: 'theme',
            child: ListTile(
                dense: true, contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.brightness_6, size: 20),
                title: Text('Theme: ${mode.name}'))),
        const PopupMenuItem(
            value: 'logout',
            child: ListTile(
                dense: true, contentPadding: EdgeInsets.zero,
                leading: Icon(Icons.logout, size: 20), title: Text('Disconnect'))),
      ],
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

const _hints = {
  'usage': 'tokens & limits', 'notes': 'capture a thought',
  'projects': 'switch project', 'ports': "what's listening",
  'mac': 'lock, wake, screenshot', 'sysmon': 'cpu, ram, disk',
  'reminders': 'nudges', 'diary': 'daily log', 'sessions': 'resume a thread',
  'filemanager': 'browse files', 'fix': 'self-heal a bug', 'build': 'rebuild app',
  'errors': 'what broke', 'model': 'engine & model',
};

/// Home features: your top pins get big colour-coded cards with live context;
/// the rest ride along as small chips. Order comes from your pins.
class _Features extends ConsumerStatefulWidget {
  final List<Skill> all;
  const _Features(this.all);
  @override
  ConsumerState<_Features> createState() => _FeaturesState();
}

class _FeaturesState extends ConsumerState<_Features> {
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
    final big = pinned.take(4).toList();
    final rest = pinned.skip(4).toList();

    return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      if (big.isNotEmpty)
        GridView.count(
          crossAxisCount: 2, shrinkWrap: true,
          physics: const NeverScrollableScrollPhysics(),
          mainAxisSpacing: 12, crossAxisSpacing: 12, childAspectRatio: 1.42,
          children: [for (final s in big) _FeatureCard(s, widget.all)],
        ),
      if (rest.isNotEmpty) ...[
        const SizedBox(height: 14),
        SizedBox(
          height: 36,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            padding: EdgeInsets.zero,
            itemCount: rest.length + 1,
            separatorBuilder: (_, _) => const SizedBox(width: 8),
            itemBuilder: (_, i) => i == rest.length
                ? _MoreChip(widget.all)
                : _SkillChip(rest[i]),
          ),
        ),
      ] else ...[
        const SizedBox(height: 14),
        Align(alignment: Alignment.centerLeft, child: _MoreChip(widget.all)),
      ],
    ]);
  }
}

/// Large, tinted, colour-coded — with a line of live context where it's cheap.
class _FeatureCard extends ConsumerWidget {
  final Skill skill;
  final List<Skill> all;
  const _FeatureCard(this.skill, this.all);
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final c = GajalaColors.forSkill(skill.name);
    final pal = context.pal;
    return Material(
      color: Colors.transparent,
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: () => Navigator.of(context)
            .push(MaterialPageRoute(builder: (_) => _screenFor(skill))),
        onLongPress: () => showAllSkillsSheet(context, all),
        child: Ink(
          decoration: BoxDecoration(
            color: c.withValues(alpha: .13),
            borderRadius: BorderRadius.circular(18),
            border: Border.all(color: c.withValues(alpha: .30)),
          ),
          child: Padding(
            padding: const EdgeInsets.all(14),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Container(
                width: 34, height: 34,
                decoration: BoxDecoration(
                    color: c.withValues(alpha: .24),
                    borderRadius: BorderRadius.circular(10)),
                child: Icon(_icons[skill.name] ?? Icons.bolt, color: c, size: 19),
              ),
              const Spacer(),
              Text(_labelFor(skill),
                  maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                      fontSize: 14.5, fontWeight: FontWeight.w700, color: pal.text)),
              const SizedBox(height: 2),
              _FeatureSubtitle(skill.name),
            ]),
          ),
        ),
      ),
    );
  }
}

class _FeatureSubtitle extends ConsumerWidget {
  final String name;
  const _FeatureSubtitle(this.name);
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final style = TextStyle(fontSize: 11.5, color: context.pal.textDim);
    String text = _hints[name] ?? '';
    if (name == 'notes') {
      final n = ref.watch(notesProvider).valueOrNull?.length;
      if (n != null) text = '$n open';
    } else if (name == 'projects') {
      final p = ref.watch(projectsProvider).valueOrNull?['current_name']?.toString();
      if (p != null && p.isNotEmpty) text = p;
    }
    return Text(text, maxLines: 1, overflow: TextOverflow.ellipsis, style: style);
  }
}

/// Secondary pins — small tinted pills, not full tiles.
class _SkillChip extends StatelessWidget {
  final Skill skill;
  const _SkillChip(this.skill);
  @override
  Widget build(BuildContext context) {
    final c = GajalaColors.forSkill(skill.name);
    return Material(
      color: c.withValues(alpha: .12),
      borderRadius: BorderRadius.circular(10),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: () => Navigator.of(context)
            .push(MaterialPageRoute(builder: (_) => _screenFor(skill))),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(_icons[skill.name] ?? Icons.bolt, size: 15, color: c),
            const SizedBox(width: 7),
            Text(_labelFor(skill),
                style: TextStyle(
                    fontSize: 12.5, fontWeight: FontWeight.w600, color: context.pal.text)),
          ]),
        ),
      ),
    );
  }
}

class _MoreChip extends StatelessWidget {
  final List<Skill> all;
  const _MoreChip(this.all);
  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    return Material(
      color: pal.surfaceAlt,
      borderRadius: BorderRadius.circular(10),
      child: InkWell(
        borderRadius: BorderRadius.circular(10),
        onTap: () => showAllSkillsSheet(context, all),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            Icon(Icons.apps, size: 15, color: pal.textDim),
            const SizedBox(width: 7),
            Text('All skills',
                style: TextStyle(
                    fontSize: 12.5, fontWeight: FontWeight.w600, color: pal.textDim)),
          ]),
        ),
      ),
    );
  }
}

/// One sheet for the long tail: reorder your pins, pin/unpin anything, or jump
/// straight into a skill.
void showAllSkillsSheet(BuildContext context, List<Skill> all) {
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    backgroundColor: context.pal.surface,
    shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(18))),
    builder: (sheetCtx) => Consumer(builder: (c, ref, _) {
      final favs = ref.watch(favoritesProvider);
      final byName = {for (final s in all) s.name: s};
      final pinned = [for (final n in favs) if (byName[n] != null) byName[n]!];
      final rest = all.where((s) => !favs.contains(s.name)).toList();
      return SafeArea(
        child: DraggableScrollableSheet(
          initialChildSize: .75, minChildSize: .4, maxChildSize: .95,
          expand: false,
          builder: (_, scrollCtl) => ListView(
            controller: scrollCtl,
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 20),
            children: [
              Center(
                child: Container(
                  width: 36, height: 4,
                  margin: const EdgeInsets.only(bottom: 14),
                  decoration: BoxDecoration(
                      color: c.pal.textDim, borderRadius: BorderRadius.circular(2)),
                ),
              ),
              Row(children: [
                Text('Pinned', style: Theme.of(c).textTheme.titleMedium),
                const Spacer(),
                Text('drag to reorder · first 4 are cards',
                    style: Theme.of(c).textTheme.bodySmall),
              ]),
              const SizedBox(height: 6),
              ReorderableListView(
                shrinkWrap: true,
                physics: const NeverScrollableScrollPhysics(),
                onReorderItem: (a, b) =>
                    ref.read(favoritesProvider.notifier).reorder(a, b),
                children: [
                  for (final s in pinned)
                    ListTile(
                      key: ValueKey(s.name),
                      contentPadding: EdgeInsets.zero,
                      leading: Icon(_icons[s.name] ?? Icons.bolt,
                          color: GajalaColors.forSkill(s.name), size: 20),
                      title: Text(_labelFor(s)),
                      trailing: IconButton(
                        icon: Icon(Icons.push_pin, size: 18, color: GajalaColors.accent),
                        onPressed: () =>
                            ref.read(favoritesProvider.notifier).toggle(s.name),
                      ),
                    ),
                ],
              ),
              const SizedBox(height: 10),
              Text('Everything else', style: Theme.of(c).textTheme.titleMedium),
              const SizedBox(height: 4),
              for (final s in rest)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: Icon(_icons[s.name] ?? Icons.bolt,
                      color: GajalaColors.forSkill(s.name), size: 20),
                  title: Text(_labelFor(s)),
                  subtitle: Text(_hints[s.name] ?? s.description,
                      maxLines: 1, overflow: TextOverflow.ellipsis),
                  trailing: IconButton(
                    icon: Icon(Icons.push_pin_outlined, size: 18, color: c.pal.textDim),
                    onPressed: () =>
                        ref.read(favoritesProvider.notifier).toggle(s.name),
                  ),
                  onTap: () {
                    Navigator.pop(sheetCtx);
                    Navigator.of(context)
                        .push(MaterialPageRoute(builder: (_) => _screenFor(s)));
                  },
                ),
            ],
          ),
        ),
      );
    }),
  );
}

/// Ambient system info — a slim strip at the bottom, not a headline card.
class _SystemStrip extends ConsumerWidget {
  const _SystemStrip();
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final pal = context.pal;
    return ref.watch(systemProvider).when(
      data: (s) => Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: () => Navigator.of(context)
              .push(MaterialPageRoute(builder: (_) => const SystemScreen())),
          child: Container(
            padding: const EdgeInsets.fromLTRB(14, 12, 8, 12),
            decoration: BoxDecoration(
              color: pal.surface,
              borderRadius: BorderRadius.circular(14),
              border: Border.all(color: pal.border),
            ),
            child: Row(children: [
              _Meter('CPU', s.cpu / 100, '${s.cpu.toStringAsFixed(0)}%',
                  GajalaColors.teal),
              _Meter('RAM', s.ramPct / 100, '${s.ramPct}%', GajalaColors.violet),
              _Meter('DISK', s.diskPct / 100, '${s.diskPct}%', GajalaColors.amber),
              if (s.batteryPct != null)
                _Meter('BATT', s.batteryPct! / 100, '${s.batteryPct}%',
                    GajalaColors.green),
              Icon(Icons.chevron_right, size: 18, color: pal.textDim),
            ]),
          ),
        ),
      ),
      loading: () => const SizedBox(height: 56),
      error: (e, _) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Text('system offline',
            style: TextStyle(color: pal.textDim, fontSize: 12)),
      ),
    );
  }
}

class _Meter extends StatelessWidget {
  final String label, value;
  final double frac;
  final Color color;
  const _Meter(this.label, this.frac, this.value, this.color);
  @override
  Widget build(BuildContext context) {
    final hot = frac > .85;
    return Expanded(
      child: Padding(
        padding: const EdgeInsets.only(right: 14),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Row(children: [
            Text(label,
                style: TextStyle(
                    fontSize: 10, letterSpacing: .6,
                    fontWeight: FontWeight.w600, color: context.pal.textDim)),
            const Spacer(),
            Text(value,
                style: TextStyle(
                    fontSize: 11.5, fontWeight: FontWeight.w700,
                    color: hot ? GajalaColors.danger : context.pal.text)),
          ]),
          const SizedBox(height: 5),
          ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: LinearProgressIndicator(
              value: frac.clamp(0, 1), minHeight: 4,
              backgroundColor: context.pal.surfaceAlt,
              color: hot ? GajalaColors.danger : color,
            ),
          ),
        ]),
      ),
    );
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
