import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/widget_bridge.dart';

void main() {
  test('Mac widget distinguishes a successful run response', () {
    expect(macActionFailure({'result': 'Mac locked.'}), isNull);
  });

  test('Mac widget surfaces a tool failure returned with HTTP 200', () {
    expect(
      macActionFailure({'result': '[mac] lock failed: permission denied'}),
      '[mac] lock failed: permission denied',
    );
  });

  test('Mac widget rejects malformed successful responses', () {
    expect(macActionFailure({'command': 'mac'}), 'No response from Mac');
    expect(macActionFailure('ok'), 'Invalid response from Mac');
  });
}
