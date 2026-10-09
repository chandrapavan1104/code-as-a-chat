import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter/services.dart';

/// Adds a right-swipe reply affordance without joining the gesture arena.
/// SelectableText and the parent chat list therefore keep their own gestures.
class SwipeReply extends StatefulWidget {
  final Widget child;
  final VoidCallback onReply;
  final double threshold;
  final String semanticLabel;
  final bool swipeEnabled;

  const SwipeReply({
    super.key,
    required this.child,
    required this.onReply,
    this.threshold = 72,
    this.semanticLabel = 'Reply to message',
    this.swipeEnabled = true,
  });

  @override
  State<SwipeReply> createState() => _SwipeReplyState();
}

class _SwipeReplyState extends State<SwipeReply> {
  int? _pointer;
  Offset? _origin;
  Timer? _longPressGuard;
  bool _locked = false;
  bool _crossed = false;
  bool _hapticsSent = false;
  double _offset = 0;

  @override
  void dispose() {
    _longPressGuard?.cancel();
    super.dispose();
  }

  void _down(PointerDownEvent event) {
    if (!widget.swipeEnabled || _pointer != null) return;
    _pointer = event.pointer;
    _origin = event.position;
    _locked = false;
    _crossed = false;
    _hapticsSent = false;
    // A long press belongs to SelectableText (selection handles/context menu).
    _longPressGuard = Timer(
      const Duration(milliseconds: 500),
      () => _locked = true,
    );
  }

  void _move(PointerMoveEvent event) {
    if (event.pointer != _pointer || _origin == null || _locked) return;
    final delta = event.position - _origin!;
    if (delta.dy.abs() > 12 && delta.dy.abs() > delta.dx.abs() * 1.1) {
      _locked = true; // let ListView own vertical scrolling
      _reset();
      return;
    }
    if (delta.dx < 0 || delta.dx < delta.dy.abs() * 1.1) return;

    final crossed = delta.dx >= widget.threshold;
    if (crossed && !_hapticsSent) {
      _hapticsSent = true;
      HapticFeedback.selectionClick();
    }
    setState(() {
      _offset = math.min(delta.dx, widget.threshold + 18);
      _crossed = crossed;
    });
  }

  void _up(PointerEvent event) {
    if (event.pointer != _pointer) return;
    final reply = _crossed;
    _clearPointer();
    _reset();
    if (reply) widget.onReply();
  }

  void _cancel(PointerCancelEvent event) {
    if (event.pointer != _pointer) return;
    _clearPointer();
    _reset();
  }

  void _clearPointer() {
    _longPressGuard?.cancel();
    _pointer = null;
    _origin = null;
  }

  void _reset() {
    if (!mounted) return;
    setState(() {
      _offset = 0;
      _crossed = false;
    });
  }

  @override
  Widget build(BuildContext context) => Semantics(
    customSemanticsActions: {
      CustomSemanticsAction(label: 'Reply'): widget.onReply,
    },
    child: Listener(
      behavior: HitTestBehavior.translucent,
      onPointerDown: _down,
      onPointerMove: _move,
      onPointerUp: _up,
      onPointerCancel: _cancel,
      child: Stack(
        children: [
          Positioned.fill(
            child: Align(
              alignment: Alignment.centerLeft,
              child: Padding(
                padding: const EdgeInsets.only(left: 8),
                child: AnimatedOpacity(
                  opacity: _offset == 0
                      ? 0
                      : (_offset / widget.threshold).clamp(0, 1),
                  duration: const Duration(milliseconds: 80),
                  child: Icon(
                    Icons.reply_rounded,
                    color: _crossed
                        ? Theme.of(context).colorScheme.primary
                        : Theme.of(context).colorScheme.onSurfaceVariant,
                    size: 20,
                    semanticLabel: widget.semanticLabel,
                  ),
                ),
              ),
            ),
          ),
          AnimatedContainer(
            duration: const Duration(milliseconds: 360),
            curve: Curves.elasticOut,
            transform: Matrix4.translationValues(_offset, 0, 0),
            child: widget.child,
          ),
        ],
      ),
    ),
  );
}
