import 'package:flutter/material.dart';
import '../core/models.dart';
import '../core/theme.dart';

/// Old research replies embedded their quote in prose. Present it without
/// rewriting history or changing the receipt that prevents duplicate delivery.
({String body, String? quote, String sender}) replyPresentation(
  ChatMessage message,
) {
  var body = message.text;
  var quote = message.replyToContent;
  var sender = message.replyToRole == 'user' ? 'You' : 'Gajala';
  if (message.localRequestId?.startsWith('research-result:') == true &&
      body.startsWith('Research reply to: ')) {
    final split = body.indexOf('\n\n');
    if (split > 0) {
      if (quote == null || quote.isEmpty) {
        quote = body.substring('Research reply to: '.length, split);
        sender = 'You';
      }
      body = body.substring(split + 2);
    }
  }
  return (body: body, quote: quote, sender: sender);
}

/// A compact quote inside the message, with a sender and a bounded preview.
class QuotedReply extends StatelessWidget {
  final String sender, text;
  final VoidCallback? onTap;
  const QuotedReply({
    super.key,
    required this.sender,
    required this.text,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final accent = sender == 'You' ? GajalaColors.green : GajalaColors.accent;
    final preview = text.replaceAll(RegExp(r'\s+'), ' ').trim();
    return Semantics(
      button: onTap != null,
      label: onTap == null
          ? 'Quoted message from $sender'
          : 'Go to quoted message from $sender',
      child: Material(
        color: context.pal.bg.withValues(alpha: .45),
        borderRadius: BorderRadius.circular(7),
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: Container(
            width: double.infinity,
            decoration: BoxDecoration(
              border: Border(left: BorderSide(color: accent, width: 3)),
            ),
            padding: const EdgeInsets.fromLTRB(9, 7, 9, 7),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  sender,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: accent,
                    fontSize: 12,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  preview,
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                  style: TextStyle(
                    color: context.pal.textDim,
                    fontSize: 12.5,
                    height: 1.25,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
