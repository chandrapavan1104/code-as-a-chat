import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/home_confirmation.dart';
import '../core/state.dart';

Widget homeScreenRoute() => Consumer(
  builder: (context, ref, _) {
    final api = ref.watch(apiProvider);
    return api == null
        ? const Scaffold(body: Center(child: Text('Connect to your Mac first')))
        : HomeScreen(api: api);
  },
);

/// Read-only home status with explicit, narrowly scoped device controls.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.api});
  final GajalaApi api;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  late Future<Map<String, dynamic>> _future;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _future = widget.api.homeStatus();
  }

  Future<void> _refresh() async {
    if (!mounted) return;
    final future = widget.api.homeStatus();
    setState(() {
      _future = future;
    });
    try {
      await future;
    } catch (_) {}
  }

  void _message(String value) {
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(SnackBar(content: Text(value)));
  }

  Future<void> _run(Map<String, dynamic> action) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final result = await widget.api.homeAction(action);
      final status = result['status']?.toString();
      final message = result['message']?.toString();
      _message(
        message?.isNotEmpty == true
            ? message!
            : status == 'succeeded'
            ? 'Action completed.'
            : status == 'unverified'
            ? 'Action was sent; the final device state is unverified.'
            : 'Action status: ${status ?? 'unknown'}',
      );
      await _refresh();
    } catch (e) {
      _message(friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _switch(
    Map<String, dynamic> device,
    bool on, {
    int? level,
  }) async {
    final target = device['id']?.toString();
    if (target == null || target.isEmpty) return;
    await _run({
      'action': 'switch',
      'target': target,
      'state': on,
      if (level != null) 'level': level.clamp(1, 99),
    });
  }

  Future<void> _climate(Map<String, dynamic> thermostat) async {
    final target = thermostat['id']?.toString();
    if (target == null || target.isEmpty) return;
    final unit = thermostat['unit']?.toString() ?? 'F';
    final initial = <String, dynamic>{
      'mode': thermostat['mode']?.toString() ?? 'off',
      'heat': _number(thermostat['heat'], unit == 'C' ? 21 : 71),
      'cool': _number(thermostat['cool'], unit == 'C' ? 24 : 74),
      'fan': thermostat['fan']?.toString() ?? 'auto',
      'unit': unit,
    };
    final selection = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (context) => _ClimateDialog(
        name: thermostat['name']?.toString() ?? 'Thermostat',
        initial: initial,
      ),
    );
    if (selection == null || !mounted) return;
    await _run({'action': 'climate', 'target': target, ...selection});
  }

  Future<void> _garage(Map<String, dynamic> door, bool open) async {
    final target = door['id']?.toString();
    if (target == null || target.isEmpty || _busy) return;
    setState(() => _busy = true);
    try {
      final request = await widget.api.requestGarageAction(
        target: target,
        open: open,
      );
      final token = request['token']?.toString();
      if (token == null || token.isEmpty) {
        throw StateError(
          'The garage request did not return a confirmation token.',
        );
      }
      if (!mounted) return;
      final confirmed = await showOnsiteConfirmationDialog(
        context,
        title: 'Confirm at the garage door',
        detail:
            '${open ? 'Open' : 'Close'} ${door['name'] ?? 'garage door'}? '
            'Only continue while you are at the door and can observe it.',
      );
      if (!confirmed || !mounted) {
        _message('Cancelled; no garage action was confirmed.');
        return;
      }
      final result = await widget.api.confirmGarageAction(token);
      final status = result['status']?.toString();
      final message = result['message']?.toString();
      _message(
        message?.isNotEmpty == true
            ? message!
            : status == 'succeeded'
            ? 'Garage action completed.'
            : 'Garage request: ${status ?? 'accepted'}; checking device state.',
      );
      await _refresh();
    } catch (e) {
      _message(friendlyError(e));
      await _refresh();
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _allOff() async {
    final confirmed =
        await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
            title: const Text('Turn all switches off?'),
            content: const Text(
              'This affects every available switch. Thermostats and garage doors are not included.',
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context, false),
                child: const Text('Cancel'),
              ),
              FilledButton.tonal(
                onPressed: () => Navigator.pop(context, true),
                child: const Text('Turn all off'),
              ),
            ],
          ),
        ) ??
        false;
    if (confirmed) await _run({'action': 'all_off'});
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Home'),
      actions: [
        IconButton(
          tooltip: 'Refresh home status',
          onPressed: _busy ? null : _refresh,
          icon: const Icon(Icons.refresh),
        ),
      ],
    ),
    body: FutureBuilder<Map<String, dynamic>>(
      future: _future,
      builder: (context, snapshot) {
        if (snapshot.connectionState == ConnectionState.waiting &&
            !snapshot.hasData) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snapshot.hasError) {
          return _loadError(friendlyError(snapshot.error!));
        }
        final data = snapshot.data ?? const <String, dynamic>{};
        final status = _map(data['status']);
        final switches = _map(data['switches']);
        final climate = _map(data['climate']);
        final garage = _map(data['garage']);
        final deviceList = _list(switches['switches']);
        final thermostats = _list(climate['thermostats']);
        final doors = _list(garage['doors']);
        final trouble = _list(status['troubles']);
        final zones = _list(status['zones']);
        final areas = _list(status['areas']);
        final connected = status['connected'] == true;
        final panelAge = _ageSeconds(status['updated_at']);
        final panelFresh =
            connected && panelAge != null && panelAge >= 0 && panelAge <= 45;
        final switchesReady =
            panelFresh &&
            switches['available'] == true &&
            switches['loaded'] == true;
        final err = [
          status['error'],
          switches['error'],
        ].where((x) => x != null && x.toString().trim().isNotEmpty).join(' · ');

        return RefreshIndicator(
          onRefresh: _refresh,
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
            children: [
              _StatusCard(
                connected: connected,
                fresh: panelFresh,
                updatedAt: status['updated_at'],
                trouble: trouble,
                error: err,
                areas: areas,
                zones: zones,
              ),
              if (status['connected'] == true && zones.isEmpty)
                const Padding(
                  padding: EdgeInsets.fromLTRB(4, 8, 4, 0),
                  child: Text(
                    'No enrolled sensors are reported. This does not indicate whether the home is secure.',
                    style: TextStyle(fontSize: 12),
                  ),
                ),
              const SizedBox(height: 12),
              _sectionHeader(
                context,
                'Switches',
                subtitle: switches['loaded'] == false
                    ? 'Not loaded'
                    : '${deviceList.length} available',
              ),
              if (deviceList.isEmpty)
                _emptyCard(
                  switches['available'] == false
                      ? 'Switch controls are unavailable.'
                      : 'No switches were returned.',
                )
              else
                Card(
                  clipBehavior: Clip.antiAlias,
                  child: Column(
                    children: [
                      for (var i = 0; i < deviceList.length; i++) ...[
                        if (i > 0) const Divider(height: 1, indent: 16),
                        _switchTile(deviceList[i], switchesReady),
                      ],
                    ],
                  ),
                ),
              if (deviceList.isNotEmpty) ...[
                const SizedBox(height: 4),
                Align(
                  alignment: Alignment.centerRight,
                  child: TextButton.icon(
                    onPressed:
                        _busy ||
                            !panelFresh ||
                            switches['loaded'] != true ||
                            switches['available'] != true
                        ? null
                        : _allOff,
                    icon: const Icon(Icons.power_settings_new),
                    label: const Text('Turn all switches off'),
                  ),
                ),
              ],
              const SizedBox(height: 8),
              _sectionHeader(context, 'Climate'),
              if (thermostats.isEmpty)
                _emptyCard('No thermostats were returned.')
              else
                Card(
                  clipBehavior: Clip.antiAlias,
                  child: Column(
                    children: [
                      for (var i = 0; i < thermostats.length; i++) ...[
                        if (i > 0) const Divider(height: 1, indent: 16),
                        _thermostatTile(thermostats[i], panelFresh),
                      ],
                    ],
                  ),
                ),
              const SizedBox(height: 8),
              _sectionHeader(context, 'Garage'),
              if (doors.isEmpty)
                _emptyCard('No garage doors were returned.')
              else
                Card(
                  clipBehavior: Clip.antiAlias,
                  child: Column(
                    children: [
                      for (var i = 0; i < doors.length; i++) ...[
                        if (i > 0) const Divider(height: 1, indent: 16),
                        _garageTile(doors[i], panelFresh),
                      ],
                    ],
                  ),
                ),
              if (err.isNotEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Text(
                    err,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                ),
            ],
          ),
        );
      },
    ),
  );

  Widget _loadError(String error) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(error, textAlign: TextAlign.center),
          const SizedBox(height: 12),
          FilledButton.tonal(onPressed: _refresh, child: const Text('Retry')),
        ],
      ),
    ),
  );

  Widget _switchTile(Map<String, dynamic> device, bool switchesReady) {
    final reachable = device['reachable'] == true;
    final dimmable = device['dimmable'] == true;
    final state = _boolOrNull(device['on']);
    final observationAge = _ageSeconds(device['observed_at']);
    final observationFresh =
        device['state_confirmed'] == true &&
        state != null &&
        observationAge != null &&
        observationAge >= 0 &&
        observationAge <= _observationStaleSeconds;
    final on = state == true;
    final name =
        device['name']?.toString() ?? device['id']?.toString() ?? 'Switch';
    final room = device['room']?.toString();
    final level = _number(device['level'], on ? 99 : 1).clamp(1, 99).toDouble();
    final stateLabel = _switchStateLabel(device);
    final canControl = switchesReady && reachable && !_busy;
    return Column(
      children: [
        if (observationFresh)
          SwitchListTile.adaptive(
            title: Text(name),
            subtitle: Text(
              [
                if (room?.isNotEmpty == true) room!,
                stateLabel,
                if (!reachable) 'Unreachable',
                if (!switchesReady)
                  'Home panel or switch catalog stale or unavailable',
              ].join(' · '),
            ),
            value: on,
            onChanged: canControl ? (value) => _switch(device, value) : null,
          )
        else
          ListTile(
            title: Text(name),
            subtitle: Text(
              [
                if (room?.isNotEmpty == true) room!,
                stateLabel,
                if (!reachable) 'Unreachable or reachability unknown',
                if (!switchesReady)
                  'Home panel or switch catalog stale or unavailable',
              ].join(' · '),
            ),
            trailing: Wrap(
              spacing: 0,
              children: [
                TextButton(
                  onPressed: canControl ? () => _switch(device, true) : null,
                  child: const Text('Set on'),
                ),
                TextButton(
                  onPressed: canControl ? () => _switch(device, false) : null,
                  child: const Text('Set off'),
                ),
              ],
            ),
          ),
        if (dimmable && observationFresh && on && device['level'] is num)
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
            child: Row(
              children: [
                const Icon(Icons.lightbulb_outline, size: 18),
                Expanded(
                  child: Slider(
                    min: 1,
                    max: 99,
                    divisions: 98,
                    label: '${level.round()}%',
                    value: level,
                    onChanged: canControl ? (_) {} : null,
                    onChangeEnd: canControl
                        ? (value) => _switch(device, true, level: value.round())
                        : null,
                  ),
                ),
                Text('${level.round()}%'),
              ],
            ),
          ),
      ],
    );
  }

  String _switchStateLabel(Map<String, dynamic> device) {
    final state = _boolOrNull(device['on']);
    final confirmed = device['state_confirmed'] == true;
    final age = _ageSeconds(device['observed_at']);
    if (!confirmed) {
      return state == null
          ? 'State unknown · device read not confirmed'
          : 'Requested ${state ? 'on' : 'off'} · device read pending';
    }
    if (age == null) return 'State unknown · observation time unavailable';
    final value = state == null ? 'state unknown' : (state ? 'on' : 'off');
    if (age < 0 || age > _observationStaleSeconds) {
      return 'Last observed $value · stale read ${_ageLabel(age)}';
    }
    if (state == null) return 'State unknown';
    return 'Last observed ${state ? 'on' : 'off'} · ${_ageLabel(age)}';
  }

  Widget _thermostatTile(Map<String, dynamic> thermostat, bool panelFresh) {
    final fieldAges = _climateFieldAges(thermostat['observed_fields']);
    final detail = fieldAges.isEmpty
        ? 'Climate read freshness unknown'
        : 'Device reads: ${fieldAges.entries.map((e) => '${e.key} ${e.value == null ? 'freshness unknown' : _ageLabel(e.value!)}').join(' · ')}';
    return ListTile(
      leading: const Icon(Icons.thermostat_outlined),
      title: Text(thermostat['name']?.toString() ?? 'Thermostat'),
      subtitle: Text(
        'Last reported ${thermostat['temp'] ?? 'unknown'}°${thermostat['unit'] ?? ''} · '
        '${thermostat['mode'] ?? 'mode unknown'} · ${thermostat['activity'] ?? 'activity unknown'}\n$detail',
      ),
      isThreeLine: true,
      trailing: IconButton(
        tooltip: 'Adjust thermostat',
        onPressed: _busy || !panelFresh ? null : () => _climate(thermostat),
        icon: const Icon(Icons.tune),
      ),
      onTap: _busy || !panelFresh ? null : () => _climate(thermostat),
    );
  }

  Widget _garageTile(Map<String, dynamic> door, bool panelFresh) {
    final state = door['state']?.toString().toLowerCase() ?? 'unknown';
    final age = _ageSeconds(door['observed_at'] ?? door['at']);
    final confirmed = door['state_confirmed'] == true;
    final stateFresh =
        confirmed && age != null && age >= 0 && age <= _observationStaleSeconds;
    final controlFresh = stateFresh && age <= _garageActionFreshSeconds;
    final canOpen = controlFresh && state == 'closed';
    final canClose = controlFresh && state == 'open';
    final stateLabel = !confirmed
        ? (state == 'opening' || state == 'closing'
              ? 'Reported $state · device read pending'
              : state == 'open' || state == 'closed'
              ? 'Reported $state · device read pending'
              : 'State unknown · device read not confirmed')
        : age == null
        ? 'State unknown · observation time unavailable'
        : age < 0 || age > _observationStaleSeconds
        ? 'Last observed $state · stale read ${_ageLabel(age)}'
        : age > _garageActionFreshSeconds
        ? 'Last observed $state · too old for garage control'
        : 'Last observed $state · ${_ageLabel(age)}';
    return ListTile(
      leading: const Icon(Icons.garage_outlined),
      title: Text(door['name']?.toString() ?? 'Garage door'),
      subtitle: Text(
        '$stateLabel${panelFresh ? '' : ' · home panel status stale or unknown'}',
      ),
      trailing: Wrap(
        spacing: 4,
        children: [
          IconButton(
            tooltip: 'Open at garage door',
            onPressed: canOpen && panelFresh && !_busy
                ? () => _garage(door, true)
                : null,
            icon: const Icon(Icons.keyboard_arrow_up),
          ),
          IconButton(
            tooltip: 'Close at garage door',
            onPressed: canClose && panelFresh && !_busy
                ? () => _garage(door, false)
                : null,
            icon: const Icon(Icons.keyboard_arrow_down),
          ),
        ],
      ),
    );
  }

  Widget _sectionHeader(
    BuildContext context,
    String title, {
    String? subtitle,
  }) => Padding(
    padding: const EdgeInsets.fromLTRB(4, 4, 4, 6),
    child: Row(
      children: [
        Expanded(
          child: Text(title, style: Theme.of(context).textTheme.titleMedium),
        ),
        if (subtitle != null)
          Text(subtitle, style: Theme.of(context).textTheme.bodySmall),
      ],
    ),
  );

  Widget _emptyCard(String text) => Card(
    child: Padding(
      padding: const EdgeInsets.all(16),
      child: Row(
        children: [
          const Icon(Icons.info_outline),
          const SizedBox(width: 12),
          Expanded(child: Text(text)),
        ],
      ),
    ),
  );
}

