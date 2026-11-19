import 'dart:async';
import 'dart:math';

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api.dart';
import '../core/app_lock.dart';
import '../core/state.dart';
import '../core/storage.dart';
import '../core/terminal_text.dart';

/// Library → Terminal: live zsh sessions on the Mac.
///
/// Opening it asks for the phone's fingerprint / screen lock, whatever the app
/// lock setting. The Mac only accepts this phone once it is paired (the first
/// phone to pair wins and the owner gets a push); other devices are refused
/// even with the API token. Output streams from a byte offset, so a dropped
/// connection resumes exactly where it stopped.
class TerminalScreen extends ConsumerStatefulWidget {
  final bool requireUnlock;
  final Future<String> Function()? deviceKey; // tests inject a fixed key
  const TerminalScreen({super.key, this.requireUnlock = true, this.deviceKey});

  @override
  ConsumerState<TerminalScreen> createState() => _TerminalScreenState();
}

enum _Phase { locked, loading, disabled, unpaired, elsewhere, ready, error }

const _termBg = Color(0xFF0E1116);
const _termFg = Color(0xFFD7DCE2);
const _mono = TextStyle(fontFamily: 'monospace', fontSize: 12.5, height: 1.3, color: _termFg);

class _TerminalScreenState extends ConsumerState<TerminalScreen> {
  _Phase _phase = _Phase.loading;
  String? _message;
  String? _key;
  List<Map<String, dynamic>> _sessions = [];
  String? _sid;
  final Map<String, TerminalBuffer> _buffers = {};
  final Map<String, int> _offsets = {};
  final Set<String> _ended = {};
  StreamSubscription<Map<String, dynamic>>? _sub;
  Timer? _retry;
  int _retries = 0;
  bool _connected = false;
  final _input = TextEditingController();
  final _focus = FocusNode();
  final _scroll = ScrollController();
  bool _follow = true;
  final List<String> _history = [];
  int _historyPos = 0;
  // How the program in front reads the terminal, as the Mac reports it:
  // 'line' (commands), 'keys' (arrow-key menus, y/n keys, REPLs) or 'secret'
  // (a password prompt). [_forceKeys] is the manual override for the rare
  // program the detection gets wrong.
  String _mode = 'line';
  String? _program;
  bool _forceKeys = false;
  bool _ctrl = false; // Ctrl latch: the next typed letter becomes a control key
  bool _appCursor = false; // DECCKM: arrows as ESC O x instead of ESC [ x
  // Keys typed in quick succession go out as one request; every input waits for
  // the previous one so keystrokes can never arrive out of order.
  Future<void> _outbox = Future.value();
  String _pendingKeys = '';
  Timer? _keyFlush;
  static const _sentinel = '\u200B'; // lets Backspace on an "empty" field be seen
  final _keysInput = TextEditingController(text: _sentinel);
  final _secretInput = TextEditingController();
  bool _supportsPrivateInput = false;
  bool _privateEditing = false;
  bool _privateSending = false;
  String? _promptId;
  String? _pasteStatus;

  static final _codePrompt = RegExp(
    r'paste (?:the )?code here[^\r\n]*(?:>|:)\s*$',
    caseSensitive: false,
  );
  bool get _authPrompt =>
      _program?.toLowerCase().contains('claude') == true &&
      _codePrompt.hasMatch(_buffer.text);

  GajalaApi? get _api => ref.read(apiProvider);
  TerminalBuffer get _buffer => _buffers.putIfAbsent(_sid ?? '', TerminalBuffer.new);

  @override
  void initState() {
    super.initState();
    _scroll.addListener(() {
      if (!_scroll.hasClients) return;
      final pos = _scroll.position;
      _follow = pos.pixels >= pos.maxScrollExtent - 24;
    });
    _start();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _retry?.cancel();
    _keyFlush?.cancel();
    _keysInput.dispose();
    _secretInput.dispose();
    _input.dispose();
    _focus.dispose();
    _scroll.dispose();
    super.dispose();
  }

