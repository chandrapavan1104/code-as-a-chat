import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:gajala/main.dart';

void main() {
  testWidgets('app boots to the connect screen', (tester) async {
    FlutterSecureStorage.setMockInitialValues({});
    await tester.pumpWidget(const ProviderScope(child: GajalaApp()));
    // The app-lock gate reads its setting before showing anything, so the
    // first frame is intentionally blank.
    await tester.pump();
    await tester.pump();
    expect(find.text('Gajala'), findsWidgets);
  });
}
