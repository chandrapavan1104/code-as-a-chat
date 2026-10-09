import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:timezone/data/latest.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;
import '../core/api.dart';
import '../core/reminders_api.dart';
import '../core/state.dart';
import '../core/theme.dart';
import '../core/models.dart';

const _zones = <String, String>{
  'UTC': 'UTC',
  'America/Los_Angeles': 'Pacific',
  'America/Denver': 'Mountain',
  'America/Chicago': 'Central',
  'America/New_York': 'Eastern',
  'Europe/London': 'London',
  'Europe/Paris': 'Central Europe',
  'Asia/Kolkata': 'India',
  'Asia/Tokyo': 'Tokyo',
  'Australia/Sydney': 'Sydney',
};

class RemindersScreen extends ConsumerWidget {
  const RemindersScreen({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    tzdata.initializeTimeZones();
    final reminders = ref.watch(remindersProvider);
    return Scaffold(
      appBar: AppBar(title: const Text('Reminders')),
      body: reminders.when(
        data: (items) {
          final sorted = [
            ...items,
          ]..sort((a, b) => (a['due_at'] as num).compareTo(b['due_at'] as num));
          if (sorted.isEmpty) {
            return RefreshIndicator(
              onRefresh: () async => ref.invalidate(remindersProvider),
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                children: [
                  SizedBox(
                    height: 260,
                    child: Center(
                      child: Text(
                        'No reminders ra ⏰',
                        style: TextStyle(color: context.pal.textDim),
                      ),
                    ),
                  ),
                ],
              ),
            );
          }
          return RefreshIndicator(
            onRefresh: () async => ref.invalidate(remindersProvider),
            child: ListView.builder(
              padding: const EdgeInsets.fromLTRB(12, 12, 12, 96),
              itemCount: sorted.length,
              itemBuilder: (_, i) => _ReminderTile(sorted[i], ref),
            ),
          );
        },
        loading: () => const Center(child: CircularProgressIndicator()),
        error: (e, _) => Center(
          child: Text(
            friendlyError(e),
            style: const TextStyle(color: GajalaColors.danger),
          ),
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        backgroundColor: GajalaColors.accent,
        onPressed: () => _edit(context, ref),
        icon: const Icon(Icons.add, color: Colors.white),
        label: const Text('Reminder', style: TextStyle(color: Colors.white)),
      ),
    );
  }

  static Future<void> _edit(
    BuildContext context,
    WidgetRef ref, [
    Map<String, dynamic>? existing,
  ]) async {
    final saved = await showDialog<bool>(
      context: context,
      builder: (_) => _ReminderEditor(reminder: existing),
    );
    if (saved == true && context.mounted) ref.invalidate(remindersProvider);
  }
}

class _ReminderTile extends ConsumerWidget {
  const _ReminderTile(this.reminder, this.ref);
  final Map<String, dynamic> reminder;
  final WidgetRef ref;

  @override
  Widget build(BuildContext context, WidgetRef _) {
    final recurrence = reminder['recurrence'] == 'daily' ? ' · Daily' : '';
    final timezone = reminder['timezone']?.toString() ?? 'UTC';
    final linked = reminder['until_note_id'] == null
        ? ''
        : ' · Until linked note is done';
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: ListTile(
        leading: const Icon(Icons.alarm, color: GajalaColors.accent),
        title: Text(reminder['text']?.toString() ?? ''),
        subtitle: Text(
          '${_format(reminder)}$recurrence$linked\n$timezone',
          style: TextStyle(color: context.pal.textDim, fontSize: 12),
          maxLines: 2,
        ),
        isThreeLine: true,
        onTap: () => RemindersScreen._edit(context, ref, reminder),
        trailing: PopupMenuButton<String>(
          onSelected: (action) async {
            if (action == 'edit') {
              await RemindersScreen._edit(context, ref, reminder);
            } else if (action == 'cancel') {
              final ok = await showDialog<bool>(
                context: context,
                builder: (c) => AlertDialog(
                  title: const Text('Cancel reminder?'),
                  content: Text(
                    '“${reminder['text']}” will stop repeating and leave your active list.',
                  ),
                  actions: [
                    TextButton(
                      onPressed: () => Navigator.pop(c, false),
                      child: const Text('Keep'),
                    ),
                    FilledButton(
                      onPressed: () => Navigator.pop(c, true),
                      child: const Text('Cancel reminder'),
                    ),
                  ],
                ),
              );
              if (ok == true && context.mounted) {
                await _cancel(context, ref, reminder['id'] as int);
              }
            }
          },
          itemBuilder: (_) => const [
            PopupMenuItem(value: 'edit', child: Text('Edit')),
            PopupMenuItem(value: 'cancel', child: Text('Cancel reminder')),
          ],
        ),
      ),
    );
  }

