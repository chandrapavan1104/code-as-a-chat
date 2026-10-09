import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/models.dart';
import 'package:gajala/core/state.dart';
import 'package:gajala/screens/library_screen.dart';
import 'package:gajala/screens/dashboard_screen.dart';
import 'package:gajala/screens/ports_screen.dart';
import 'package:gajala/screens/reminders_screen.dart';
import 'package:gajala/screens/skill_action_screen.dart';

Skill skill(String name) => Skill(
  name: name,
  command: name,
  description: name,
  helpLine: name,
  exposeToAgent: true,
  enabled: true,
);
void main() {
  testWidgets('Library has compact accessible rows', (tester) async {
    tester.view.physicalSize = const Size(360, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      ProviderScope(
        overrides: [
          skillsProvider.overrideWith(
            (ref) async => [
              for (final n in [
                'filemanager',
                'diary',
                'notes',
                'reminders',
                'projects',
                'sessions',
                'claude',
                'codex',
                'antigravity',
                'ports',
              ])
                skill(n),
            ],
          ),
        ],
        child: const MaterialApp(home: LibraryScreen()),
      ),
    );
    await tester.pumpAndSettle();
    final row = find.widgetWithText(ListTile, 'Notes');
    expect(tester.getSize(row).height, greaterThanOrEqualTo(48));
    expect(tester.getSize(row).height, lessThanOrEqualTo(52));
    expect(
      tester.getTopLeft(find.text('Notes')).dy,
      lessThan(tester.getTopLeft(find.text('Diary')).dy),
    );
    expect(tester.takeException(), isNull);
  });
  test('Fixed skills have native controls and task forms', () {
    expect(screenForSkill(skill('ports')), isA<PortsScreen>());
    expect(screenForSkill(skill('reminders')), isA<RemindersScreen>());
    expect(screenForSkill(skill('context')), isA<SkillActionScreen>());
    expect(screenForSkill(skill('claude')), isA<SkillActionScreen>());
  });
}
