// Plugin-free voice logic: which utterances the phone handles itself, and how
// a chat reply is turned into something worth hearing.
//
// Phone-native requests (alarms, timers, calls, maps, web search) run as Android
// intents on-device — they work without the Mac and are what the stock assistant
// would do anyway. Anything that mentions Gajala's own world (notes, queue,
// projects, the Mac…) always goes to the server, even if it also sounds native:
// "remind me at 7" belongs in Gajala's synced reminders, not the clock app.

sealed class LocalIntent {
  const LocalIntent();

  /// What Gajala says back after handing the request to Android.
  String get confirmation;
}

class SetAlarm extends LocalIntent {
  final int hour; // 0-23
  final int minute;
  const SetAlarm(this.hour, this.minute);
  @override
  String get confirmation => 'Alarm set for ${spokenTime(hour, minute)}.';
  @override
  bool operator ==(Object other) => other is SetAlarm && other.hour == hour && other.minute == minute;
  @override
  int get hashCode => Object.hash(hour, minute);
  @override
  String toString() => 'SetAlarm($hour:$minute)';
}

class SetTimer extends LocalIntent {
  final int seconds;
  const SetTimer(this.seconds);
  @override
  String get confirmation => 'Timer started for ${spokenDuration(seconds)}.';
  @override
  bool operator ==(Object other) => other is SetTimer && other.seconds == seconds;
  @override
  int get hashCode => seconds.hashCode;
  @override
  String toString() => 'SetTimer($seconds)';
}

/// A number to dial. Named contacts go to [AskGoogle], which resolves them.
class DialNumber extends LocalIntent {
  final String number;
  const DialNumber(this.number);
  @override
  String get confirmation => 'Opening the dialer.';
  @override
  bool operator ==(Object other) => other is DialNumber && other.number == number;
  @override
  int get hashCode => number.hashCode;
  @override
  String toString() => 'DialNumber($number)';
}

class Navigate extends LocalIntent {
  final String destination;
  const Navigate(this.destination);
  @override
  String get confirmation => 'Getting directions to $destination.';
  @override
  bool operator ==(Object other) => other is Navigate && other.destination == destination;
  @override
  int get hashCode => destination.hashCode;
  @override
  String toString() => 'Navigate($destination)';
}

class WebSearch extends LocalIntent {
  final String query;
  const WebSearch(this.query);
  @override
  String get confirmation => 'Searching the web for $query.';
  @override
  bool operator ==(Object other) => other is WebSearch && other.query == query;
  @override
  int get hashCode => query.hashCode;
  @override
  String toString() => 'WebSearch($query)';
}

/// Hand the whole utterance to Google (e.g. "call mom", "ask Google …").
class AskGoogle extends LocalIntent {
  final String query;
  const AskGoogle(this.query);
  @override
  String get confirmation => 'Handing that to Google.';
  @override
  bool operator ==(Object other) => other is AskGoogle && other.query == query;
  @override
  int get hashCode => query.hashCode;
  @override
  String toString() => 'AskGoogle($query)';
}

final _gajalaDomain = RegExp(
  r'\b(notes?|diary|remind(er|ers)?|queue|deploy\w*|projects?|repo\w*|code|'
  r'coding|codex|claude|gemini|qwen|mac|macbook|build|commit|branch|tasks?|'
  r'server|usage|brain ?dump|gajala|night shift|ports?|sessions?)\b',
);

/// Returns the on-device action for [utterance], or null when the request
/// should go to Gajala on the Mac. [now] is injectable for tests.
LocalIntent? parseLocalIntent(String utterance, {DateTime? now}) {
  final t = _normalize(utterance);
  if (t.isEmpty) return null;

  final google = RegExp(r'^(?:ask|hey|ok|okay) google[, ]+(.+)$').firstMatch(t);
  if (google != null) return AskGoogle(google.group(1)!.trim());

  if (_gajalaDomain.hasMatch(t)) return null;

  final timer = _parseTimer(t);
  if (timer != null) return timer;

  final alarm = _parseAlarm(t, now ?? DateTime.now());
  if (alarm != null) return alarm;

  final call = RegExp(r'^(?:call|dial|phone|ring)\s+(.+)$').firstMatch(t);
  if (call != null) {
    final target = call.group(1)!.trim();
    final digits = target.replaceAll(RegExp(r'[\s\-().]'), '');
    if (RegExp(r'^\+?\d{3,15}$').hasMatch(digits)) return DialNumber(digits);
    return AskGoogle('call $target');
  }

  final nav = RegExp(
    r'^(?:navigate to|directions to|get directions to|take me to|'
    r'how do i get to|drive to|show me the way to)\s+(.+)$',
  ).firstMatch(t);
  if (nav != null) return Navigate(nav.group(1)!.trim());

  final search = RegExp(
    r'^(?:search (?:the web |google )?for|google|search the web for|web search)\s+(.+)$',
  ).firstMatch(t);
  if (search != null) return WebSearch(search.group(1)!.trim());

  return null;
}

String _normalize(String s) => s
    .toLowerCase()
    .replaceAll(RegExp(r'\ba\.m\.?'), 'am')
    .replaceAll(RegExp(r'\bp\.m\.?'), 'pm')
    .replaceAll(RegExp(r'[?!]+$'), '')
    .replaceAll(RegExp(r'\s+'), ' ')
    .replaceAll(RegExp(r'^(please |can you |could you |hey gajala,? )+'), '')
    .trim();

