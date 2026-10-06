import 'voice_logic.dart';

/// The narrow bridge between voice UX and Android platform actions.
///
/// Keeping this coordinator plugin-free makes confirmation and lifecycle rules
/// testable without a device. The platform layer supplies [invoke], while the
/// voice sheet supplies [ask] for a fresh spoken answer.
typedef PhoneInvoke =
    Future<Map<String, dynamic>> Function(
      String method,
      Map<String, dynamic> args,
    );

class PhoneActionResult {
  final String reply;
  final bool handedOff;
  const PhoneActionResult(this.reply, {this.handedOff = false});
}

class PhoneActions {
  final PhoneInvoke invoke;
  final Future<String> Function(String prompt) ask;
  final bool Function() isActive;
  final Duration askTimeout;

  const PhoneActions({
    required this.invoke,
    required this.ask,
    required this.isActive,
    this.askTimeout = const Duration(seconds: 45),
  });

  Future<PhoneActionResult> execute(LocalIntent intent) async {
    if (!isActive()) return const PhoneActionResult('Voice session ended.');
    if (intent is PlayMusic) return _music(intent);
    if (intent is DialNumber) return _callNumber(intent.number);
    if (intent is CallContact) return _callContact(intent.name);
    return PhoneActionResult(intent.confirmation);
  }

  Future<PhoneActionResult> _music(PlayMusic intent) async {
    if (!isActive()) return const PhoneActionResult('Voice session ended.');
    try {
      final result = await invoke('playMusic', {
        'query': intent.query,
        'package': 'com.google.android.apps.youtube.music',
      });
      final status = _status(result);
      if (status == 'playing') {
        final title = result['title'];
        final played = title is String && title.trim().isNotEmpty
            ? title.trim()
            : intent.query;
        return PhoneActionResult(
          'Playing $played on YouTube Music.',
          handedOff: true,
        );
      }
      if (status == 'unsupported') {
        return PhoneActionResult(
          _nativeMessage(
            result,
            'YouTube Music cannot start playback on this phone.',
          ),
        );
      }
      if (status == 'permission_denied' || status == 'permission_required') {
        return const PhoneActionResult(
          'I do not have permission to open YouTube Music.',
        );
      }
      if (status == 'requested') {
        return PhoneActionResult(
          'I requested ${intent.query}, but I could not confirm that it started.',
        );
      }
      return PhoneActionResult(
        'YouTube Music did not confirm that ${intent.query} started.',
      );
    } catch (_) {
      return PhoneActionResult(
        'I could not start ${intent.query} in YouTube Music.',
      );
    }
  }

  Future<PhoneActionResult> _callContact(String name) async {
    if (!isActive()) return const PhoneActionResult('Voice session ended.');
    Map<String, dynamic> result;
    try {
      result = await invoke('resolveContact', {'query': name});
    } catch (_) {
      return const PhoneActionResult('I could not look up that contact.');
    }
    if (!isActive()) return const PhoneActionResult('Voice session ended.');
    final contacts = _contacts(result);
    if (contacts.isEmpty) {
      final status = _status(result);
      if (status == 'permission_denied' || status == 'permission_required') {
        return const PhoneActionResult(
          'I do not have permission to read contacts.',
        );
      }
      return PhoneActionResult('I could not find $name.');
    }

    Map<String, dynamic>? chosen;
    if (contacts.length == 1) {
      chosen = contacts.first;
    } else {
      if (contacts.length > 3) {
        return PhoneActionResult(
          'I found more than three contacts named $name. Please say a more specific name.',
        );
      }
      final options = contacts;
      final spoken = <String>[];
      for (var i = 0; i < options.length; i++) {
        final c = options[i];
        spoken.add(
          '${i + 1}, ${_displayName(c)}, ${_label(c)}, last four ${_lastFour(c)}',
        );
      }
      final answer = await _askFresh(
        'I found multiple contacts: ${spoken.join('; ')}. Say a number.',
      );
      if (answer == null)
        return const PhoneActionResult('I did not choose a contact.');
      final index = _selectionIndex(answer, options.length);
      if (index == null || index < 1 || index > options.length) {
        return const PhoneActionResult('I did not choose a contact.');
      }
      chosen = options[index - 1];
    }
    final number = _number(chosen);
    if (number == null)
      return const PhoneActionResult('That contact has no callable number.');
    return _confirmAndCall(number, _displayName(chosen));
  }

