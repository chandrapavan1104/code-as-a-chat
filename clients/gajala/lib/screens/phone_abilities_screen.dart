// Owner switches for what the Mac agent may read from this phone.

import 'package:flutter/material.dart';
import '../core/device_actions.dart';
import '../core/phone_abilities.dart';
import '../core/theme.dart';

class PhoneAbilitiesScreen extends StatefulWidget {
  const PhoneAbilitiesScreen({super.key});
  @override
  State<PhoneAbilitiesScreen> createState() => _PhoneAbilitiesScreenState();
}

class _PhoneAbilitiesScreenState extends State<PhoneAbilitiesScreen> {
  final Map<String, bool> _on = {};
  final _home = TextEditingController();
  final _work = TextEditingController();

  @override
  void dispose() {
    _home.dispose();
    _work.dispose();
    super.dispose();
  }

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    for (final a in abilities) {
      _on[a.id] = await PhoneAbilities.instance.isEnabled(a.id);
    }
    for (final a in sensitiveActions) {
      _on[a.id] = await DeviceActions.instance.isEnabled(a.id);
    }
    _home.text = await DeviceActions.instance.savedPlace('home') ?? '';
    _work.text = await DeviceActions.instance.savedPlace('work') ?? '';
    if (mounted) setState(() {});
  }

  Future<void> _set(String id, bool on) async {
    setState(() => _on[id] = on);
    await PhoneAbilities.instance.setEnabled(id, on);
  }

  Widget _heading(BuildContext context, String title, String detail) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
    child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      Text(title, style: Theme.of(context).textTheme.titleMedium),
      const SizedBox(height: 4),
      Text(detail, style: TextStyle(color: context.pal.textDim)),
    ]),
  );

  Widget _place(String label, TextEditingController c, String key) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
    child: TextField(
      controller: c,
      decoration: InputDecoration(labelText: label, hintText: 'Address or place name'),
      onChanged: (v) => DeviceActions.instance.setSavedPlace(key, v),
    ),
  );

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Phone abilities')),
      body: ListView(children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
          child: Text(
            'Let Gajala read these from your phone while you are chatting with '
            'it in this app. Everything is off until you turn it on, Android '
            'asks for permission the first time, and each use is noted in the '
            'chat. Nothing runs in the background or from Telegram.',
            style: TextStyle(color: context.pal.textDim),
          ),
        ),
        for (final a in abilities)
          SwitchListTile(
            title: Text(a.title),
            subtitle: Text(a.detail),
            value: _on[a.id] ?? false,
            onChanged: (v) => _set(a.id, v),
          ),
        const Divider(height: 32),
        _heading(context, 'Actions',
            'Alarms, timers, music, apps, flashlight, messages you send yourself, '
            'calls and calendar events (confirmed on screen) always work. These '
            'three need your OK:'),
        for (final a in sensitiveActions)
          SwitchListTile(
            title: Text(a.title),
            subtitle: Text(a.detail),
            value: _on[a.id] ?? false,
            onChanged: (v) async {
              setState(() => _on[a.id] = v);
              await DeviceActions.instance.setEnabled(a.id, v);
              // Android's own access screen; the switch alone is not enough.
              if (v && a.access != null) {
                await DeviceActions.instance.openAccessSettings(a.access!);
              }
            },
          ),
        const Divider(height: 32),
        _heading(context, 'Saved places', 'Used for "navigate home" and "navigate to work".'),
        _place('Home', _home, 'home'),
        _place('Work', _work, 'work'),
        const SizedBox(height: 24),
      ]),
    );
  }
}
