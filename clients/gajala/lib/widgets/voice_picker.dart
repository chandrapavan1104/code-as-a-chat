import 'package:flutter/material.dart';

import '../core/voice_preferences.dart';

class VoicePicker extends StatefulWidget {
  final VoicePreferences preferences;
  const VoicePicker({super.key, required this.preferences});

  @override
  State<VoicePicker> createState() => _VoicePickerState();
}

class _VoicePickerState extends State<VoicePicker> {
  List<VoiceOption> voices = const [];
  VoiceOption? selected;
  String? error;
  bool loading = true;
  bool previewing = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      loading = true;
      error = null;
    });
    try {
      final found = await widget.preferences.available();
      final chosen = await widget.preferences.selected(found);
      if (!mounted) return;
      setState(() {
        voices = found;
        selected = chosen;
        loading = false;
      });
      if (chosen != null) await widget.preferences.select(chosen);
    } catch (e) {
      if (mounted)
        setState(() {
          loading = false;
          error = 'Could not read phone voices: $e';
        });
    }
  }

  Future<void> _select(VoiceOption? voice) async {
    if (voice == null) return;
    setState(() => selected = voice);
    try {
      await widget.preferences.select(voice);
    } catch (e) {
      if (mounted) setState(() => error = 'Could not select that voice: $e');
    }
  }

  Future<void> _preview() async {
    final voice = selected;
    if (voice == null || previewing) return;
    setState(() {
      previewing = true;
      error = null;
    });
    try {
      await widget.preferences.preview(voice);
    } catch (e) {
      if (mounted) setState(() => error = 'Preview failed: $e');
    } finally {
      if (mounted) setState(() => previewing = false);
    }
  }

  Future<void> _openSettings() async {
    try {
      await widget.preferences.openEngineSettings();
    } catch (e) {
      if (mounted) setState(() => error = 'Could not open voice settings: $e');
    }
  }

  @override
  Widget build(BuildContext context) {
    if (loading) return const Center(child: CircularProgressIndicator());
    if (voices.isEmpty) {
      return Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          const Text(
            'No Indian English voice is installed on this phone.',
            textAlign: TextAlign.center,
          ),
          const SizedBox(height: 10),
          OutlinedButton.icon(
            onPressed: _openSettings,
            icon: const Icon(Icons.settings_voice),
            label: const Text('Install or manage voices'),
          ),
          if (error != null)
            Text(
              error!,
              style: TextStyle(color: Theme.of(context).colorScheme.error),
            ),
        ],
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        DropdownButtonFormField<VoiceOption>(
          value: selected,
          decoration: const InputDecoration(labelText: 'Indian English voice'),
          items: [
            for (final voice in voices)
              DropdownMenuItem(
                value: voice,
                child: Text(voice.label, overflow: TextOverflow.ellipsis),
              ),
          ],
          onChanged: _select,
        ),
        const SizedBox(height: 8),
        Align(
          alignment: Alignment.centerLeft,
          child: OutlinedButton.icon(
            onPressed: selected == null || previewing ? null : _preview,
            icon: previewing
                ? const SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  )
                : const Icon(Icons.volume_up),
            label: Text(previewing ? 'Speaking…' : 'Preview voice'),
          ),
        ),
        if (error != null)
          Text(
            error!,
            style: TextStyle(color: Theme.of(context).colorScheme.error),
          ),
      ],
    );
  }
}
