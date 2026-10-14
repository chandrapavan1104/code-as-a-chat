// Turns a shell's raw output stream into display lines for the Terminal screen.
//
// The Mac runs zsh with TERM=dumb and no line editor, so output is mostly plain
// text. What remains is handled the way a simple terminal would: colour and
// cursor escape codes are dropped, `\r` returns to the start of the line so
// progress bars overwrite themselves, `\b` steps back, and the bell is silent.

final _escape = RegExp(
  r'\x1B(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1B]*(?:\x07|\x1B\\)|[@-Z\\-_])',
);

String stripAnsi(String text) => text.replaceAll(_escape, '');

class TerminalBuffer {
  TerminalBuffer({this.maxLines = 4000});

  final int maxLines;
  final List<String> _lines = [];
  String _current = '';
  int _cursor = 0;
  String _carry = ''; // an escape sequence split across two chunks

  /// Completed lines plus the line still being written (the prompt, usually).
  List<String> get lines => [..._lines, _current];

  String get text => lines.join('\n');

  void clear() {
    _lines.clear();
    _current = '';
    _cursor = 0;
  }

  void append(String chunk) {
    var data = _carry + chunk;
    _carry = '';
    // Hold back a trailing, unfinished escape sequence until the next chunk.
    final lastEsc = data.lastIndexOf('\x1B');
    if (lastEsc >= 0 && !_escape.hasMatch(data.substring(lastEsc)) &&
        data.length - lastEsc < 64) {
      _carry = data.substring(lastEsc);
      data = data.substring(0, lastEsc);
    }
    for (final ch in stripAnsi(data).split('')) {
      switch (ch) {
        case '\n':
          _lines.add(_current);
          _current = '';
          _cursor = 0;
        case '\r':
          _cursor = 0;
        case '\b':
          if (_cursor > 0) _cursor--;
        case '\x07':
          break;
        default:
          if (_cursor < _current.length) {
            _current = _current.replaceRange(_cursor, _cursor + 1, ch);
          } else {
            _current += ch;
          }
          _cursor++;
      }
    }
    if (_lines.length > maxLines) {
      _lines.removeRange(0, _lines.length - maxLines);
    }
  }
}
