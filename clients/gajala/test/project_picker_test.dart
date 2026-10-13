import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:gajala/core/chat_controller.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/core/state.dart';
import 'package:gajala/screens/chat_screen.dart';

class PickerApi extends GajalaApi {
  PickerApi() : super('http://localhost', 'test');
  final catalog = Completer<Map<String, dynamic>>();
  final sessions = Completer<Map<String, dynamic>>();
  bool failProjects = false;
  bool failSwitch = false;
  String? switched;
  @override
  Future<Map<String, dynamic>> projects() async {
    if (failProjects) throw StateError('offline');
    return {'current_name': switched ?? 'alpha', 'projects': [{'name': 'alpha'}, {'name': 'beta'}]};
  }
  @override
  Future<Map<String, dynamic>> model({bool refresh = false, bool includeCatalog = true}) => includeCatalog ? catalog.future : Future.value({'engine': 'auto'});
  @override
  Future<Map<String, dynamic>> activeSessions() => sessions.future;
  @override
  Future<String> switchProject(String name) async {
    if (failSwitch) throw StateError('offline');
    switched = name;
    return name;
  }
}

class TestChat extends ChatController {
  TestChat(ChatKey key) : super(null, key);
  @override
  Future<void> ensureLoaded({String? welcome}) async {
    state = state.copyWith(loaded: true, messages: [
      ChatMessage('bot', key.sid.endsWith('::beta') ? 'Beta project history' : 'Original conversation history')]);
  }
  Future<void> refreshBackgroundResearch() async {}
  Future<void> replayOutbox() async {}
}