  Future<void> _start() async {
    if (widget.requireUnlock &&
        !await AppLock.authenticate('Open the Mac terminal')) {
      if (mounted) setState(() => _phase = _Phase.locked);
      return;
    }
    _key = await (widget.deviceKey ?? Storage.shellDeviceKey)();
    await _loadStatus();
  }

  String _detail(Object e) {
    if (e is DioException) {
      final data = e.response?.data;
      if (data is Map && data['detail'] != null) return data['detail'].toString();
    }
    return friendlyError(e);
  }

  Future<void> _loadStatus() async {
    final api = _api;
    if (api == null || _key == null) return;
    setState(() => _phase = _Phase.loading);
    try {
      final s = await api.shellStatus(_key!);
      if (!mounted) return;
      _supportsPrivateInput = s['private_input'] == true;
      if (s['enabled'] != true) {
        setState(() {
          _phase = _Phase.disabled;
          _message = 'The remote shell is turned off on the Mac '
              '(REMOTE_SHELL_ENABLED=0 in its .env).';
        });
      } else if (s['this_device'] == true) {
        setState(() => _phase = _Phase.ready);
        await _loadSessions();
      } else if (s['paired'] == true) {
        setState(() {
          _phase = _Phase.elsewhere;
          _message = 'The Mac\'s shell is paired with ${s['label']}. Unpair it there, '
              'or run scripts/remote-shell unbind on the Mac, then pair this phone.';
        });
      } else {
        setState(() => _phase = _Phase.unpaired);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _phase = _Phase.error;
          _message = _detail(e);
        });
      }
    }
  }

  Future<void> _pair() async {
    try {
      await _api!.shellPair(_key!, 'Gajala phone');
      await _loadStatus();
    } catch (e) {
      _snack(_detail(e));
      await _loadStatus();
    }
  }

  Future<void> _unpair() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Unpair this phone?'),
        content: const Text(
          'Open shells on the Mac are closed. The next phone to open Terminal '
          'will be paired instead.',
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Unpair')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await _sub?.cancel();
      await _api!.shellUnpair(_key!);
      setState(() {
        _sessions = [];
        _sid = null;
      });
      await _loadStatus();
    } catch (e) {
      _snack(_detail(e));
    }
  }

  Future<void> _loadSessions() async {
    try {
      final list = await _api!.shellSessions(_key!);
      if (!mounted) return;
      setState(() => _sessions = list);
      if (_sid == null && list.isNotEmpty) {
        final live = list.where((s) => s['closed'] != true).toList();
        _attach((live.isNotEmpty ? live.last : list.last)['id'].toString());
      }
    } catch (e) {
      _snack(_detail(e));
    }
  }

  void _attach(String id) {
    _flushKeys();
    _clearPrivate();
    _sub?.cancel();
    _retry?.cancel();
    setState(() {
      _sid = id;
      _connected = false;
      _follow = true;
    });
    final offset = _offsets[id] ?? 0;
    _sub = _api!.shellOutput(_key!, id, offset).listen(
      (frame) => _onFrame(id, frame),
      onError: (Object e) => _lost(id, e),
      onDone: () => _lost(id, null),
      cancelOnError: true,
    );
  }

  void _onFrame(String id, Map<String, dynamic> frame) {
    if (!mounted || id != _sid) return;
    final buffer = _buffers.putIfAbsent(id, TerminalBuffer.new);
    switch (frame['type']) {
      case 'hello':
        _retries = 0;
        setState(() {
          _connected = true;
          _mode = frame['mode']?.toString() ?? 'line';
          _program = frame['program']?.toString();
          _promptId = frame['prompt_id'] as String?;
        });
      case 'mode':
        _flushKeys();
        _clearPrivate();
        setState(() {
          _mode = frame['mode']?.toString() ?? 'line';
          _program = frame['program']?.toString();
          _promptId = frame['prompt_id'] as String?;
          _ctrl = false;
          if (_mode == 'line') _forceKeys = false; // back at a prompt: drop the override
        });
      case 'output':
        final data = frame['data']?.toString() ?? '';
        final wasAuthPrompt = _authPrompt;
        final on = data.lastIndexOf('\x1B[?1h'), off = data.lastIndexOf('\x1B[?1l');
        if (on != off) _appCursor = on > off;
        buffer.append(data);
        if (wasAuthPrompt && !_authPrompt) _clearPrivate();
        _offsets[id] = (frame['next'] as num).toInt();
        setState(() {});
        _scrollToEnd();
      case 'gap':
        buffer.append('\n[earlier output was dropped]\n');
      case 'exit':
        _clearPrivate();
        _offsets[id] = (frame['next'] as num?)?.toInt() ?? _offsets[id] ?? 0;
        buffer.append('\n[session ended${frame['code'] == null ? '' : ' (exit ${frame['code']})'}]\n');
        setState(() {
          _ended.add(id);
          _connected = false;
        });
        _scrollToEnd();
    }
  }

  /// The stream dropped (network, app paused). Resume from the last offset.
  void _lost(String id, Object? error) {
    if (!mounted || id != _sid || _ended.contains(id)) return;
    _clearPrivate();
    _keyFlush?.cancel();
    _pendingKeys = '';
    if (error is DioException && error.response?.statusCode == 404) {
      setState(() => _ended.add(id));
      return;
    }
    if (error is DioException && error.response?.statusCode == 403) {
      _loadStatus();
      return;
    }
    setState(() => _connected = false);
    final delay = Duration(seconds: min(10, 1 << min(_retries++, 3)));
    _retry = Timer(delay, () {
      if (mounted && _sid == id) _attach(id);
    });
  }

  void _scrollToEnd() {
    if (!_follow) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
    });
  }

  int _columns() {
    final width = MediaQuery.of(context).size.width - 24;
    return (width / 7.6).floor().clamp(40, 160);
  }

  Future<void> _newSession() async {
    final api = _api!;
    List<String> projects = [];
    String? current;
    try {
      final p = await api.projects();
      projects = [
        for (final item in (p['projects'] as List? ?? const []))
          if ((item['name']?.toString() ?? '').isNotEmpty) item['name'].toString(),
      ];
      current = p['current_name']?.toString();
    } catch (_) {/* home is still offered */}
    if (!mounted) return;
    var choice = current != null && projects.contains(current) ? current : '~';
    final picked = await showModalBottomSheet<String>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, setSheet) => SafeArea(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                DropdownButtonFormField<String>(
                  initialValue: choice,
                  isExpanded: true,
                  decoration: const InputDecoration(
                    labelText: 'Start in',
                    border: OutlineInputBorder(),
                  ),
                  items: [
                    const DropdownMenuItem(value: '~', child: Text('Home folder (~)')),
                    for (final name in projects)
                      DropdownMenuItem(value: name, child: Text(name, overflow: TextOverflow.ellipsis)),
                  ],
                  onChanged: (v) => setSheet(() => choice = v ?? '~'),
                ),
                const SizedBox(height: 12),
                FilledButton(
                  onPressed: () => Navigator.pop(context, choice),
                  child: const Text('Open shell'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
    if (picked == null) return;
    try {
      final created = await api.shellCreate(_key!, project: picked, cols: _columns());
      final id = created['id'].toString();
      await _loadSessions();
      _attach(id);
      _focus.requestFocus();
    } catch (e) {
      _snack(_detail(e));
    }
  }

  Future<void> _closeSession() async {
    final id = _sid;
    if (id == null) return;
    _flushKeys();
    _clearPrivate();
    try {
      await _sub?.cancel();
      await _api!.shellClose(_key!, id);
    } catch (_) {/* already gone */}
    setState(() {
      _sid = null;
      _buffers.remove(id);
      _offsets.remove(id);
    });
    await _loadSessions();
  }

  Future<void> _send(String data, {bool reportPaste = false}) {
    final id = _sid;
    final api = _api;
    final key = _key;
    if (id == null || api == null || key == null || !_connected ||
        _ended.contains(id) || data.isEmpty) {
      return _outbox;
    }
    _follow = true;
    return _outbox = _outbox.then((_) async {
      try {
        await api.shellInput(key, id, data);
        if (reportPaste && mounted && _sid == id) {
          setState(() => _pasteStatus = 'Paste sent to terminal');
        }
      } catch (e) {
        _snack(_detail(e));
      }
    });
  }

  String get _effectiveMode =>
      _mode == 'secret' ? 'secret' : (_forceKeys || _mode == 'keys' ? 'keys' : 'line');

  /// One key (or a typed burst) in keys mode. Sent ~30 ms later together with
  /// whatever else was typed meanwhile.
  void _typeKey(String seq) {
    if (!_connected || _privateSending) return;
    if (_pasteStatus != null) setState(() => _pasteStatus = null);
    if (_ctrl && seq.length == 1 && RegExp('[a-zA-Z@\\[\\]\\\\^_ ]').hasMatch(seq)) {
      seq = String.fromCharCode(seq.toUpperCase().codeUnitAt(0) & 0x1f);
      setState(() => _ctrl = false);
    }
    _pendingKeys += seq;
    _keyFlush?.cancel();
    _keyFlush = Timer(const Duration(milliseconds: 30), _flushKeys);
  }

  void _flushKeys() {
    _keyFlush?.cancel();
    final batch = _pendingKeys;
    _pendingKeys = '';
    if (batch.isNotEmpty) _send(batch);
  }

  String _arrow(String dir) => _appCursor ? '\x1BO$dir' : '\x1B[$dir';

  /// The keys-mode field never holds text: whatever lands in it is sent and the
  /// field resets to one invisible character, so Backspace is still noticed.
  void _onKeysTyped(String value) {
    if (!value.contains(_sentinel)) {
      _typeKey('\x7f');
    } else {
      final typed = value.replaceAll(_sentinel, '');
      if (typed.length > 1 && !_ctrl) {
        _flushKeys();
        _send(typed.replaceAll('\r\n', '\r').replaceAll('\n', '\r'),
            reportPaste: true);
      } else if (typed.isNotEmpty) {
        _typeKey(typed == '\n' ? '\r' : typed);
      }
    }
    _keysInput.value = const TextEditingValue(
      text: _sentinel,
      selection: TextSelection.collapsed(offset: 1),
    );
  }

  void _clearPrivate() {
    _secretInput.clear();
    _privateEditing = false;
    _pasteStatus = null;
  }

  Future<void> _pastePrivate() async {
    final id = _sid;
    final prompt = _promptId;
    final clipboard = await Clipboard.getData(Clipboard.kTextPlain);
    if (!mounted || id != _sid || prompt != _promptId || !_connected) return;
    final text = clipboard?.text?.replaceFirst(RegExp(r'(?:\r\n|\n|\r)$'), '');
    if (text == null || text.isEmpty || RegExp(r'[\x00-\x1f\x7f]').hasMatch(text)) {
      _snack('Copy a single line of text first.');
      return;
    }
    setState(() {
      _privateEditing = true;
      _secretInput.value = TextEditingValue(
        text: text,
        selection: TextSelection.collapsed(offset: text.length),
      );
    });
  }

  Future<void> _submitSecret() async {
    final text = _secretInput.text;
    final prompt = _promptId;
    final id = _sid;
    final key = _key;
    final api = _api;
    if (!_connected || _privateSending || id == null || key == null || api == null) return;
    if (!_supportsPrivateInput || prompt == null) {
      if (_mode != 'secret') return;
      _secretInput.clear();
      await _send('$text\n');
      return;
    }
    if (text.isEmpty || RegExp(r'[\x00-\x1f\x7f]').hasMatch(text)) return;
    _flushKeys();
    setState(() {
      _secretInput.clear();
      _privateSending = true;
    });
    final submission = _outbox.then((_) async {
      if (!mounted || !_connected || id != _sid || prompt != _promptId) return;
      try {
        await api.shellPrivateInput(key, id, prompt, text);
      } catch (_) {
        _snack('Private input could not be confirmed. Check the prompt before pasting again.');
      }
    });
    _outbox = submission;
    await submission;
    if (mounted) {
      setState(() {
        _privateSending = false;
        _privateEditing = false;
      });
      _focus.requestFocus();
    }
  }

  void _submit() {
    if (!_connected) return;
    final line = _input.text;
    _input.clear();
    if (line.trim().isNotEmpty && (_history.isEmpty || _history.last != line)) {
      _history.add(line);
    }
    _historyPos = _history.length;
    _send('$line\n');
    _focus.requestFocus();
  }

  void _recall(int step) {
    if (_history.isEmpty) return;
    _historyPos = (_historyPos + step).clamp(0, _history.length);
    final text = _historyPos < _history.length ? _history[_historyPos] : '';
    _input.value = TextEditingValue(
      text: text,
      selection: TextSelection.collapsed(offset: text.length),
    );
  }

  void _snack(String text) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(text)));
  }

  String _sessionLabel(Map<String, dynamic> s) {
    final cwd = s['cwd']?.toString() ?? '';
    final name = cwd.split('/').where((p) => p.isNotEmpty).lastOrNull ?? '~';
    final ended = s['closed'] == true || _ended.contains(s['id']) ? ' · ended' : '';
    return '$name · ${s['id'].toString().substring(0, 4)}$ended';
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Terminal'),
        actions: [
          if (_phase == _Phase.ready)
            PopupMenuButton<String>(
              onSelected: (v) {
                if (v == 'copy') {
                  Clipboard.setData(ClipboardData(text: _buffer.text));
                  _snack('Output copied');
                } else if (v == 'clear') {
                  setState(_buffer.clear);
                } else if (v == 'unpair') {
                  _unpair();
                }
              },
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'copy', child: Text('Copy output')),
                PopupMenuItem(value: 'clear', child: Text('Clear screen')),
                PopupMenuItem(value: 'unpair', child: Text('Unpair this phone')),
              ],
            ),
        ],
      ),
      body: switch (_phase) {
        _Phase.loading => const Center(child: CircularProgressIndicator()),
        _Phase.locked => _notice(
          Icons.lock_outline,
          'Unlock to use the Mac terminal.',
          action: FilledButton(onPressed: _start, child: const Text('Unlock')),
        ),
        _Phase.unpaired => _notice(
          Icons.phonelink_lock_outlined,
          'Pair this phone to run commands on the Mac. Only one phone can be '
              'paired; other devices are refused even with the app\'s token, and '
              'you\'ll get a notification when it pairs.',
          action: FilledButton(onPressed: _pair, child: const Text('Pair this phone')),
        ),
        _Phase.disabled || _Phase.elsewhere => _notice(Icons.block, _message ?? ''),
        _Phase.error => _notice(
          Icons.cloud_off_outlined,
          _message ?? 'Could not reach the Mac.',
          action: TextButton(onPressed: _loadStatus, child: const Text('Retry')),
        ),
        _Phase.ready => _terminal(),
      },
    );
  }

  Widget _notice(IconData icon, String text, {Widget? action}) => Center(
    child: Padding(
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, size: 40),
          const SizedBox(height: 12),
          Text(text, textAlign: TextAlign.center),
          if (action != null) ...[const SizedBox(height: 16), action],
        ],
      ),
    ),
  );

  Widget _terminal() {
    final ended = _sid != null && _ended.contains(_sid);
    final lines = _buffer.lines;
    return SafeArea(
      top: false,
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 4, 4, 4),
            child: Row(
              children: [
                Expanded(
                  child: _sessions.isEmpty
                      ? const Text('No open shells')
                      : DropdownButton<String>(
                          value: _sessions.any((s) => s['id'] == _sid) ? _sid : null,
                          isExpanded: true,
                          underline: const SizedBox.shrink(),
                          hint: const Text('Choose a shell'),
                          items: [
                            for (final s in _sessions)
                              DropdownMenuItem(
                                value: s['id'].toString(),
                                child: Text(_sessionLabel(s), overflow: TextOverflow.ellipsis),
                              ),
                          ],
                          onChanged: (id) {
                            if (id != null && id != _sid) _attach(id);
                          },
                        ),
                ),
                if (_sid != null)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 6),
                    child: Icon(
                      Icons.circle,
                      size: 10,
                      color: ended ? Colors.grey : (_connected ? Colors.green : Colors.orange),
                    ),
                  ),
                IconButton(
                  tooltip: 'New shell',
                  icon: const Icon(Icons.add),
                  onPressed: _newSession,
                ),
                if (_sid != null)
                  IconButton(
                    tooltip: 'Close shell',
                    icon: const Icon(Icons.close),
                    onPressed: _closeSession,
                  ),
              ],
            ),
          ),
          Expanded(
            child: Container(
              width: double.infinity,
              color: _termBg,
              child: _sid == null
                  ? Center(
                      child: TextButton.icon(
                        onPressed: _newSession,
                        icon: const Icon(Icons.terminal),
                        label: const Text('Open a shell'),
                      ),
                    )
                  : SelectionArea(
                      child: ListView.builder(
                        controller: _scroll,
                        padding: const EdgeInsets.all(10),
                        itemCount: lines.length,
                        itemBuilder: (_, i) => Text(lines[i], style: _mono, softWrap: true),
                      ),
                    ),
            ),
          ),
          if (_sid != null && !ended) ..._inputArea(),
        ],
      ),
    );
  }

  List<Widget> _inputArea() => switch ((_privateEditing || _authPrompt) ? 'secret' : _effectiveMode) {
    'keys' => _keysArea(),
    'secret' => _secretArea(),
    _ => _lineArea(),
  };

  Widget _chips(List<Widget> children) => SingleChildScrollView(
    scrollDirection: Axis.horizontal,
    padding: const EdgeInsets.symmetric(horizontal: 8),
    child: Row(
      children: [
        for (final c in children)
          Padding(padding: const EdgeInsets.only(right: 6), child: c),
      ],
    ),
  );

  List<Widget> _lineArea() => [
    _chips([
      for (final (label, data) in const [
        ('Ctrl-C', '\x03'),
        ('Ctrl-D', '\x04'),
        ('Tab', '\t'),
        ('Esc', '\x1b'),
        ('y ⏎', 'y\n'),
      ])
        ActionChip(label: Text(label), onPressed: () => _send(data)),
      ActionChip(
        avatar: const Icon(Icons.keyboard_outlined, size: 18),
        label: const Text('Keys'),
        tooltip: 'Send each key as you press it',
        onPressed: () => setState(() => _forceKeys = true),
      ),
      if (_supportsPrivateInput && _program != null)
        ActionChip(label: const Text('Paste privately'), onPressed: _pastePrivate),
      IconButton(
        tooltip: 'Previous command',
        icon: const Icon(Icons.keyboard_arrow_up),
        onPressed: () => _recall(-1),
      ),
      IconButton(
        tooltip: 'Next command',
        icon: const Icon(Icons.keyboard_arrow_down),
        onPressed: () => _recall(1),
      ),
    ]),
    Padding(
      padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: _input,
              focusNode: _focus,
              autocorrect: false,
              enableSuggestions: false,
              textCapitalization: TextCapitalization.none,
              keyboardType: TextInputType.visiblePassword, // no autocorrect/predictions
              style: const TextStyle(fontFamily: 'monospace'),
              decoration: const InputDecoration(
                hintText: 'Command',
                isDense: true,
                border: OutlineInputBorder(),
              ),
              onSubmitted: (_) => _submit(),
              textInputAction: TextInputAction.send,
            ),
          ),
          _enter(_submit),
        ],
      ),
    ),
  ];

  /// Raw-key programs: an arrow pad and a field that sends each key as typed.
  List<Widget> _keysArea() {
    Widget key(String label, String seq, {IconData? icon, String? tip}) => SizedBox(
      height: 40,
      child: OutlinedButton(
        style: OutlinedButton.styleFrom(
          padding: const EdgeInsets.symmetric(horizontal: 10),
          minimumSize: const Size(44, 40),
        ),
        onPressed: () => _typeKey(seq),
        child: icon != null
            ? Icon(icon, size: 20, semanticLabel: tip ?? label)
            : Text(label),
      ),
    );
    final who = _program ?? 'The program';
    return [
      Container(
        width: double.infinity,
        padding: const EdgeInsets.fromLTRB(12, 6, 4, 2),
        child: Row(
          children: [
            const Icon(Icons.keyboard_outlined, size: 16),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                _forceKeys && _mode != 'keys'
                    ? 'Keys mode (manual): each key goes straight to the shell'
                    : '$who is reading keys — they go straight through',
                style: const TextStyle(fontSize: 12.5),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            if (_forceKeys && _mode != 'keys')
              TextButton(
                onPressed: () => setState(() => _forceKeys = false),
                child: const Text('Back to line'),
              ),
          ],
        ),
      ),
      _chips([
        key('↑', _arrow('A'), icon: Icons.arrow_upward, tip: 'Up'),
        key('↓', _arrow('B'), icon: Icons.arrow_downward, tip: 'Down'),
        key('←', _arrow('D'), icon: Icons.arrow_back, tip: 'Left'),
        key('→', _arrow('C'), icon: Icons.arrow_forward, tip: 'Right'),
        if (_supportsPrivateInput && _program != null)
          ActionChip(label: const Text('Paste privately'), onPressed: _pastePrivate),
        key('Space', ' ', icon: Icons.space_bar, tip: 'Space'),
        key('⌫', '\x7f', icon: Icons.backspace_outlined, tip: 'Backspace'),
        key('Tab', '\t'),
        key('Esc', '\x1b'),
        FilterChip(
          label: const Text('Ctrl'),
          selected: _ctrl,
          tooltip: 'Make the next letter a control key',
          onSelected: (on) => setState(() => _ctrl = on),
        ),
        key('Ctrl-C', '\x03'),
      ]),
      Padding(
        padding: const EdgeInsets.fromLTRB(8, 4, 8, 8),
        child: Row(children: [Expanded(child: TextField(
          key: const ValueKey('keys-input'),
          controller: _keysInput,
          focusNode: _focus,
          autocorrect: false,
          enableSuggestions: false,
          textCapitalization: TextCapitalization.none,
          keyboardType: TextInputType.visiblePassword,
          style: const TextStyle(fontFamily: 'monospace'),
          decoration: InputDecoration(
            hintText: _ctrl ? 'Type a letter for Ctrl-…' : _pasteStatus ?? 'Type — each key is sent',
            isDense: true,
            border: const OutlineInputBorder(),
          ),
          onChanged: _onKeysTyped,
          onEditingComplete: _enterKeys,
          textInputAction: TextInputAction.go,
        )), _enter(_enterKeys)]),
      ),
    ];
  }

  void _enterKeys() {
    _typeKey('\r');
    _flushKeys();
  }

  Widget _enter(VoidCallback submit) => IconButton(
    key: const ValueKey('terminal-enter'),
    tooltip: 'Enter',
    icon: const Icon(Icons.keyboard_return),
    onPressed: _connected && !_privateSending ? submit : null,
  );

  /// Password and private code prompts use a local masked draft.
  List<Widget> _secretArea() => [
    _chips([
      ActionChip(label: const Text('Ctrl-C'), onPressed: () {
        setState(_clearPrivate);
        _send('\x03');
      }),
      if (_supportsPrivateInput)
        ActionChip(label: const Text('Paste privately'), onPressed: _pastePrivate),
      if (_secretInput.text.isNotEmpty)
        ActionChip(label: const Text('Clear'), onPressed: () => setState(_secretInput.clear)),
    ]),
    Padding(
      padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
      child: Row(
        children: [
          Expanded(
            child: TextField(
              key: const ValueKey('secret-input'),
              controller: _secretInput,
              focusNode: _focus,
              obscureText: true,
              autocorrect: false,
              enableSuggestions: false,
              onChanged: (_) => setState(() {}),
              decoration: InputDecoration(
                prefixIcon: const Icon(Icons.lock_outline, size: 18),
                labelText: _secretInput.text.isNotEmpty
                    ? 'Code ready · Press Enter'
                    : 'Private input',
                isDense: true,
                border: const OutlineInputBorder(),
              ),
              onSubmitted: (_) => _submitSecret(),
              textInputAction: TextInputAction.send,
            ),
          ),
          _enter(_submitSecret),
        ],
      ),
    ),
  ];
}