  static String _format(Map<String, dynamic> reminder) {
    final epoch = (reminder['due_at'] as num).toDouble();
    final zone = _zone(reminder['timezone']?.toString() ?? 'UTC');
    final date = tz.TZDateTime.fromMillisecondsSinceEpoch(
      zone,
      (epoch * 1000).round(),
    );
    final when =
        '${date.day}/${date.month} ${date.hour.toString().padLeft(2, '0')}:${date.minute.toString().padLeft(2, '0')}';
    final diff = DateTime.fromMillisecondsSinceEpoch(
      (epoch * 1000).round(),
    ).difference(DateTime.now());
    if (diff.isNegative) return '$when (overdue)';
    if (diff.inHours < 24) {
      return '$when (in ${diff.inHours}h ${diff.inMinutes % 60}m)';
    }
    return '$when (in ${diff.inDays}d)';
  }
}

class _ReminderEditor extends ConsumerStatefulWidget {
  const _ReminderEditor({this.reminder});
  final Map<String, dynamic>? reminder;

  @override
  ConsumerState<_ReminderEditor> createState() => _ReminderEditorState();
}

class _ReminderEditorState extends ConsumerState<_ReminderEditor> {
  final _text = TextEditingController();
  DateTime? _date;
  TimeOfDay? _time;
  String _recurrence = 'none';
  String _timezone = 'UTC';
  bool _timezoneLoaded = false;
  bool _timezoneLoadFailed = false;
  bool _timezoneManuallySelected = false;
  int? _noteId;
  List<Note> _notes = [];
  bool _saving = false;

  bool get _scheduleReady =>
      widget.reminder != null || _timezoneLoaded || _timezoneManuallySelected;

  @override
  void initState() {
    super.initState();
    tzdata.initializeTimeZones();
    final row = widget.reminder;
    _text.text = row?['text']?.toString() ?? '';
    _timezone = row?['timezone']?.toString() ?? 'UTC';
    _timezoneLoaded = row != null;
    _recurrence = row?['recurrence']?.toString() == 'daily' ? 'daily' : 'none';
    _noteId = row?['until_note_id'] as int?;
    final zone = _zone(_timezone);
    _setInitialTime(zone, row);
    _loadNotes();
    if (row == null) _loadServerTimezone();
  }

  void _setInitialTime(tz.Location zone, Map<String, dynamic>? row) {
    final initial = row == null
        ? tz.TZDateTime.now(zone).add(const Duration(hours: 1))
        : tz.TZDateTime.fromMillisecondsSinceEpoch(
            zone,
            ((row['due_at'] as num).toDouble() * 1000).round(),
          );
    _date = DateTime(initial.year, initial.month, initial.day);
    _time = TimeOfDay(hour: initial.hour, minute: initial.minute);
  }

  Future<void> _loadServerTimezone() async {
    final config = ref.read(configProvider);
    if (config == null) {
      if (mounted) setState(() => _timezoneLoadFailed = true);
      return;
    }
    try {
      final timezone = await RemindersApi(
        config.url,
        config.token,
      ).serverTimezone();
      tz.getLocation(timezone);
      if (!mounted) return;
      setState(() {
        _timezoneLoaded = true;
        _timezoneLoadFailed = false;
        if (!_timezoneManuallySelected) {
          _timezone = timezone;
          _setInitialTime(_zone(timezone), null);
        }
      });
    } catch (_) {
      if (mounted) setState(() => _timezoneLoadFailed = true);
    }
  }

