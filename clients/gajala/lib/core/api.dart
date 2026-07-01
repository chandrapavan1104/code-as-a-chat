import 'package:dio/dio.dart';
import 'models.dart';

/// Turns Dio's verbose exceptions into a clear, human message.
String friendlyError(Object e) {
  if (e is DioException) {
    final code = e.response?.statusCode;
    if (code == 401) return 'Bad API token (401) — re-check the token';
    if (code != null) return 'Server error $code';
    switch (e.type) {
      case DioExceptionType.connectionTimeout:
      case DioExceptionType.receiveTimeout:
      case DioExceptionType.sendTimeout:
        return 'Timed out reaching the server';
      case DioExceptionType.connectionError:
        return "Can't reach the server — check the URL and that Tailscale is ON on your phone";
      case DioExceptionType.badCertificate:
        return 'TLS certificate error';
      default:
        return 'Network error: ${e.message ?? e.type.name}';
    }
  }
  return e.toString();
}

/// Talks to the Code-as-a-Chat server: the conversational /run path (Gajala
/// text) and the structured /api/* v2 endpoints (dashboard data).
class GajalaApi {
  final Dio _dio;
  final String token;

  GajalaApi(String baseUrl, this.token)
      : _dio = Dio(BaseOptions(
          baseUrl: baseUrl.replaceAll(RegExp(r'/+$'), ''),
          connectTimeout: const Duration(seconds: 15),
          receiveTimeout: const Duration(minutes: 10),
          headers: {'X-API-Token': token},
        ));

  // ── health / connection test ──────────────────────────────────────────────
  static Future<bool> ping(String baseUrl) async {
    try {
      final d = Dio();
      final r = await d.get('${baseUrl.replaceAll(RegExp(r'/+$'), '')}/health',
          options: Options(receiveTimeout: const Duration(seconds: 8)));
      return r.statusCode == 200;
    } catch (_) {
      return false;
    }
  }

  // ── skills (dashboard manifest) ─────────────────────────────────────────────
  Future<List<Skill>> skills() async {
    final r = await _dio.get('/skills');
    final list = (r.data['skills'] as List).map((e) => Skill.fromJson(e)).toList();
    return list.where((s) => s.name != 'shell' && s.name != 'memory').toList();
  }

  // ── chat (Gajala, conversational) ───────────────────────────────────────────
  Future<List<ChatMessage>> chatHistory(String sessionId, {int limit = 50}) async {
    final r = await _dio.get('/api/chat',
        queryParameters: {'session_id': sessionId, 'limit': limit});
    return (r.data['turns'] as List)
        .map((t) => ChatMessage(t['role'] == 'user' ? 'user' : 'bot', t['content']?.toString() ?? ''))
        .toList();
  }

  Future<String> run(String command, String prompt, String sessionId) async {
    final r = await _dio.post('/run', data: {
      'command': command,
      'prompt': prompt,
      'session_id': sessionId,
    });
    return r.data['result']?.toString() ?? '(no result)';
  }

  // ── structured v2 endpoints ─────────────────────────────────────────────────
  Future<SystemStats> system() async =>
      SystemStats.fromJson((await _dio.get('/api/system')).data);

  Future<List<Note>> notes({String status = 'open'}) async {
    final r = await _dio.get('/api/notes', queryParameters: {'status': status});
    return (r.data['notes'] as List).map((e) => Note.fromJson(e)).toList();
  }

  Future<Note> createNote({String kind = 'note', required String title,
      String body = '', String? project}) async {
    final r = await _dio.post('/api/notes',
        data: {'kind': kind, 'title': title, 'body': body, 'project': project});
    return Note.fromJson(r.data);
  }

  Future<void> setNoteStatus(int id, String status) async =>
      _dio.patch('/api/notes/$id', data: {'status': status});

  Future<void> deleteNote(int id) async => _dio.delete('/api/notes/$id');

  Future<List<Map<String, dynamic>>> reminders() async {
    final r = await _dio.get('/api/reminders');
    return List<Map<String, dynamic>>.from(r.data['reminders']);
  }

  Future<void> createReminder(String text, double dueAtEpoch) async =>
      _dio.post('/api/reminders', data: {'text': text, 'due_at': dueAtEpoch});

  Future<void> deleteReminder(int id) async => _dio.delete('/api/reminders/$id');

  Future<Map<String, dynamic>> diary({String? category}) async {
    final r = await _dio.get('/api/diary',
        queryParameters: category == null ? null : {'category': category});
    return Map<String, dynamic>.from(r.data);
  }

  Future<void> registerDevice(String fcmToken, {String? label}) async =>
      _dio.post('/api/devices', data: {'fcm_token': fcmToken, 'label': label});

  Future<Map<String, dynamic>> projects() async =>
      Map<String, dynamic>.from((await _dio.get('/api/projects')).data);

  Future<String> switchProject(String name) async {
    final r = await _dio.post('/api/projects/switch', data: {'name': name});
    return r.data['current_name']?.toString() ?? name;
  }

  Future<List<Map<String, dynamic>>> usage() async {
    final r = await _dio.get('/api/usage');
    return List<Map<String, dynamic>>.from(r.data['providers']);
  }
}