  Future<PhoneActionResult> _callNumber(String number) =>
      _confirmAndCall(number, number);

  Future<PhoneActionResult> _confirmAndCall(String number, String name) async {
    final confirmed = await _confirm(
      'Call $name, number ${_spokenNumber(number)}? Say yes or no.',
    );
    if (!confirmed) return const PhoneActionResult('Okay, I will not call.');
    if (!isActive()) return const PhoneActionResult('Voice session ended.');
    Map<String, dynamic> result;
    try {
      result = await invoke('call', {'number': number});
    } catch (_) {
      return const PhoneActionResult('I could not start the call.');
    }
    if (_status(result) == 'permission_granted_retry') {
      final retry = await _confirm(
        'Permission is ready. Call $name, number ${_spokenNumber(number)}? Say yes or no.',
      );
      if (!retry || !isActive())
        return const PhoneActionResult('Okay, I will not call.');
      try {
        result = await invoke('call', {'number': number});
      } catch (_) {
        return const PhoneActionResult('I could not start the call.');
      }
    }
    final status = _status(result);
    if (status == 'called') {
      return PhoneActionResult('Calling $name.', handedOff: true);
    }
    return PhoneActionResult(
      _nativeMessage(result, 'I could not confirm that the call started.'),
    );
  }

  Future<bool> _confirm(String prompt) async {
    for (var attempt = 0; attempt < 2; attempt++) {
      final answer = await _askFresh(
        attempt == 0 ? prompt : 'Please say yes or no.',
      );
      if (answer == null) return false;
      final normalized = answer.trim().toLowerCase();
      if (RegExp(
        r'^(yes|yeah|yep|okay|ok|call|go ahead|do it)$',
      ).hasMatch(normalized))
        return true;
      if (RegExp(r'^(no|nope|nah|cancel|stop|don.t)$').hasMatch(normalized))
        return false;
    }
    return false;
  }

  Future<String?> _askFresh(String prompt) async {
    if (!isActive()) return null;
    try {
      final answer = await ask(prompt).timeout(askTimeout);
      return isActive() ? answer : null;
    } catch (_) {
      return null;
    }
  }

  static List<Map<String, dynamic>> _contacts(Map<String, dynamic> result) {
    final raw = result['contacts'];
    if (raw is! List) return const [];
    return raw
        .whereType<Map>()
        .map((e) => Map<String, dynamic>.from(e))
        .toList();
  }

  static String _status(Map<String, dynamic> result) =>
      '${result['status'] ?? ''}'.toLowerCase();

  static String _nativeMessage(Map<String, dynamic> result, String fallback) {
    final message = result['message'] ?? result['error'];
    return message is String && message.trim().isNotEmpty ? message : fallback;
  }

  static String _displayName(Map<String, dynamic> contact) =>
      '${contact['name'] ?? contact['displayName'] ?? 'that contact'}';

  static String _label(Map<String, dynamic> contact) =>
      '${contact['label'] ?? contact['type'] ?? 'number'}';

  static String _lastFour(Map<String, dynamic> contact) {
    final value = _number(contact) ?? '';
    return value.length <= 4 ? value : value.substring(value.length - 4);
  }

  static int? _selectionIndex(String answer, int length) {
    final normalized = answer.trim().toLowerCase();
    final words = <String, int>{
      'one': 1,
      'first': 1,
      'two': 2,
      'second': 2,
      'three': 3,
      'third': 3,
    };
    final value = int.tryParse(normalized) ?? words[normalized];
    return value != null && value >= 1 && value <= length ? value : null;
  }

  static String? _number(Map<String, dynamic> contact) {
    final value =
        contact['number'] ?? contact['phoneNumber'] ?? contact['phone'];
    return value == null ? null : '$value';
  }

  static String _spokenNumber(String number) => number.split('').join(' ');
}
