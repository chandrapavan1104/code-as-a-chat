import 'package:dio/dio.dart';

/// Reminder controls live here so the existing shared API client stays stable.
class RemindersApi {
  RemindersApi(String baseUrl, String token)
    : _dio = Dio(
        BaseOptions(
          baseUrl: baseUrl.replaceAll(RegExp(r'/+$'), ''),
          connectTimeout: const Duration(seconds: 15),
          receiveTimeout: const Duration(seconds: 30),
          headers: {'X-API-Token': token},
        ),
      );

  final Dio _dio;

  Future<String> serverTimezone() async {
    final response = await _dio.get('/api/library/options');
    final body = Map<String, dynamic>.from(response.data);
    final timezone = body['timezone']?.toString().trim() ?? '';
    if (timezone.isEmpty) throw StateError('Server did not return a timezone');
    return timezone;
  }

  Future<Map<String, dynamic>> create({
    required String text,
    required double dueAt,
    required String recurrence,
    required String timezone,
    int? untilNoteId,
  }) async {
    final response = await _dio.post(
      '/api/reminders',
      data: {
        'text': text,
        'due_at': dueAt,
        'recurrence': recurrence,
        'timezone': timezone,
        'until_note_id': untilNoteId,
      },
    );
    return Map<String, dynamic>.from(response.data);
  }

  Future<Map<String, dynamic>> update(
    int id,
    Map<String, dynamic> fields,
  ) async {
    final response = await _dio.patch('/api/reminders/$id', data: fields);
    return Map<String, dynamic>.from(response.data);
  }

  Future<void> cancel(int id) async => _dio.post('/api/reminders/$id/cancel');
}
