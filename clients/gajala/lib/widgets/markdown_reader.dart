import 'package:android_intent_plus/android_intent.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:markdown/markdown.dart' as md;

/// Document content inside the caller's scroll view.
class MarkdownReader extends StatefulWidget {
  final String source;
  final String copyLabel;
  final Widget Function(String path)? attachmentBuilder;

  const MarkdownReader({
    super.key,
    required this.source,
    this.copyLabel = 'Copy Markdown',
    this.attachmentBuilder,
  });

  @override
  State<MarkdownReader> createState() => _MarkdownReaderState();
}

class _MarkdownReaderState extends State<MarkdownReader> {
  bool _showSource = false;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final attachmentBuilder = widget.attachmentBuilder;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Wrap(
          alignment: WrapAlignment.end,
          children: [
            TextButton(
              onPressed: () => setState(() => _showSource = !_showSource),
              child: Text(_showSource ? 'Reading view' : 'View source'),
            ),
            TextButton.icon(
              onPressed: () =>
                  _copy(context, widget.source, 'Markdown copied.'),
              icon: const Icon(Icons.copy_outlined, size: 18),
              label: Text(widget.copyLabel),
            ),
          ],
        ),
        SelectionArea(
          child: _showSource
              ? Text(
                  widget.source,
                  style: const TextStyle(fontFamily: 'monospace'),
                )
              : MarkdownBody(
                  data: widget.source,
                  fitContent: false,
                  extensionSet: md.ExtensionSet.gitHubFlavored,
                  inlineSyntaxes: [
                    if (attachmentBuilder != null) _AttachmentSyntax(),
                  ],
                  builders: {
                    'pre': _CodeBlockBuilder(),
                    if (attachmentBuilder != null)
                      'gajala-attachment': _AttachmentBuilder(
                        attachmentBuilder,
                      ),
                  },
                  styleSheet: MarkdownStyleSheet.fromTheme(theme).copyWith(
                    p: theme.textTheme.bodyMedium?.copyWith(height: 1.5),
                    blockSpacing: 12,
                    tableColumnWidth: const IntrinsicColumnWidth(),
                    tableHead: theme.textTheme.bodyMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  imageBuilder: (_, title, alt) => Text(
                    alt?.isNotEmpty == true ? alt! : title ?? 'Image',
                    style: theme.textTheme.bodyMedium?.copyWith(
                      fontStyle: FontStyle.italic,
                    ),
                  ),
                  onTapLink: (_, href, _) => _openLink(context, href),
                ),
        ),
      ],
    );
  }
}

Future<void> _copy(BuildContext context, String text, String feedback) async {
  await Clipboard.setData(ClipboardData(text: text));
  if (context.mounted) {
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(feedback)));
  }
}

Future<void> _openLink(BuildContext context, String? href) async {
  final uri = href == null ? null : Uri.tryParse(href);
  if (uri == null ||
      !{'http', 'https'}.contains(uri.scheme) ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty) {
    return;
  }
  try {
    await AndroidIntent(
      action: 'android.intent.action.VIEW',
      data: uri.toString(),
    ).launch();
  } catch (_) {
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(content: Text('Could not open this link.')),
      );
    }
  }
}

class _CodeBlockBuilder extends MarkdownElementBuilder {
  @override
  Widget visitElementAfterWithContext(
    BuildContext context,
    md.Element element,
    TextStyle? preferredStyle,
    TextStyle? parentStyle,
  ) {
    final code = element.textContent;
    final child = element.children?.firstOrNull;
    final language = child is md.Element
        ? child.attributes['class']?.replaceFirst('language-', '')
        : null;
    final theme = Theme.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest,
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(
                    language?.toUpperCase() ?? 'CODE',
                    overflow: TextOverflow.ellipsis,
                    style: theme.textTheme.labelSmall,
                  ),
                ),
                IconButton(
                  tooltip: 'Copy code',
                  onPressed: () => _copy(context, code, 'Code copied.'),
                  icon: const Icon(Icons.copy_outlined, size: 18),
                ),
              ],
            ),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.only(bottom: 8),
              child: Text(
                code,
                style: theme.textTheme.bodySmall?.copyWith(
                  fontFamily: 'monospace',
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Keep the existing authenticated attachment path for worker file markers.
class _AttachmentSyntax extends md.InlineSyntax {
  _AttachmentSyntax()
    : super(r'\[file:\s*(/[^\]\r\n\x00]+?)\s*\]', startCharacter: 91);

  @override
  bool onMatch(md.InlineParser parser, Match match) {
    parser.addNode(
      md.Element.empty('gajala-attachment')
        ..attributes['path'] = match[1]!.trim(),
    );
    return true;
  }
}

class _AttachmentBuilder extends MarkdownElementBuilder {
  final Widget Function(String path) buildAttachment;
  _AttachmentBuilder(this.buildAttachment);

  @override
  Widget visitElementAfterWithContext(
    BuildContext context,
    md.Element element,
    TextStyle? preferredStyle,
    TextStyle? parentStyle,
  ) => buildAttachment(element.attributes['path']!);
}
