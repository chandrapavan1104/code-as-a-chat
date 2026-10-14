import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/terminal_text.dart';

void main() {
  test('plain lines and the trailing prompt', () {
    final b = TerminalBuffer()..append('one\r\ntwo\r\nuser@mac ~ % ');
    expect(b.lines, ['one', 'two', 'user@mac ~ % ']);
  });

  test('colour codes are dropped, even when split across chunks', () {
    final b = TerminalBuffer()
      ..append('\x1B[1;3')
      ..append('2mgreen\x1B[0m done\n');
    expect(b.lines.first, 'green done');
  });

  test('carriage return overwrites a progress line; backspace steps back', () {
    final b = TerminalBuffer()
      ..append('10%\r55%\r100%\n')
      ..append('abc\b\bX\n');
    expect(b.lines.take(2), ['100%', 'aXc']);
  });

  test('old lines are dropped past the limit', () {
    final b = TerminalBuffer(maxLines: 3);
    for (var i = 0; i < 10; i++) {
      b.append('line $i\n');
    }
    expect(b.lines, ['line 7', 'line 8', 'line 9', '']);
  });
}
