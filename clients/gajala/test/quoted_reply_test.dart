import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/widgets/quoted_reply.dart';

void main() {
  test('saved research reply separates legacy question from answer', () {
    final view = replyPresentation(
      ChatMessage(
        'bot',
        'Research reply to: My original question\n\nResearch failed: Timeout',
        localRequestId: 'research-result:old',
      ),
    );
    expect(view.quote, 'My original question');
    expect(view.sender, 'You');
    expect(view.body, 'Research failed: Timeout');
  });
  test('exact linked source overrides legacy truncated preview', () {
    final view = replyPresentation(
      ChatMessage(
        'bot',
        'Research reply to: Truncated…\n\nAnswer',
        localRequestId: 'research-result:old',
        replyToContent: 'The exact original question',
        replyToRole: 'user',
      ),
    );
    expect(view.quote, 'The exact original question');
    expect(view.body, 'Answer');
  });
  test('ordinary text is not rewritten', () {
    final text = 'Research reply to: A literal example\n\nKeep this.';
    expect(replyPresentation(ChatMessage('bot', text)).body, text);
  });
  testWidgets('quote has separate sender, bounded preview and tap navigation', (
    tester,
  ) async {
    var jumps = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 250,
            child: QuotedReply(
              sender: 'You',
              text: List.filled(80, 'original question').join(' '),
              onTap: () => jumps++,
            ),
          ),
        ),
      ),
    );
    expect(find.text('You'), findsOneWidget);
    final preview = tester.widget<Text>(
      find.textContaining('original question'),
    );
    expect(preview.maxLines, 2);
    expect(preview.overflow, TextOverflow.ellipsis);
    await tester.tap(find.text('You'));
    expect(jumps, 1);
    expect(tester.takeException(), isNull);
  });
}
