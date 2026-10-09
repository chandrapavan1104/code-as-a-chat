import 'package:flutter/material.dart';

import '../core/models.dart';
import '../core/theme.dart';

class WorkResultButton extends StatelessWidget {
  final QueueResultSummary result;
  final VoidCallback onPressed;
  const WorkResultButton({
    super.key,
    required this.result,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) => FilledButton.icon(
    onPressed: onPressed,
    icon: const Icon(Icons.open_in_new),
    label: Text(
      result.completeness == 'partial' ? 'Open partial result' : 'Open result',
    ),
    style: FilledButton.styleFrom(
      backgroundColor: GajalaColors.accent,
      foregroundColor: Colors.white,
      minimumSize: const Size.fromHeight(44),
    ),
  );
}

class WorkAttemptTimeline extends StatelessWidget {
  final List<QueueAttempt> attempts;
  const WorkAttemptTimeline({super.key, required this.attempts});

  @override
  Widget build(BuildContext context) {
    if (attempts.isEmpty) return const SizedBox.shrink();
    return ExpansionTile(
      tilePadding: EdgeInsets.zero,
      title: const Text('Attempts and logs'),
      subtitle: Text(
        '${attempts.length} recorded attempt${attempts.length == 1 ? '' : 's'}',
      ),
      children: [
        for (final attempt in attempts) _AttemptTile(attempt: attempt),
      ],
    );
  }
}

class _AttemptTile extends StatelessWidget {
  final QueueAttempt attempt;
  const _AttemptTile({required this.attempt});

  String _time(double? epoch) {
    if (epoch == null) return '—';
    return DateTime.fromMillisecondsSinceEpoch(
      (epoch * 1000).round(),
    ).toLocal().toString().split('.').first;
  }

  @override
  Widget build(BuildContext context) => ExpansionTile(
    tilePadding: const EdgeInsets.only(left: 12),
    title: Text('Attempt ${attempt.attemptNo} · ${attempt.engine}'),
    subtitle: Text(
      '${attempt.stage} · ${attempt.status} · ${_time(attempt.startedAt)}',
    ),
    childrenPadding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
    children: [
      if (attempt.error?.isNotEmpty == true)
        _LogSection(title: 'Error', text: attempt.error!),
      if (attempt.exitCode != null)
        Align(
          alignment: Alignment.centerLeft,
          child: Text('Exit code: ${attempt.exitCode}'),
        ),
      if (attempt.output?.isNotEmpty == true)
        _LogSection(
          title: 'Final output',
          text:
              '${attempt.output}${attempt.outputTruncated ? '\n… output truncated' : ''}',
        ),
      if (attempt.stdout?.isNotEmpty == true)
        _LogSection(
          title: 'stdout',
          text:
              '${attempt.stdout}${attempt.stdoutTruncated ? '\n… stdout truncated' : ''}',
        ),
      if (attempt.stderr?.isNotEmpty == true)
        _LogSection(
          title: 'stderr',
          text:
              '${attempt.stderr}${attempt.stderrTruncated ? '\n… stderr truncated' : ''}',
        ),
      Text('Finished: ${_time(attempt.endedAt)}'),
    ],
  );
}

class _LogSection extends StatelessWidget {
  final String title, text;
  const _LogSection({required this.title, required this.text});

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.only(top: 8),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: Theme.of(context).textTheme.labelLarge),
        const SizedBox(height: 3),
        SelectableText(text, style: TextStyle(color: context.pal.textDim)),
      ],
    ),
  );
}
