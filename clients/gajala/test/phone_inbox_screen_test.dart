import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/screens/phone_inbox_screen.dart';

class _InboxApi extends GajalaApi {
  _InboxApi() : super('http://localhost', 'test');

  final attempts = <String>[];
  final receipts = <Map<String, dynamic>>[];

  @override
  Future<List<Map<String, dynamic>>> phoneInbox() async => receipts.toList();

  @override
  Future<Map<String, dynamic>> sendPhoneFile({
    required String id,
    required String name,
    required File file,
    required CancelToken cancel,
    required ProgressCallback progress,
  }) async {
    attempts.add(id);
    if (attempts.length == 1) throw StateError('Connection lost');
    final receipt = {'id': id, 'name': name, 'size': 128, 'path': '/phone/$name'};
    receipts.add(receipt);
    return receipt;
  }
}

void main() {
  testWidgets('reviews shared and picked files and retries with the same ID', (
    tester,
  ) async {
    final api = _InboxApi();
    final messenger =
        TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
    messenger.setMockMethodCallHandler(phoneImportChannel, (call) async {
      if (call.method == 'pick') {
        return [
          {'id': 'oversized', 'name': 'large.zip', 'size': 201 * 1024 * 1024},
        ];
      }
      if (call.method == 'stage') return '/staged/report.md';
      return null;
    });
    addTearDown(
      () => messenger.setMockMethodCallHandler(phoneImportChannel, null),
    );
    await tester.pumpWidget(
      MaterialApp(
        home: PhoneInboxScreen(
          api: api,
          shared: const [
            {'id': 'stable-id', 'name': 'report.md', 'size': 128},
          ],
        ),
      ),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('Choose files'));
    await tester.pumpAndSettle();
    expect(find.text('large.zip'), findsOneWidget);
    expect(find.textContaining('exceeds 200 MiB'), findsOneWidget);
    expect(api.attempts, isEmpty);
    await tester.tap(find.text('Send to Mac'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Connection lost'), findsOneWidget);
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(api.attempts, ['stable-id', 'stable-id']);
    await tester.tap(find.byTooltip('File actions'));
    await tester.pumpAndSettle();
    expect(find.text('Copy Mac path'), findsOneWidget);
    expect(find.text('Attach to chat'), findsOneWidget);
  });
}