Future<void> openPicker(WidgetTester tester, PickerApi api,
    Future<void> Function(String?, String?) changed) async {
  await tester.pumpWidget(MaterialApp(home: Scaffold(body: Builder(
    builder: (context) => TextButton(onPressed: () => showModalBottomSheet(
      context: context, isScrollControlled: true,
      builder: (_) => ChatContextSheet(api: api, currentDir: 'alpha',
        currentModel: 'auto', onChanged: changed)), child: const Text('Open'))))));
  await tester.tap(find.text('Open'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('explicit project selection replaces chat history and persists on reopen', (tester) async {
    FlutterSecureStorage.setMockInitialValues({
      'selected_conversation_id': 'app:install:conversation:independent',
      'selected_conversation_project': 'alpha',
      'selected_conversation_independent': 'true',
    });
    final api = PickerApi();
    final container = ProviderContainer(overrides: [
      apiProvider.overrideWithValue(api),
      sessionIdProvider.overrideWith((ref) async => 'install'),
      chatControllerProvider.overrideWith((ref, key) => TestChat(key)),
    ]);
    addTearDown(container.dispose);
    Widget app() => UncontrolledProviderScope(container: container,
      child: const MaterialApp(home: ChatScreen()));
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    expect(find.text('Original conversation history'), findsOneWidget);
    await tester.tap(find.text('Gajala'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('beta'));
    await tester.pumpAndSettle();
    expect(find.text('Beta project history'), findsOneWidget);
    expect(find.text('Original conversation history'), findsNothing);
    expect(find.text('beta · auto'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpAndSettle();
    await tester.pumpWidget(app());
    await tester.pumpAndSettle();
    expect(find.text('Beta project history'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 1));
    expect(tester.takeException(), isNull);
  });

  testWidgets('projects load while catalog and sessions remain pending', (tester) async {
    final api = PickerApi();
    await openPicker(tester, api, (_, _) async {});
    expect(find.text('beta'), findsOneWidget);
    expect(find.text('No projects found.'), findsNothing);
    expect(tester.getTopLeft(find.text('beta')).dy,
        lessThan(tester.getTopLeft(find.text('CODING ENGINE')).dy));
    expect(tester.takeException(), isNull);
  });

  testWidgets('switch awaits the chat callback and dismisses without session discovery', (tester) async {
    final api = PickerApi();
    final opened = Completer<void>();
    String? chat;
    await openPicker(tester, api, (dir, _) async { chat = dir; await opened.future; });
    await tester.tap(find.text('beta'));
    await tester.pump();
    expect(api.switched, 'beta');
    expect(chat, 'beta');
    expect(find.byType(ChatContextSheet), findsOneWidget);
    opened.complete();
    await tester.pumpAndSettle();
    expect(find.byType(ChatContextSheet), findsNothing);
  });

  testWidgets('project errors show retry and do not masquerade as an empty catalog', (tester) async {
    final api = PickerApi()..failProjects = true;
    await openPicker(tester, api, (_, _) async {});
    expect(find.text('Could not load projects.'), findsOneWidget);
    expect(find.text('No projects found.'), findsNothing);
    api.failProjects = false;
    await tester.tap(find.text('Retry projects'));
    await tester.pumpAndSettle();
    expect(find.text('beta'), findsOneWidget);
  });

  testWidgets('a switch failure stays visible and can be retried', (tester) async {
    final api = PickerApi()..failSwitch = true;
    await openPicker(tester, api, (_, _) async {});
    await tester.tap(find.text('beta'));
    await tester.pump();
    expect(find.text('Could not switch project. Please retry.'), findsOneWidget);
    expect(find.byType(ChatContextSheet), findsOneWidget);
    api.failSwitch = false;
    await tester.tap(find.text('beta'));
    await tester.pumpAndSettle();
    expect(api.switched, 'beta');
    expect(find.byType(ChatContextSheet), findsNothing);
  });

  testWidgets('selecting the active project still opens its project chat', (tester) async {
    final api = PickerApi();
    String? selected;
    await openPicker(tester, api, (dir, _) async { selected = dir; });
    await tester.tap(find.text('alpha'));
    await tester.pumpAndSettle();
    expect(selected, 'alpha');
    expect(find.byType(ChatContextSheet), findsNothing);
  });

  Future<ProviderContainer> openChat(WidgetTester tester, PickerApi api,
      Map<String, String> saved) async {
    FlutterSecureStorage.setMockInitialValues(saved);
    final container = ProviderContainer(overrides: [
      apiProvider.overrideWithValue(api),
      sessionIdProvider.overrideWith((ref) async => 'install'),
      chatControllerProvider.overrideWith((ref, key) => TestChat(key)),
    ]);
    addTearDown(container.dispose);
    await tester.pumpWidget(UncontrolledProviderScope(container: container,
      child: const MaterialApp(home: ChatScreen())));
    await tester.pumpAndSettle();
    return container;
  }

  void resume(WidgetTester tester) {
    for (final s in const [AppLifecycleState.inactive, AppLifecycleState.hidden,
        AppLifecycleState.paused, AppLifecycleState.hidden,
        AppLifecycleState.inactive, AppLifecycleState.resumed]) {
      tester.binding.handleAppLifecycleStateChanged(s);
    }
  }

  testWidgets('a switch made on another screen moves an open chat to that project', (tester) async {
    final api = PickerApi();
    await openChat(tester, api, {
      'selected_conversation_id': 'app:install:conversation:independent',
      'selected_conversation_project': 'alpha',
      'selected_conversation_independent': 'true',
    });
    expect(find.text('Original conversation history'), findsOneWidget);
    await requestProjectChat('install', 'beta'); // what Library → Projects does
    await tester.pumpAndSettle();
    expect(find.text('Beta project history'), findsOneWidget);
    expect(find.text('beta · auto'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 1));
  });

  testWidgets('resume keeps the open project chat unless the server default changed', (tester) async {
    final api = PickerApi(); // server default stays alpha, as after an agent switch
    await openChat(tester, api, {
      'selected_conversation_id': 'install::beta',
      'selected_conversation_project': 'beta',
      'selected_conversation_independent': 'false',
    });
    expect(find.text('Beta project history'), findsOneWidget);
    resume(tester);
    await tester.pumpAndSettle();
    expect(find.text('Beta project history'), findsOneWidget);
    api.switched = 'gamma'; // switched explicitly elsewhere (Mac, Telegram)
    resume(tester);
    await tester.pumpAndSettle();
    expect(find.text('Original conversation history'), findsOneWidget);
    expect(find.text('gamma · auto'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpAndSettle();
    await tester.pump(const Duration(seconds: 1));
  });
}
