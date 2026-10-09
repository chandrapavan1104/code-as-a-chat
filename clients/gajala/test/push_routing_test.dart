import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/push.dart';

void main() {
  tearDown(() {
    Push.onOpenWork = null;
    Push.onOpenTasks = null;
    Push.onOpenChat = null;
  });
  test(
    'queue status opens the exact result, old payload falls back to Work',
    () {
      int? job;
      var work = 0;
      Push.onOpenWork = (id) => job = id;
      Push.onOpenTasks = () => work++;
      Push.routeData({'type': 'queue_status', 'ref_id': '24'});
      expect(job, 24);
      expect(work, 0);
      Push.routeData({'type': 'queue_status'});
      expect(work, 1);
    },
  );
  test('cold launch work route waits for the connected shell', () {
    Push.routeData({'type': 'queue_status', 'ref_id': '88'});
    int? job;
    Push.onOpenWork = (id) => job = id;
    Push.flushPendingRoute();
    expect(job, 88);
    job = null;
    Push.flushPendingRoute();
    expect(job, isNull);
  });
  test('chat reply retains its exact conversation', () {
    String? session;
    Push.onOpenChat = (sid) => session = sid;
    Push.routeData({
      'type': 'chat_reply',
      'session_id': 'app:install::conversation:one',
    });
    expect(session, 'app:install::conversation:one');
  });
}