class _StatusCard extends StatelessWidget {
  const _StatusCard({
    required this.connected,
    required this.fresh,
    required this.updatedAt,
    required this.trouble,
    required this.error,
    required this.areas,
    required this.zones,
  });
  final bool connected;
  final bool fresh;
  final Object? updatedAt;
  final List<dynamic> trouble;
  final String error;
  final List<dynamic> areas;
  final List<dynamic> zones;

  @override
  Widget build(BuildContext context) {
    final color = connected && fresh
        ? Colors.green
        : connected
        ? Colors.orange
        : Theme.of(context).colorScheme.error;
    final age = _ageSeconds(updatedAt);
    final updated = age == null
        ? 'Freshness unknown'
        : 'Updated ${_ageLabel(age)}';
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.circle, size: 11, color: color),
                const SizedBox(width: 8),
                Text(
                  !connected
                      ? 'Home service unavailable'
                      : fresh
                      ? 'Home service connected · status fresh'
                      : 'Home service connected · status stale',
                  style: Theme.of(context).textTheme.titleSmall,
                ),
              ],
            ),
            if (connected) ...[
              const SizedBox(height: 5),
              Text(updated, style: Theme.of(context).textTheme.bodySmall),
            ],
            if (trouble.isNotEmpty) ...[
              const SizedBox(height: 8),
              for (final item in trouble) Text('• $item'),
            ],
            if (error.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                error,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            ],
            if (areas.isNotEmpty || zones.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                'Areas: ${areas.isEmpty ? 'none reported' : areas.join(', ')}',
              ),
              Text(
                'Zones: ${zones.isEmpty ? 'none reported' : zones.join(', ')}',
              ),
            ],
          ],
        ),
      ),
    );
  }
}