  Future<void> _loadNotes() async {
    try {
      final notes = await ref.read(apiProvider)?.notes(status: 'open') ?? [];
      if (mounted) setState(() => _notes = notes);
    } catch (_) {}
  }

  tz.Location _zone(String name) {
    try {
      return tz.getLocation(name);
    } catch (_) {
      return tz.UTC;
    }
  }

  Future<void> _pickDate() async {
    final now = tz.TZDateTime.now(_zone(_timezone));
    final chosen = await showDatePicker(
      context: context,
      initialDate: _date ?? now,
      firstDate: DateTime(now.year, now.month, now.day),
      lastDate: DateTime(now.year + 5, 12, 31),
    );
    if (chosen != null) setState(() => _date = chosen);
  }

  Future<void> _pickTime() async {
    final chosen = await showTimePicker(
      context: context,
      initialTime: _time ?? TimeOfDay.now(),
    );
    if (chosen != null) setState(() => _time = chosen);
  }

  double? _dueEpoch() {
    if (_date == null || _time == null) return null;
    final location = _zone(_timezone);
    return tz.TZDateTime(
          location,
          _date!.year,
          _date!.month,
          _date!.day,
          _time!.hour,
          _time!.minute,
        ).millisecondsSinceEpoch /
        1000;
  }

  String get _dateLabel => _date == null
      ? 'Choose date'
      : '${_date!.day}/${_date!.month}/${_date!.year}';
  String get _timeLabel => _time?.format(context) ?? 'Choose time';

