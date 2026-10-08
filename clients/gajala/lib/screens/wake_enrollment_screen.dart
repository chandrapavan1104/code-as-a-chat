import 'dart:typed_data';
import 'dart:async';
import 'package:flutter/material.dart';
import '../core/wake_word.dart';

class WakeEnrollmentScreen extends StatefulWidget {
  const WakeEnrollmentScreen({super.key});
  @override
  State<WakeEnrollmentScreen> createState() => _WakeEnrollmentScreenState();
}

class _WakeEnrollmentScreenState extends State<WakeEnrollmentScreen>
    with WidgetsBindingObserver {
  final _samples = <Uint8List>[];
  bool _recording = false, _calibrating = false;
  bool _foreground = true, _finished = false;
  int _generation = 0;
  String? _error;

  static const _prompts = [
    'Say “Hey Gajala” naturally',
    'Say it a little faster',
    'Say it from a little farther away',
    'Say it in your usual slang and rhythm',
    'Say “Hey Gajala” one more time',
    'Now talk normally without saying Gajala',
    'Stay quiet for three seconds',
    'Final check: say “Hey Gajala” naturally',
  ];

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    unawaited(WakeWord.hold());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    if (!_finished) WakeWord.cancelEnrollment();
    unawaited(WakeWord.release());
    _samples.clear();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    _foreground = state == AppLifecycleState.resumed;
    if (!_foreground) {
      _generation++;
      WakeWord.cancelEnrollment();
      if (mounted) {
        setState(() {
          _recording = false;
          _calibrating = false;
        });
      }
    }
  }

  Future<void> _capture() async {
    setState(() {
      _recording = true;
      _error = null;
    });
    try {
      final pcm = await WakeWord.enrollmentCapture();
      if (!mounted) return;
      _samples.add(pcm);
      setState(() => _recording = false);
      if (_samples.length == _prompts.length) await _calibrate();
    } catch (e) {
      if (mounted) {
        setState(() {
          _recording = false;
          _error = e.toString().contains('mic_permission')
              ? 'Allow microphone access in Android Settings, then try again.'
              : 'Recording stopped. Keep Gajala open and try this sample again.';
        });
      }
    }
  }

  Future<void> _calibrate() async {
    final generation = ++_generation;
    setState(() => _calibrating = true);
    try {
      final dir = await WakeWord.enrollmentModelDir();
      final profile = await calibrateWakeSamples(dir, _samples);
      if (!mounted || !_foreground || generation != _generation) return;
      if (profile == null) {
        throw StateError('No safe profile');
      }
      await WakeWord.saveCalibration(profile);
      if (!mounted) return;
      _finished = true;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Personal wake sensitivity saved on this phone.'),
        ),
      );
      Navigator.pop(context, true);
    } catch (_) {
      if (mounted) {
        setState(() {
          _calibrating = false;
          _error =
              'Those samples did not produce a safe setting. Your previous '
              'setting is unchanged. Try again in a quieter room.';
          _samples.clear();
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final done = _samples.length;
    return Scaffold(
      appBar: AppBar(title: const Text('Teach “Hey Gajala”')),
      body: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            const Text(
              'This tunes wake sensitivity to your pronunciation. It does '
              'not create a voiceprint or retrain the model. Audio stays in memory '
              'on this phone and is discarded when you leave.',
            ),
            const SizedBox(height: 28),
            LinearProgressIndicator(value: done / _prompts.length),
            const SizedBox(height: 12),
            Text(
              'Sample ${done + 1} of ${_prompts.length}',
              style: Theme.of(context).textTheme.labelLarge,
            ),
            const SizedBox(height: 20),
            Text(
              _calibrating
                  ? 'Checking your samples on-device…'
                  : _prompts[done.clamp(0, _prompts.length - 1)],
              style: Theme.of(context).textTheme.headlineSmall,
            ),
            if (_error != null) ...[
              const SizedBox(height: 16),
              Text(
                _error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            const Spacer(),
            FilledButton.icon(
              onPressed: _recording || _calibrating ? null : _capture,
              icon: Icon(_recording ? Icons.mic : Icons.mic_none),
              label: Text(
                _recording ? 'Recording 3 seconds…' : 'Record sample',
              ),
            ),
            TextButton(
              onPressed: _recording || _calibrating
                  ? null
                  : () async {
                      await WakeWord.resetCalibration();
                      if (mounted) {
                        _finished = true;
                        Navigator.pop(this.context, false);
                      }
                    },
              child: const Text('Reset to default sensitivity'),
            ),
          ],
        ),
      ),
    );
  }
}
