import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/core/theme.dart';
import 'package:gajala/widgets/run_trace.dart';

void main() {
  final trace = RunTrace.fromJson({
    'id': 'r1',
    'workspace': '/Users/x/Projects/general',
    'stop_reason': 'done',
    'brains': 'claude',
    'duration_ms': 4200,
    'charged_steps': 1,
    'steps': [
      {'idx': 1, 'tool': 'claude', 'args': 'fix it', 'result': 'done', 'ok': 1, 'charged': 1},
    ],
    'usage': {
      'input_tokens': 51200,
      'output_tokens': 80,
      'cost_usd': 0.0042,
      'cost_complete': false,
      'calls': [
        {'source': 'router', 'model': 'claude-sonnet', 'input_tokens': 1200, 'output_tokens': 80},
        {'source': 'codex', 'model': 'gpt-6-astra', 'input_tokens': 50000, 'output_tokens': null},
      ],
    },
  });

  test('usage is parsed', () {
    expect(trace.usage!.inputTokens, 51200);
    expect(trace.usage!.calls.last.output, isNull);
    expect(compactTokens(51200), '51.2k');
    expect(RunTrace.fromJson({'steps': []}).usage, isNull);
  });

  testWidgets('expanded trace shows tokens, cost and each model call', (tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildTheme(Brightness.dark),
      home: Scaffold(body: RunTraceStrip.fromTrace(trace)),
    ));
    await tester.tap(find.byType(InkWell).first);
    await tester.pumpAndSettle();
    expect(
      find.text('51.2k in · 80 out · \$0.0042 + unpriced calls'
          '\nrouter · claude-sonnet · 1.2k/80\ncodex · gpt-6-astra · 50.0k'),
      findsOneWidget,
    );
  });
}
