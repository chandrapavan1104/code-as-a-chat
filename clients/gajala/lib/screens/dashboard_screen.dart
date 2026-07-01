import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';
import 'chat_screen.dart';
import 'notes_screen.dart';
import 'system_screen.dart';
import 'reminders_screen.dart';
import 'diary_screen.dart';
import 'projects_screen.dart';
import 'usage_screen.dart';
import 'mac_screen.dart';

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
                  const _SystemCard(),
                  Padding(
                    padding: const EdgeInsets.fromLTRB(4, 16, 4, 10),
                    child: Text('SKILLS', style: TextStyle(
                        color: context.pal.textDim, fontWeight: FontWeight.w700, letterSpacing: 1)),
                  ),
                  skills.when(
                    data: (list) => GridView.count(
                      crossAxisCount: 3,
                      shrinkWrap: true,
                      physics: const NeverScrollableScrollPhysics(),
                      mainAxisSpacing: 10, crossAxisSpacing: 10, childAspectRatio: .92,
                      children: list.map((s) => _SkillCard(s)).toList(),
                    ),
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
          color: context.pal.surfaceAlt,
          borderRadius: BorderRadius.circular(26),
          child: InkWell(
            borderRadius: BorderRadius.circular(26),
            onTap: () => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => const ChatScreen(command: 'shell', title: 'Gajala'))),
            child: Padding(
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

class _SkillCard extends StatelessWidget {
  final Skill skill;
  const _SkillCard(this.skill);
  @override
  Widget build(BuildContext context) {
    return Card(
      child: InkWell(
        borderRadius: BorderRadius.circular(18),
        onTap: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => _screenFor(skill))),
        child: Padding(
          padding: const EdgeInsets.all(10),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              Icon(_icons[skill.name] ?? Icons.bolt, color: GajalaColors.accent, size: 28),
              const SizedBox(height: 8),
              Text(skill.command,
                  maxLines: 1, overflow: TextOverflow.ellipsis,
                  style: const TextStyle(fontWeight: FontWeight.w600)),
            ],
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
