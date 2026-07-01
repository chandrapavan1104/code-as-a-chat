import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:gajala/main.dart';

void main() {
  testWidgets('app boots to the connect screen', (tester) async {
    await tester.pumpWidget(const ProviderScope(child: GajalaApp()));
    await tester.pump();
    expect(find.text('Gajala'), findsWidgets);
  });
}