  Future<void> _save() async {
    final text = _text.text.trim();
    final dueAt = _dueEpoch();
    if (text.isEmpty) return _error('Add reminder text first.');
    if (dueAt == null ||
        dueAt <= DateTime.now().millisecondsSinceEpoch / 1000) {
      return _error('Choose a future date and time.');
    }
    if (!_timezoneLoaded && !_timezoneManuallySelected) {
      return _error(
        'Loading the server time zone. Select a time zone if it cannot be loaded.',
      );
    }
    final config = ref.read(configProvider);
    if (config == null) return _error('Connect to your Mac to save reminders.');
    setState(() => _saving = true);
    try {
      final api = RemindersApi(config.url, config.token);
      if (widget.reminder == null) {
        await api.create(
          text: text,
          dueAt: dueAt,
          recurrence: _recurrence,
          timezone: _timezone,
          untilNoteId: _noteId,
        );
      } else {
        await api.update(widget.reminder!['id'] as int, {
          'text': text,
          'due_at': dueAt,
          'recurrence': _recurrence,
          'timezone': _timezone,
          'until_note_id': _noteId,
        });
      }
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) setState(() => _saving = false);
      if (mounted) _error(friendlyError(e));
    }
  }

  void _error(String message) {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), backgroundColor: GajalaColors.danger),
    );
  }

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final zoneNames = {
      ..._zones.keys,
      if (!_zones.containsKey(_timezone)) _timezone,
    }.toList();
    return AlertDialog(
      title: Text(widget.reminder == null ? 'New reminder' : 'Edit reminder'),
      content: SizedBox(
        width: 420,
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _text,
                autofocus: true,
                maxLength: 500,
                decoration: const InputDecoration(
                  labelText: 'Reminder',
                  hintText: 'What should Gajala remind you about?',
                ),
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: _scheduleReady ? _pickDate : null,
                      icon: const Icon(Icons.calendar_month),
                      label: Text(_dateLabel),
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: OutlinedButton.icon(
                      onPressed: _scheduleReady ? _pickTime : null,
                      icon: const Icon(Icons.schedule),
                      label: Text(_timeLabel),
                    ),
                  ),
                ],
              ),
              Wrap(
                spacing: 6,
                children: [
                  for (final preset in [
                    ("Today", 0),
                    ("Tomorrow", 1),
                    ("Next week", 7),
                  ])
                    ActionChip(
                      label: Text(preset.$1),
                      onPressed: _scheduleReady
                          ? () {
                              final now = tz.TZDateTime.now(_zone(_timezone));
                              setState(
                                () => _date = DateTime(
                                  now.year,
                                  now.month,
                                  now.day + preset.$2,
                                ),
                              );
                            }
                          : null,
                    ),
                ],
              ),
              Wrap(
                spacing: 6,
                children: [
                  for (final preset in [
                    ("9 AM", 9),
                    ("Noon", 12),
                    ("6 PM", 18),
                  ])
                    ActionChip(
                      label: Text(preset.$1),
                      onPressed: _scheduleReady
                          ? () => setState(
                              () =>
                                  _time = TimeOfDay(hour: preset.$2, minute: 0),
                            )
                          : null,
                    ),
                ],
              ),
              const SizedBox(height: 8),
              DropdownButtonFormField<String>(
                initialValue: _recurrence,
                decoration: const InputDecoration(labelText: 'Repeat'),
                items: const [
                  DropdownMenuItem(
                    value: 'none',
                    child: Text('Does not repeat'),
                  ),
                  DropdownMenuItem(value: 'daily', child: Text('Every day')),
                ],
                onChanged: (value) =>
                    setState(() => _recurrence = value ?? 'none'),
              ),
              DropdownButtonFormField<String>(
                key: ValueKey('timezone-$_timezone'),
                initialValue: _timezone,
                decoration: InputDecoration(
                  labelText: 'Time zone',
                  helperText: _timezoneLoadFailed && !_timezoneManuallySelected
                      ? 'Could not load the server zone. Choose one to continue.'
                      : !_timezoneLoaded && !_timezoneManuallySelected
                      ? 'Loading the server time zone…'
                      : null,
                ),
                items: [
                  for (final zone in zoneNames)
                    DropdownMenuItem(
                      value: zone,
                      child: Text('${_zones[zone] ?? zone} · $zone'),
                    ),
                ],
                onChanged: (value) {
                  if (value == null) return;
                  final wasReady = _scheduleReady;
                  setState(() {
                    _timezone = value;
                    _timezoneManuallySelected = true;
                    if (!wasReady && widget.reminder == null) {
                      _setInitialTime(_zone(value), null);
                    }
                  });
                },
              ),
              DropdownButtonFormField<int>(
                initialValue: _noteId ?? 0,
                decoration: const InputDecoration(
                  labelText: 'Stop when a note is done (optional)',
                ),
                items: [
                  const DropdownMenuItem(
                    value: 0,
                    child: Text('No linked note'),
                  ),
                  for (final note in _notes)
                    DropdownMenuItem(
                      value: note.id,
                      child: Text(
                        '${note.title} · #${note.id}',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  if (_noteId != null &&
                      !_notes.any((note) => note.id == _noteId))
                    DropdownMenuItem(
                      value: _noteId!,
                      child: Text('Linked note #$_noteId'),
                    ),
                ],
                onChanged: (value) => setState(
                  () => _noteId = value == null || value == 0 ? null : value,
                ),
              ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: _saving ? null : () => Navigator.pop(context, false),
          child: const Text('Close'),
        ),
        FilledButton(
          onPressed: _saving ? null : _save,
          child: _saving
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(strokeWidth: 2),
                )
              : Text(widget.reminder == null ? 'Create' : 'Save'),
        ),
      ],
    );
  }
}

Future<void> _cancel(BuildContext context, WidgetRef ref, int id) async {
  final config = ref.read(configProvider);
  if (config == null) return;
  try {
    await RemindersApi(config.url, config.token).cancel(id);
    ref.invalidate(remindersProvider);
  } catch (e) {
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(friendlyError(e)),
          backgroundColor: GajalaColors.danger,
        ),
      );
    }
  }
}

tz.Location _zone(String name) {
  try {
    return tz.getLocation(name);
  } catch (_) {
    return tz.UTC;
  }
}
