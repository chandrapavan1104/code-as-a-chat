import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/app_lock.dart';
import '../core/state.dart';
import 'phone_abilities_screen.dart';
import 'voice_sheet.dart';
import 'skills_screen.dart';

class SettingsScreen extends ConsumerWidget {
  const SettingsScreen({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final mode = ref.watch(themeModeProvider);
    final config = ref.watch(configProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        children: [
          ListTile(
            leading: const Icon(Icons.mic_none),
            title: const Text('Voice and wake word'),
            onTap: () => showVoiceSettings(context),
          ),
          ListTile(
            leading: const Icon(Icons.phonelink_lock),
            title: const Text('Phone permissions'),
            onTap: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => const PhoneAbilitiesScreen()),
            ),
          ),
          ListTile(
            leading: const Icon(Icons.brightness_6),
            title: const Text('Appearance'),
            trailing: DropdownButton<ThemeMode>(
              value: mode,
              items: [
                for (final m in ThemeMode.values)
                  DropdownMenuItem(value: m, child: Text(m.name)),
              ],
              onChanged: (m) {
                if (m != null) ref.read(themeModeProvider.notifier).set(m);
              },
            ),
          ),
          ListTile(
            leading: const Icon(Icons.fingerprint),
            title: const Text('App lock'),
            onTap: () async {
              final enabled = await AppLock.enabled();
              final why = await AppLock.setEnabled(!enabled);
              if (context.mounted)
                ScaffoldMessenger.of(context).showSnackBar(
                  SnackBar(
                    content: Text(
                      why ?? (enabled ? 'App lock is off' : 'App lock is on'),
                    ),
                  ),
                );
            },
          ),
          ListTile(
            leading: const Icon(Icons.toggle_on),
            title: const Text('Skills'),
            onTap: () => Navigator.of(
              context,
            ).push(MaterialPageRoute(builder: (_) => const SkillsScreen())),
          ),
          const Divider(),
          ListTile(
            leading: const Icon(Icons.link),
            title: const Text('Connection'),
            subtitle: Text(config?.url ?? 'Not connected'),
          ),
          ListTile(
            leading: const Icon(Icons.logout),
            title: const Text('Disconnect'),
            onTap: () async {
              final yes = await showDialog<bool>(
                context: context,
                builder: (ctx) => AlertDialog(
                  title: const Text('Disconnect this phone?'),
                  content: const Text(
                    'Your conversations and work stay on the Mac.',
                  ),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(ctx, false),
                      child: const Text('Cancel'),
                    ),
                    TextButton(
                      onPressed: () => Navigator.pop(ctx, true),
                      child: const Text('Disconnect'),
                    ),
                  ],
                ),
              );
              if (yes == true) {
                await ref.read(configProvider.notifier).disconnect();
                if (context.mounted) Navigator.pop(context);
              }
            },
          ),
        ],
      ),
    );
  }
}
