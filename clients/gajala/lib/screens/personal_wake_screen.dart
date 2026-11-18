import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/wake_word.dart';

/// Raw microphone audio stays in Android. This screen receives only features.
class PersonalWakeScreen extends StatefulWidget {
  const PersonalWakeScreen({super.key});

  @override
  State<PersonalWakeScreen> createState() => _PersonalWakeScreenState();
}

class _PersonalWakeScreenState extends State<PersonalWakeScreen>
    with WidgetsBindingObserver {
  static const channel = MethodChannel('gajala/wakeword/personal');
  static const requiredExamples = 3;
  static const maxExamples = 8;
  List<List<double>> _saved = [];
  final List<List<double>> _draft = [];
  bool _loading = true;
  bool _recording = false;
  bool _retraining = false;
  String? _message;
  int _generation = 0;

  bool get _teaching => _saved.isEmpty || _retraining;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    unawaited(_open());
  }

  Future<void> _open() async {
    await WakeWord.hold();
    if (!mounted) return;
    try {
      final rows = await channel.invokeListMethod<List<Object?>>('examples');
      if (mounted) {
        setState(
          () => _saved = [
            for (final row in rows ?? const <List<Object?>>[])
              row.map((value) => (value as num).toDouble()).toList(),
          ],
        );
      }
    } on PlatformException catch (error) {
      if (mounted) setState(() => _message = error.message);
    } on MissingPluginException {
      if (mounted)
        setState(() => _message = 'Voice training requires Android.');
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) return;
    _generation++;
    unawaited(WakeWord.cancelEnrollment());
    setState(() {
      _draft.clear();
      _recording = false;
    });
  }

  @override
  void dispose() {
    _generation++;
    WidgetsBinding.instance.removeObserver(this);
    unawaited(WakeWord.cancelEnrollment());
    unawaited(WakeWord.release());
    _draft.clear();
    super.dispose();
  }

  Future<void> _record({bool practice = false}) async {
    final generation = ++_generation;
    setState(() {
      _message = null;
      _recording = true;
    });
    bool current() => mounted && generation == _generation;
    try {
      final row = await channel.invokeListMethod<double>('capture');
      if (!current()) return;
      if (row == null) throw StateError('No recording was captured.');
      if (practice) {
        final detected = await channel.invokeMethod<bool>('match', row);
        if (current()) {
          setState(
            () => _message = detected == true
                ? 'Heard “Gajala.”'
                : 'Missed that one. You can add this pronunciation as another example.',
          );
        }
        return;
      }
      final teaching = _teaching;
      final examples = teaching
          ? [..._draft, row]
          : [..._saved, row].takeLast(maxExamples);
      if (teaching && examples.length < requiredExamples) {
        setState(() => _draft.add(row));
        return;
      }
      if (await channel.invokeMethod<bool>('save', examples) != true) {
        throw StateError(
          'Could not save your voice examples. Try this recording again.',
        );
      }
      if (current()) {
        setState(() {
          _saved = examples;
          _draft.clear();
          _retraining = false;
          _message = teaching
              ? 'Your voice is ready to try.'
              : 'Example added.';
        });
      }
    } on PlatformException catch (error) {
      if (current())
        setState(() => _message = error.message ?? 'Try this recording again.');
    } catch (error) {
      if (current()) setState(() => _message = error.toString());
    } finally {
      if (current()) setState(() => _recording = false);
    }
  }

  Future<void> _reset() async {
    if (await channel.invokeMethod<bool>('reset') != true || !mounted) return;
    setState(() {
      _saved = [];
      _draft.clear();
      _retraining = false;
      _message = 'Saved examples deleted. Your previous wake setup is active.';
    });
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Teach my voice')),
    body: ListView(
      padding: const EdgeInsets.all(24),
      children: [
        if (_loading)
          const Center(child: CircularProgressIndicator())
        else ...[
          Text(
            _teaching ? 'Say “Gajala” three times' : 'Personalized “Gajala”',
            style: Theme.of(context).textTheme.headlineSmall,
          ),
          const SizedBox(height: 12),
          Text(
            _teaching
                ? 'Say it naturally once per recording. Pause briefly after the word. '
                      'The phone keeps only the speech features it needs to recognize you.'
                : '${_saved.length} examples saved on this phone. Personalized wake is used '
                      'while unlocked when hands-free listening is on.',
          ),
          const SizedBox(height: 20),
          if (_teaching) ...[
            LinearProgressIndicator(value: _draft.length / requiredExamples),
            const SizedBox(height: 10),
            Text('Recording ${_draft.length + 1} of $requiredExamples'),
            FilledButton(
              onPressed: _recording ? null : () => _record(),
              child: Text(_recording ? 'Listening…' : 'Record “Gajala”'),
            ),
            if (_retraining)
              TextButton(
                onPressed: _recording
                    ? null
                    : () => setState(() {
                        _draft.clear();
                        _retraining = false;
                        _message = 'Previous profile kept.';
                      }),
                child: const Text('Cancel retraining'),
              ),
          ] else ...[
            FilledButton(
              onPressed: _recording ? null : () => _record(practice: true),
              child: Text(_recording ? 'Listening…' : 'Try it now'),
            ),
            OutlinedButton(
              onPressed: _recording ? null : () => _record(),
              child: const Text('Add an example'),
            ),
            TextButton(
              onPressed: _recording
                  ? null
                  : () => setState(() {
                      _retraining = true;
                      _message = null;
                    }),
              child: const Text('Retrain'),
            ),
            TextButton(
              onPressed: _recording ? null : _reset,
              child: const Text('Reset my voice'),
            ),
          ],
          if (_message != null)
            Semantics(liveRegion: true, child: Text(_message!)),
        ],
      ],
    ),
  );
}

extension<T> on List<T> {
  List<T> takeLast(int max) => length <= max ? this : sublist(length - max);
}