const _unitSeconds = {
  'second': 1, 'seconds': 1, 'sec': 1, 'secs': 1,
  'minute': 60, 'minutes': 60, 'min': 60, 'mins': 60,
  'hour': 3600, 'hours': 3600, 'hr': 3600, 'hrs': 3600,
};

SetTimer? _parseTimer(String t) {
  if (!t.contains('timer')) return null;
  if (RegExp(r'\bhalf an hour\b').hasMatch(t)) return const SetTimer(1800);
  var total = 0;
  final parts = RegExp(r'\b(\d+|an?|one)\s*-?\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b')
      .allMatches(t);
  for (final m in parts) {
    final n = int.tryParse(m.group(1)!) ?? 1;
    total += n * _unitSeconds[m.group(2)!]!;
  }
  return total > 0 ? SetTimer(total) : null;
}

SetAlarm? _parseAlarm(String t, DateTime now) {
  final isAlarm = RegExp(r'\b(alarm|wake me( up)?)\b').hasMatch(t);
  if (!isAlarm) return null;
  if (RegExp(r'\bnoon\b').hasMatch(t)) return const SetAlarm(12, 0);
  if (RegExp(r'\bmidnight\b').hasMatch(t)) return const SetAlarm(0, 0);
  final m = RegExp(r'\b(?:at|for)\s+(\d{1,2})(?:[:. ](\d{2}))?\s*(am|pm)?\b').firstMatch(t);
  if (m == null) return null;
  var hour = int.parse(m.group(1)!);
  final minute = int.tryParse(m.group(2) ?? '') ?? 0;
  final meridiem = m.group(3);
  if (hour > 23 || minute > 59) return null;
  if (meridiem != null) {
    if (hour < 1 || hour > 12) return null;
    hour = hour % 12 + (meridiem == 'pm' ? 12 : 0);
    return SetAlarm(hour, minute);
  }
  if (hour >= 13 || hour == 0) return SetAlarm(hour, minute);
  // "alarm for 7" with no am/pm: the next 7 o'clock coming up.
  final candidates = [hour % 12, hour % 12 + 12];
  int minutesUntil(int h) {
    final d = (h * 60 + minute) - (now.hour * 60 + now.minute);
    return d <= 0 ? d + 24 * 60 : d;
  }
  candidates.sort((a, b) => minutesUntil(a).compareTo(minutesUntil(b)));
  return SetAlarm(candidates.first, minute);
}

String spokenTime(int hour, int minute) {
  final h12 = hour % 12 == 0 ? 12 : hour % 12;
  final mm = minute == 0 ? '' : ':${minute.toString().padLeft(2, '0')}';
  return '$h12$mm ${hour < 12 ? 'AM' : 'PM'}';
}

String spokenDuration(int seconds) {
  final h = seconds ~/ 3600, m = (seconds % 3600) ~/ 60, s = seconds % 60;
  String unit(int n, String w) => '$n $w${n == 1 ? '' : 's'}';
  return [
    if (h > 0) unit(h, 'hour'),
    if (m > 0) unit(m, 'minute'),
    if (s > 0) unit(s, 'second'),
  ].join(' ');
}

/// Turn a chat reply into speech: no code, markdown, URLs, markers or emoji,
/// and short enough to listen to. Long replies point at the chat for the rest.
String speakable(String reply, {int maxChars = 600}) {
  var s = reply
      .replaceAll(RegExp(r'```[\s\S]*?(```|$)'), ' I put the code in the chat. ')
      .replaceAll(RegExp(r'\[\[[^\]]*\]\]'), ' ')
      .replaceAll(RegExp(r'\[image:[^\]]*\]'), ' ')
      .replaceAllMapped(RegExp(r'\[([^\]]+)\]\([^)]+\)'), (m) => m.group(1)!)
      .replaceAll(RegExp(r'https?://\S+'), 'a link')
      .replaceAllMapped(RegExp(r'`([^`]*)`'), (m) => m.group(1)!)
      .replaceAll(RegExp(r'^\s{0,3}#{1,6}\s*', multiLine: true), '')
      .replaceAll(RegExp(r'^\s*[-*•]\s+', multiLine: true), '')
      .replaceAll(RegExp(r'[*_~]{1,3}'), '')
      // The lint parses without the unicode flag; the pattern is valid with it.
      // ignore: valid_regexps
      .replaceAll(RegExp(r'\p{Extended_Pictographic}|\u{FE0F}|\u{200D}', unicode: true), '')
      .replaceAll(RegExp(r'\s*\n+\s*'), '. ')
      .replaceAll(RegExp(r'\.(\s*\.)+'), '.')
      .replaceAll(RegExp(r'\s+'), ' ')
      .trim();
  if (s.startsWith('. ')) s = s.substring(2);
  if (s.length <= maxChars) return s;
  final cut = s.substring(0, maxChars);
  final end = cut.lastIndexOf(RegExp(r'[.!?]\s'));
  final head = end > maxChars ~/ 3 ? cut.substring(0, end + 1) : '$cut…';
  return '$head The rest is in the chat.';
}
