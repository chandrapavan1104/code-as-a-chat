import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

/// Shows approval separately from whether Android can currently expose playback.
class MusicAccessTile extends StatefulWidget {
  const MusicAccessTile({super.key});
  @override
  State<MusicAccessTile> createState() => _MusicAccessTileState();
}

class _MusicAccessTileState extends State<MusicAccessTile>
    with WidgetsBindingObserver {
  static const channel = MethodChannel('gajala/phone');
  String message = 'Checking Android playback access…';
  bool? enabled;
  bool musicControlEnabled = false;
  bool musicControlConnected = false;
  String? lastPlaybackDiagnostics;
  String directProbeMessage =
      'Direct YouTube Music control has not been checked.';
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    refresh();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) refresh();
  }

  Future<void> refresh() async {
    try {
      final result = await channel.invokeMapMethod<String, dynamic>(
        'musicAccess',
      );
      if (!mounted) return;
      setState(() {
        enabled = result?['enabled'] as bool?;
        musicControlEnabled = result?['musicControlEnabled'] == true;
        musicControlConnected = result?['musicControlConnected'] == true;
        lastPlaybackDiagnostics = result?['lastPlaybackDiagnostics']
            ?.toString();
        final probe = result?['directProbe'];
        if (probe is Map) {
          final stage = probe['stage']?.toString() ?? 'unknown';
          final reason =
              probe['reason']?.toString() ?? 'No probe details available.';
          final connected = probe['browserConnected'] == true;
          final searchState = probe['playFromSearchSupported'];
          final search = searchState == true
              ? 'play-from-search advertised'
              : searchState == false
              ? 'play-from-search not advertised'
              : 'play-from-search unknown';
          final browse = probe['browseSearch']?.toString() ?? 'unknown';
          directProbeMessage =
              '$stage · ${connected ? 'browser connected' : 'browser unavailable'} · '
              '$search · '
              'catalog search: $browse. $reason';
        } else {
          directProbeMessage =
              'Direct YouTube Music capability details are unavailable.';
        }
        message =
            result?['message'] as String? ??
            'Playback access could not be checked.';
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          enabled = null;
          message =
              'Playback access could not be checked. Permission may already be enabled.';
        });
      }
    }
  }

  Future<void> openSettings() async {
    try {
      await channel.invokeMethod('openMusicAccess');
    } catch (_) {
      if (mounted) {
        setState(
          () => message = 'Android access settings could not be opened.',
        );
      }
    }
  }

  Future<void> openMusicControlSettings() async {
    try {
      await channel.invokeMethod('openMusicControlAccess');
    } catch (_) {
      if (mounted) {
        setState(
          () => message = 'Android accessibility settings could not be opened.',
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) => Column(
    children: [
      ListTile(
        leading: Icon(
          enabled == true ? Icons.check_circle_outline : Icons.music_note,
        ),
        title: Text(
          'YouTube Music playback access${enabled == true ? ' · Enabled' : ''}',
        ),
        subtitle: Text(message),
        trailing: IconButton(
          tooltip: 'Recheck playback access',
          onPressed: refresh,
          icon: const Icon(Icons.refresh),
        ),
      ),
      ListTile(
        leading: const Icon(Icons.sensors_outlined),
        title: const Text('YouTube Music direct-control probe'),
        subtitle: Text(directProbeMessage),
      ),
      if (lastPlaybackDiagnostics != null)
        TextButton(
          onPressed: () => showDialog<void>(
            context: context,
            builder: (context) => AlertDialog(
              title: const Text('Last playback attempt'),
              content: SingleChildScrollView(
                child: SelectableText(lastPlaybackDiagnostics!),
              ),
              actions: [
                TextButton(
                  onPressed: () => Navigator.pop(context),
                  child: const Text('Close'),
                ),
              ],
            ),
          ),
          child: const Text('View last playback details'),
        ),
      TextButton(
        onPressed: openSettings,
        child: const Text('Manage Android notification access'),
      ),
      const Divider(),
      ListTile(
        leading: Icon(
          musicControlEnabled ? Icons.check_circle_outline : Icons.touch_app,
        ),
        title: Text(
          'Select songs in YouTube Music'
          '${musicControlEnabled ? ' · Enabled' : ''}',
        ),
        subtitle: Text(
          musicControlConnected
              ? 'Ready. Gajala can search and select a matching song when you ask. It conservatively rejects results labelled as ads or sponsored content.'
              : musicControlEnabled
              ? 'Enabled. Reopen Gajala if Android has not connected the service yet.'
              : 'Optional one-time setup. Android limits this service to YouTube Music; Gajala does not read or control other apps.',
        ),
      ),
      TextButton(
        onPressed: openMusicControlSettings,
        child: Text(
          musicControlEnabled
              ? 'Manage YouTube Music control'
              : 'Enable YouTube Music control',
        ),
      ),
    ],
  );
}
