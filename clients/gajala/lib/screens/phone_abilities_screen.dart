// Owner switches for what the Mac agent may read from this phone.

import 'package:flutter/material.dart';
import '../core/phone_abilities.dart';
import '../core/theme.dart';

class PhoneAbilitiesScreen extends StatefulWidget {
  const PhoneAbilitiesScreen({super.key});
  @override
  State<PhoneAbilitiesScreen> createState() => _PhoneAbilitiesScreenState();
}

class _PhoneAbilitiesScreenState extends State<PhoneAbilitiesScreen> {
  final Map<String, bool> _on = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    for (final a in abilities) {
      _on[a.id] = await PhoneAbilities.instance.isEnabled(a.id);
    }
    if (mounted) setState(() {});
  }

  Future<void> _set(String id, bool on) async {
    setState(() => _on[id] = on);
    await PhoneAbilities.instance.setEnabled(id, on);
  }

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
      ]),
    );
  }
}