class _ClimateDialog extends StatefulWidget {
  const _ClimateDialog({required this.name, required this.initial});
  final String name;
  final Map<String, dynamic> initial;

  @override
  State<_ClimateDialog> createState() => _ClimateDialogState();
}

class _ClimateDialogState extends State<_ClimateDialog> {
  late String _mode;
  late String _fan;
  late final TextEditingController _heat;
  late final TextEditingController _cool;
  late final String _unit;
  late final double _min;
  late final double _max;

  @override
  void initState() {
    super.initState();
    _mode = widget.initial['mode'] as String;
    _fan = widget.initial['fan'] as String;
    _unit = widget.initial['unit'] as String;
    _min = _unit == 'C' ? 13 : 55;
    _max = _unit == 'C' ? 29 : 85;
    _heat = TextEditingController(text: '${widget.initial['heat']}');
    _cool = TextEditingController(text: '${widget.initial['cool']}');
  }

  @override
  void dispose() {
    _heat.dispose();
    _cool.dispose();
    super.dispose();
  }

  void _submit() {
    final heat = double.tryParse(_heat.text);
    final cool = double.tryParse(_cool.text);
    if (heat == null ||
        cool == null ||
        heat < _min ||
        heat > _max ||
        cool < _min ||
        cool > _max ||
        heat >= cool) {
      return;
    }
    Navigator.pop(context, {
      'mode': _mode,
      'heat': heat,
      'cool': cool,
      'fan': _fan,
    });
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(widget.name),
    content: SingleChildScrollView(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          DropdownButtonFormField<String>(
            initialValue: _mode,
            decoration: const InputDecoration(labelText: 'Mode'),
            items: const [
              DropdownMenuItem(value: 'off', child: Text('Off')),
              DropdownMenuItem(value: 'heat', child: Text('Heat')),
              DropdownMenuItem(value: 'cool', child: Text('Cool')),
              DropdownMenuItem(value: 'auto', child: Text('Auto')),
            ],
            onChanged: (value) => setState(() => _mode = value ?? 'off'),
          ),
          const SizedBox(height: 8),
          TextField(
            controller: _heat,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
              labelText: 'Heat setpoint ($_min–$_max°$_unit)',
            ),
          ),
          TextField(
            controller: _cool,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
              labelText: 'Cool setpoint ($_min–$_max°$_unit)',
            ),
          ),
          DropdownButtonFormField<String>(
            initialValue: _fan,
            decoration: const InputDecoration(labelText: 'Fan'),
            items: const [
              DropdownMenuItem(value: 'auto', child: Text('Auto')),
              DropdownMenuItem(value: 'on', child: Text('On')),
            ],
            onChanged: (value) => setState(() => _fan = value ?? 'auto'),
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(onPressed: _submit, child: const Text('Apply')),
    ],
  );
}

