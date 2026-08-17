import 'package:flutter/material.dart';

import '../core/models.dart';
import '../core/theme.dart';

/// The record of what an agent turn actually did.
///
/// Two modes, deliberately the same widget: while a turn runs it shows the steps
/// arriving live, and once the reply lands the very same strip stays under the
/// bubble, collapsed. What you watched is what you can reopen — previously the
/// live bubble was destroyed the moment the reply arrived, so a turn that went
/// sideways left nothing to look at.
class RunTraceStrip extends StatefulWidget {
  /// Steps as they arrive over the stream (live mode), or the persisted trace.
  final List<RunStep> steps;
  final String? project;
  final int? durationMs;
  final String? stopLabel;
  final bool live;
  final bool hitStepLimit;
  final VoidCallback? onContinue;

  const RunTraceStrip({
    super.key,
    required this.steps,
    this.project,
    this.durationMs,
    this.stopLabel,
    this.live = false,
    this.hitStepLimit = false,
    this.onContinue,
  });

  /// Build from a fetched trace.
  factory RunTraceStrip.fromTrace(RunTrace t, {VoidCallback? onContinue}) =>
      RunTraceStrip(
        steps: t.steps,
        project: t.projectName,
        durationMs: t.durationMs,
        stopLabel: t.stopLabel,
        hitStepLimit: t.hitStepLimit,
        onContinue: onContinue,
      );

  @override
  State<RunTraceStrip> createState() => _RunTraceStripState();
}

class _RunTraceStripState extends State<RunTraceStrip> {
  // Live turns start open so you can watch; finished ones start collapsed so
  // the chat stays readable.
  late bool _open = widget.live;

  @override
  void didUpdateWidget(RunTraceStrip old) {
    super.didUpdateWidget(old);
    // Collapse automatically when a live turn finishes.
    if (old.live && !widget.live) _open = false;
  }

  String get _summary {
    final n = widget.steps.length;
    final parts = <String>[n == 1 ? '1 step' : '$n steps'];
    final ms = widget.durationMs;
    if (ms != null && ms > 0) {
      parts.add(ms < 1000 ? '${ms}ms' : '${(ms / 1000).toStringAsFixed(ms < 10000 ? 1 : 0)}s');
    }
    if (widget.project != null && widget.project!.isNotEmpty) {
      parts.add(widget.project!);
    }
    return parts.join(' · ');
  }

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    if (widget.steps.isEmpty && !widget.live) return const SizedBox.shrink();

    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        margin: const EdgeInsets.only(top: 2, bottom: 6),
        constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * .86),
        decoration: BoxDecoration(
          color: pal.botBubble.withValues(alpha: .55),
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: pal.border),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            _header(pal),
            if (_open) ...[
              Divider(height: 1, color: pal.border),
              ...widget.steps.map((s) => _StepRow(step: s)),
              if (widget.stopLabel != null) _footer(pal),
            ],
            if (widget.hitStepLimit && widget.onContinue != null) _continue(pal),
          ],
        ),
      ),
    );
  }

  Widget _header(Pal pal) => InkWell(
        onTap: () => setState(() => _open = !_open),
        borderRadius: BorderRadius.circular(10),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            if (widget.live)
              SizedBox(
                width: 12,
                height: 12,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: pal.textDim),
              )
            else
              Icon(_open ? Icons.expand_more : Icons.chevron_right,
                  size: 16, color: pal.textDim),
            const SizedBox(width: 8),
            Flexible(
              child: Text(_summary,
                  style: TextStyle(
                      color: pal.textDim,
                      fontSize: 12.5,
                      fontWeight: FontWeight.w500)),
            ),
          ]),
        ),
      );

  Widget _footer(Pal pal) => Padding(
        padding: const EdgeInsets.fromLTRB(12, 6, 12, 9),
        child: Row(children: [
          Icon(widget.hitStepLimit ? Icons.timer_off_outlined : Icons.flag_outlined,
              size: 13,
              color: widget.hitStepLimit ? GajalaColors.warn : pal.textDim),
          const SizedBox(width: 6),
          Flexible(
            child: Text(widget.stopLabel!,
                style: TextStyle(
                    color: widget.hitStepLimit ? GajalaColors.warn : pal.textDim,
                    fontSize: 11.5)),
          ),
        ]),
      );

  Widget _continue(Pal pal) => Padding(
        padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
        child: Align(
          alignment: Alignment.centerLeft,
          child: TextButton.icon(
            onPressed: widget.onContinue,
            icon: const Icon(Icons.play_arrow, size: 16),
            label: const Text('Continue'),
            style: TextButton.styleFrom(
                foregroundColor: GajalaColors.accent,
                visualDensity: VisualDensity.compact,
                padding: const EdgeInsets.symmetric(horizontal: 10)),
          ),
        ),
      );
}

class _StepRow extends StatelessWidget {
  final RunStep step;
  const _StepRow({required this.step});

  @override
  Widget build(BuildContext context) {
    final pal = context.pal;
    // A step the user was not charged for is dimmed: it happened, but it was
    // the router's mistake, not their work.
    final dim = !step.charged;
    final icon = step.ok
        ? (dim ? Icons.remove : Icons.check)
        : Icons.close;
    final color = !step.ok
        ? GajalaColors.danger
        : (dim ? pal.textDim.withValues(alpha: .6) : GajalaColors.ok);

    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 6, 10, 6),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Padding(
          padding: const EdgeInsets.only(top: 2),
          child: Icon(icon, size: 14, color: color),
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(children: [
              Text(step.tool,
                  style: TextStyle(
                      color: dim ? pal.textDim : pal.text,
                      fontSize: 12.5,
                      fontWeight: FontWeight.w600)),
              const Spacer(),
              if (step.durationMs > 0)
                Text(
                    step.durationMs < 1000
                        ? '${step.durationMs}ms'
                        : '${(step.durationMs / 1000).toStringAsFixed(1)}s',
                    style: TextStyle(color: pal.textDim, fontSize: 11)),
            ]),
            if (step.args.isNotEmpty)
              Text(step.args,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(color: pal.textDim, fontSize: 11.5)),
            if (!step.ok && step.firstResultLine.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 2),
                child: Text(step.firstResultLine,
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(
                        color: GajalaColors.danger, fontSize: 11.5)),
              ),
          ]),
        ),
      ]),
    );
  }
}
