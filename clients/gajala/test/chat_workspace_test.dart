import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/chat_controller.dart';

/// Regression tests for the "screen blinks and won't switch project" bug.
///
/// `ChatState.workspace` is a ONE-SHOT signal meaning "the last turn ended in
/// this project". Controllers are kept alive per thread for the app's lifetime,
/// so a value left sitting there is re-read on every rebuild — and because
/// following it swaps which controller the screen watches, two threads each
/// holding a stale value point at each other and the screen ping-pongs between
/// them forever.
void main() {
  group('workspace is a one-shot signal', () {
    test('copyWith can clear it back to null', () {
      const state = ChatState(workspace: 'general');
      expect(state.copyWith(clearWorkspace: true).workspace, isNull);
    });

    test('a plain copyWith still preserves it', () {
      const state = ChatState(workspace: 'general');
      expect(state.copyWith(sending: true).workspace, 'general');
    });

    test('consumeWorkspace clears it, and is idempotent', () {
      final c = ChatController(null, const ChatKey('shell', 's::general'));
      c.state = c.state.copyWith(workspace: 'general');
      expect(c.state.workspace, 'general');

      c.consumeWorkspace();
      expect(c.state.workspace, isNull,
          reason: 'an unconsumed signal re-fires on every rebuild');

      c.consumeWorkspace(); // must not throw or resurrect anything
      expect(c.state.workspace, isNull);
    });

    test('two threads cannot point at each other once consumed', () {
      // The exact shape of the blink: thread A says "go to general", thread B
      // says "go to deaf-communication-terminal". Following either one swaps
      // the watched controller; unless the signal is consumed, they alternate
      // forever.
      final a = ChatController(null, const ChatKey('shell', 's::deaf'));
      final b = ChatController(null, const ChatKey('shell', 's::general'));
      a.state = a.state.copyWith(workspace: 'general');
      b.state = b.state.copyWith(workspace: 'deaf-communication-terminal');

      a.consumeWorkspace();
      b.consumeWorkspace();

      expect(a.state.workspace, isNull);
      expect(b.state.workspace, isNull);
    });
  });

  group('the [[switch:name]] marker', () {
    test('is stripped from the reply and yields the project', () {
      final (clean, target) =
          splitSwitch('Switched to general.\n[[switch:general]]');
      expect(target, 'general');
      expect(clean, 'Switched to general.');
      expect(clean.contains('[[switch'), isFalse);
    });

    test('leaves an ordinary reply untouched', () {
      final (clean, target) = splitSwitch('No marker here.');
      expect(target, isNull);
      expect(clean, 'No marker here.');
    });

    test('handles a project name with dots and dashes', () {
      final (_, target) = splitSwitch('done [[switch:deaf-communication-terminal]]');
      expect(target, 'deaf-communication-terminal');
    });
  });
}
