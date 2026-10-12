import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:gajala/core/api.dart';
import 'package:gajala/core/home_confirmation.dart';
import 'package:gajala/core/push.dart';
import 'package:gajala/screens/home_screen.dart';

class HomeTestApi extends GajalaApi {
  HomeTestApi({
    this.stalePanel = false,
    this.staleDevice = false,
    this.pendingGarage = false,
  }) : super('http://127.0.0.1:1', 'test');
  final bool stalePanel;
  final bool staleDevice;
  final bool pendingGarage;
  final actions = <Map<String, dynamic>>[];
  final garageRequests = <({String target, bool open})>[];
  var garageConfirmations = 0;

  @override
  Future<Map<String, dynamic>> homeStatus() async {
    final now = DateTime.now().millisecondsSinceEpoch / 1000;
    return {
      'status': {
        'connected': true,
        'updated_at': now - (stalePanel ? 46 : 0),
        'troubles': ['Control Low Battery'],
        'areas': [],
        'zones': [],
      },
      'switches': {
        'available': true,
        'loaded': true,
        'switches': [
          {
            'id': 'light.kitchen',
            'name': 'Kitchen light',
            'room': 'Kitchen',
            'on': false,
            'level': 1,
            'dimmable': false,
            'reachable': true,
            'state_confirmed': true,
            'observed_at': now - (staleDevice ? 601 : 0),
          },
          {
            'id': 'light.offline',
            'name': 'Offline light',
            'room': 'Hall',
            'on': false,
            'reachable': false,
            'state_confirmed': true,
            'observed_at': now,
          },
          {
            'id': 'light.unknown',
            'name': 'Unknown light',
            'room': 'Den',
            'on': null,
            'reachable': true,
            'state_confirmed': true,
            'observed_at': now,
          },
        ],
      },
      'climate': {
        'thermostats': [
          {
            'id': 'level-2',
            'name': 'Upstairs',
            'temp': 70,
            'unit': 'F',
            'mode': 'off',
            'activity': 'idle',
            'fan': 'auto',
            'heat': 71,
            'cool': 74,
            'observed_fields': {
              'temp': now,
              'mode': now,
              'heat': now,
              'cool': now,
              'fan': now,
              'activity': now,
            },
          },
        ],
      },
      'garage': {
        'doors': [
          {
            'id': 'garage',
            'name': 'Garage',
            'state': pendingGarage ? 'opening' : 'closed',
            'state_confirmed': !pendingGarage,
            'observed_at': now,
          },
        ],
      },
    };
  }

  @override
  Future<Map<String, dynamic>> homeAction(Map<String, dynamic> action) async {
    actions.add(Map<String, dynamic>.from(action));
    return {'status': 'succeeded', 'message': 'Updated'};
  }

  @override
  Future<Map<String, dynamic>> requestGarageAction({
    required String target,
    required bool open,
  }) async {
    garageRequests.add((target: target, open: open));
    return {'token': 'one-use-token', 'expires_at': 'soon'};
  }

  @override
  Future<Map<String, dynamic>> confirmGarageAction(String token) async {
    garageConfirmations++;
    return {'status': 'accepted'};
  }
}

