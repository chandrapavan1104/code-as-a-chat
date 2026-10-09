import 'package:flutter/material.dart';
import '../core/theme.dart';
import 'quoted_reply.dart';

/// Compact chat input with optional attachment and reply trays.
class ChatComposer extends StatefulWidget {
  final TextEditingController controller;
  final FocusNode focusNode;
  final bool sending;
  final bool dictating;
  final String? replyPreview;
  final String replySender;
  final Widget? attachmentPreview;
  final VoidCallback? onCancelReply;
  final VoidCallback? onAddPhoto;
  final VoidCallback onDictate;
  final VoidCallback onSend;
  final ValueChanged<String>? onDraftChanged;

  const ChatComposer({
    super.key,
    required this.controller,
    required this.focusNode,
    required this.sending,
    required this.dictating,
    this.replyPreview,
    this.replySender = 'Gajala',
    this.attachmentPreview,
    this.onCancelReply,
    this.onAddPhoto,
    required this.onDictate,
    required this.onSend,
    this.onDraftChanged,
  });

  @override
  State<ChatComposer> createState() => _ChatComposerState();
}

class _ChatComposerState extends State<ChatComposer> {
  @override
  void initState() {
    super.initState();
    widget.controller.addListener(_onTextChanged);
  }

  @override
  void didUpdateWidget(covariant ChatComposer oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller.removeListener(_onTextChanged);
      widget.controller.addListener(_onTextChanged);
    }
  }

  @override
  void dispose() {
    widget.controller.removeListener(_onTextChanged);
    super.dispose();
  }

  void _onTextChanged() {
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final hasText = widget.controller.text.trim().isNotEmpty;
    return Container(
      decoration: BoxDecoration(
        color: context.pal.bg,
        border: Border(top: BorderSide(color: context.pal.border)),
      ),
      padding: const EdgeInsets.fromLTRB(8, 8, 8, 12),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (widget.attachmentPreview != null)
            Align(
              alignment: Alignment.centerLeft,
              child: Padding(
                padding: const EdgeInsets.only(bottom: 8, left: 4),
                child: widget.attachmentPreview,
              ),
            ),
          if (widget.replyPreview != null)
            Container(
              margin: const EdgeInsets.only(bottom: 8),
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
              decoration: BoxDecoration(
                color: context.pal.surfaceAlt,
                borderRadius: BorderRadius.circular(9),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: QuotedReply(
                      sender: widget.replySender,
                      text: widget.replyPreview!,
                    ),
                  ),
                  IconButton(
                    visualDensity: VisualDensity.compact,
                    tooltip: 'Cancel reply',
                    onPressed: widget.onCancelReply,
                    icon: const Icon(Icons.close, size: 18),
                  ),
                ],
              ),
            ),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              PopupMenuButton<String>(
                tooltip: 'Add attachment or dictate',
                padding: EdgeInsets.zero,
                onSelected: (action) {
                  if (action == 'dictate') {
                    widget.onDictate();
                  } else if (!widget.sending) {
                    widget.onAddPhoto?.call();
                  }
                },
                itemBuilder: (_) => [
                  PopupMenuItem(
                    value: 'photo',
                    enabled: !widget.sending,
                    child: const ListTile(
                      leading: Icon(Icons.add_photo_alternate_outlined),
                      title: Text('Add photo'),
                    ),
                  ),
                  const PopupMenuItem(
                    value: 'dictate',
                    child: ListTile(
                      leading: Icon(Icons.mic_none),
                      title: Text('Dictate'),
                    ),
                  ),
                ],
                child: SizedBox(
                  width: 48,
                  height: 48,
                  child: Icon(
                    Icons.add_circle_outline,
                    color: context.pal.textDim,
                  ),
                ),
              ),
              Expanded(
                child: TextField(
                  controller: widget.controller,
                  focusNode: widget.focusNode,
                  minLines: 1,
                  maxLines: 4,
                  keyboardType: TextInputType.multiline,
                  textInputAction: TextInputAction.newline,
                  onChanged: widget.onDraftChanged,
                  decoration: InputDecoration(
                    hintText: widget.dictating
                        ? 'Listening…'
                        : widget.sending
                        ? 'Follow-up message…'
                        : 'Message…',
                    isDense: true,
                    filled: true,
                    fillColor: context.pal.surfaceAlt,
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 14,
                      vertical: 12,
                    ),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(24),
                      borderSide: BorderSide.none,
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(24),
                      borderSide: BorderSide.none,
                    ),
                    focusedBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(24),
                      borderSide: BorderSide(
                        color: GajalaColors.accent.withValues(alpha: .7),
                      ),
                    ),
                  ),
                ),
              ),
              const SizedBox(width: 8),
              SizedBox(
                width: 48,
                height: 48,
                child: IconButton.filled(
                  padding: EdgeInsets.zero,
                  icon: Icon(
                    widget.dictating
                        ? Icons.stop_circle_outlined
                        : !hasText
                        ? Icons.mic_none
                        : widget.sending
                        ? Icons.playlist_add
                        : Icons.send,
                    size: 20,
                  ),
                  onPressed: widget.dictating
                      ? widget.onDictate
                      : hasText
                      ? widget.onSend
                      : widget.onDictate,
                  tooltip: widget.dictating
                      ? 'Stop dictation'
                      : !hasText
                      ? 'Dictate'
                      : widget.sending
                      ? 'Send follow-up'
                      : 'Send',
                  style: IconButton.styleFrom(
                    backgroundColor: widget.dictating
                        ? GajalaColors.danger
                        : GajalaColors.accent,
                    foregroundColor: Colors.white,
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