Map<String, dynamic> _map(Object? value) =>
    value is Map ? Map<String, dynamic>.from(value) : <String, dynamic>{};
List<dynamic> _list(Object? value) => value is List ? value : const [];
bool? _boolOrNull(Object? value) => value is bool ? value : null;
double _number(Object? value, double fallback) => value is num
    ? value.toDouble()
    : double.tryParse(value?.toString() ?? '') ?? fallback;

const int _observationStaleSeconds = 600;
const int _garageActionFreshSeconds = 90;

int? _epochSeconds(Object? value) {
  if (value is num) {
    final raw = value.toInt();
    return raw > 1000000000000 ? raw ~/ 1000 : raw;
  }
  if (value is String) {
    final parsed = DateTime.tryParse(value);
    if (parsed != null) return parsed.millisecondsSinceEpoch ~/ 1000;
    final numeric = num.tryParse(value);
    if (numeric != null) return _epochSeconds(numeric);
  }
  return null;
}

int? _ageSeconds(Object? timestamp) {
  final epoch = _epochSeconds(timestamp);
  if (epoch == null) return null;
  return DateTime.now().millisecondsSinceEpoch ~/ 1000 - epoch;
}

String _ageLabel(int age) {
  if (age < 0) return 'clock mismatch';
  final duration = age < 60
      ? (age == 0 ? 'just now' : '${age}s ago')
      : age < 3600
      ? '${age ~/ 60}m ago'
      : '${age ~/ 3600}h ago';
  return age > _observationStaleSeconds ? '$duration · stale' : duration;
}

Map<String, int?> _climateFieldAges(Object? fields) {
  final observed = _map(fields);
  if (observed.isEmpty) return const {};
  return {
    for (final key in ['temp', 'mode', 'heat', 'cool', 'fan', 'activity'])
      key: observed.containsKey(key) ? _ageSeconds(observed[key]) : null,
  };
}