Future<void> pumpHome(WidgetTester tester, HomeTestApi api) async {
  await tester.pumpWidget(MaterialApp(home: HomeScreen(api: api)));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('shows connectivity troubles and unknown safety state plainly', (
    tester,
  ) async {
    await pumpHome(tester, HomeTestApi());
    expect(find.text('Home service connected · status fresh'), findsOneWidget);
    expect(find.textContaining('Control Low Battery'), findsOneWidget);
    expect(
      find.textContaining('No enrolled sensors are reported'),
      findsOneWidget,
    );
    expect(
      find.textContaining('does not indicate whether the home is secure'),
      findsOneWidget,
    );
    expect(find.textContaining('Unreachable'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('panel older than 45 seconds is stale and disables controls', (
    tester,
  ) async {
    await pumpHome(tester, HomeTestApi(stalePanel: true));
    expect(find.text('Home service connected · status stale'), findsOneWidget);
    expect(find.textContaining('Updated '), findsOneWidget);
    expect(tester.widget<Switch>(find.byType(Switch).first).onChanged, isNull);
    final allOff = tester.widget<TextButton>(
      find.widgetWithText(TextButton, 'Turn all switches off'),
    );
    expect(allOff.onPressed, isNull);
    await tester.scrollUntilVisible(
      find.text('Upstairs'),
      160,
      scrollable: find.byType(Scrollable).first,
    );
    expect(
      tester
          .widget<IconButton>(
            find.ancestor(
              of: find.byTooltip('Adjust thermostat'),
              matching: find.byType(IconButton),
            ),
          )
          .onPressed,
      isNull,
    );
  });

  testWidgets('switch observation older than ten minutes is marked stale', (
    tester,
  ) async {
    await pumpHome(tester, HomeTestApi(staleDevice: true));
    expect(
      find.textContaining('Last observed off · stale read'),
      findsOneWidget,
    );
    expect(find.text('Set on'), findsWidgets);
  });

  testWidgets('garage moving state without confirmation stays pending', (
    tester,
  ) async {
    final api = HomeTestApi(pendingGarage: true);
    await pumpHome(tester, api);
    final open = find.byTooltip('Open at garage door');
    await tester.scrollUntilVisible(
      open,
      160,
      scrollable: find.byType(Scrollable).first,
    );
    expect(
      find.textContaining('Reported opening · device read pending'),
      findsOneWidget,
    );
    expect(
      tester
          .widget<IconButton>(
            find.ancestor(of: open, matching: find.byType(IconButton)),
          )
          .onPressed,
      isNull,
    );
    expect(
      tester
          .widget<IconButton>(
            find.ancestor(
              of: find.byTooltip('Close at garage door'),
              matching: find.byType(IconButton),
            ),
          )
          .onPressed,
      isNull,
    );
  });

  testWidgets(
    'switch sends exact target and state; unreachable switch is disabled',
    (tester) async {
      final api = HomeTestApi();
      await pumpHome(tester, api);
      await tester.tap(find.byType(Switch).first);
      await tester.pumpAndSettle();
      expect(api.actions, [
        {'action': 'switch', 'target': 'light.kitchen', 'state': true},
      ]);
      final switches = tester.widgetList<Switch>(find.byType(Switch)).toList();
      expect(switches[1].onChanged, isNull);
    },
  );

  testWidgets('null switch state is unknown and offers explicit set actions', (
    tester,
  ) async {
    final api = HomeTestApi();
    await pumpHome(tester, api);
    final row = find.ancestor(
      of: find.text('Unknown light'),
      matching: find.byType(ListTile),
    );
    expect(
      find.descendant(of: row, matching: find.textContaining('State unknown')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: row, matching: find.text('Set on')),
      findsOneWidget,
    );
    expect(
      find.descendant(of: row, matching: find.text('Set off')),
      findsOneWidget,
    );
    expect(find.descendant(of: row, matching: find.text('Off')), findsNothing);
  });

  testWidgets('climate form sends selected thermostat and setpoints', (
    tester,
  ) async {
    final api = HomeTestApi();
    await pumpHome(tester, api);
    final thermostat = find.text('Upstairs');
    await tester.scrollUntilVisible(
      thermostat,
      160,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.ensureVisible(thermostat);
    await tester.pumpAndSettle();
    await tester.tap(thermostat);
    await tester.pumpAndSettle();
    expect(find.text('Heat setpoint (55.0–85.0°F)'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Apply'));
    await tester.pumpAndSettle();
    expect(api.actions, [
      {
        'action': 'climate',
        'target': 'level-2',
        'mode': 'off',
        'heat': 71.0,
        'cool': 74.0,
        'fan': 'auto',
      },
    ]);
  });

  testWidgets('garage request cannot be confirmed before onsite checkbox', (
    tester,
  ) async {
    final api = HomeTestApi();
    await pumpHome(tester, api);
    final open = find.byTooltip('Open at garage door');
    await tester.scrollUntilVisible(
      open,
      160,
      scrollable: find.byType(Scrollable).first,
    );
    await tester.tap(open);
    await tester.pumpAndSettle();
    expect(api.garageRequests, [(target: 'garage', open: true)]);
    expect(api.garageConfirmations, 0);
    expect(find.text("I'm at the door and can observe it."), findsOneWidget);
    final confirm = tester.widget<FilledButton>(
      find.widgetWithText(FilledButton, 'Confirm'),
    );
    expect(confirm.onPressed, isNull);
    await tester.tap(find.byType(CheckboxListTile));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Confirm'));
    await tester.pumpAndSettle();
    expect(api.garageConfirmations, 1);
    expect(api.actions, isEmpty);
  });

  testWidgets('onsite confirmation dialog requires the fresh checkbox', (
    tester,
  ) async {
    bool? confirmed;
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () async =>
                  confirmed = await showOnsiteConfirmationDialog(
                    context,
                    title: 'Confirm at the garage door',
                    detail: 'Open Garage?',
                  ),
              child: const Text('Open dialog'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open dialog'));
    await tester.pumpAndSettle();
    expect(
      tester
          .widget<FilledButton>(find.widgetWithText(FilledButton, 'Confirm'))
          .onPressed,
      isNull,
    );
    await tester.tap(find.byType(CheckboxListTile));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Confirm'));
    await tester.pumpAndSettle();
    expect(confirmed, isTrue);
  });

  testWidgets('home.confirm returns only the explicit onsite decision', (
    tester,
  ) async {
    Map<String, dynamic>? result;
    await tester.pumpWidget(
      MaterialApp(
        navigatorKey: Push.navigatorKey,
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () async => result = await confirmHomePhoneRequest({
                'target': 'garage',
                'name': 'Garage',
                'open': true,
              }),
              child: const Text('Handle request'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Handle request'));
    await tester.pumpAndSettle();
    expect(
      find.text(
        'Open Garage? Only continue while you are at the door and can observe it.',
      ),
      findsOneWidget,
    );
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(result, {
      'ok': false,
      'cancelled': true,
      'data': {'confirmed': false, 'present': false},
      'error': 'Cancelled; the garage action was not confirmed.',
    });
  });
}
