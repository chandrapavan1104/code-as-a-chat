// On-phone fallback brain for voice when the Mac is unreachable.
//
// The model file comes from the Mac (GET /api/voice/model/file), never from a
// third party, and lives in app-private storage. It answers general questions
// only: it has no access to notes, projects, the queue or chat memory, and says
// so rather than inventing them. Offline turns are not written to the Mac.

import 'dart:io';
import 'package:flutter_gemma/flutter_gemma.dart';
import 'package:flutter_gemma_litertlm/flutter_gemma_litertlm.dart';
import 'package:path_provider/path_provider.dart';
import 'api.dart';

const _systemPrompt =
    'You are Gajala, a friendly voice assistant running offline on the '
    "user's phone because their Mac can't be reached. Answer in one to three "
    'short spoken sentences with no markdown. You cannot see their notes, '
    'projects, reminders, task queue or Mac; if asked about those, say you need '
    'the connection back. /no_think';

final _thinkBlock = RegExp(r'<think>[\s\S]*?(</think>|$)');

class OfflineBrain {
  OfflineBrain._();
  static final instance = OfflineBrain._();

  bool _engineReady = false;
  InferenceChat? _chat;

  Future<File> _file() async {
    final dir = await getApplicationSupportDirectory();
    return File('${dir.path}/models/Qwen3-0.6B.litertlm');
  }

  Future<bool> isInstalled() async => (await _file()).exists();

  Future<int> installedBytes() async {
    final f = await _file();
    return await f.exists() ? await f.length() : 0;
  }

  Future<void> _ensureEngine() async {
    if (_engineReady) return;
    await FlutterGemma.initialize(inferenceEngines: const [LiteRtLmEngine()]);
    _engineReady = true;
  }

  Future<void> _register(File file) async {
    await _ensureEngine();
    await FlutterGemma.installModel(
      modelType: ModelType.qwen3,
      fileType: ModelFileType.litertlm,
    ).fromFile(file.path).install();
  }

  /// Download the model from the Mac, verify its size, and register it.
  Future<void> download(GajalaApi api, void Function(double) onProgress) async {
    final info = await api.voiceModelInfo();
    if (info['available'] != true) {
      throw StateError(
        'The Mac has no offline model yet. Run scripts/fetch-voice-model on the Mac.',
      );
    }
    final expected = (info['size'] as num).toInt();
    final file = await _file();
    await file.parent.create(recursive: true);
    final part = File('${file.path}.part');
    try {
      await api.downloadFile(api.voiceModelUrl, part.path, onProgress);
      final got = await part.length();
      if (got != expected) {
        throw StateError('Download incomplete ($got of $expected bytes).');
      }
      await part.rename(file.path);
    } finally {
      if (await part.exists()) await part.delete();
    }
    _chat = null;
    await _register(file);
  }

  Future<void> remove() async {
    _chat = null;
    final file = await _file();
    if (await file.exists()) await file.delete();
  }

  /// One offline turn. Earlier offline turns in this app session stay in
  /// context so follow-ups work.
  Future<String> answer(String prompt) async {
    final file = await _file();
    if (!await file.exists()) {
      throw StateError('No offline model on this phone.');
    }
    var chat = _chat;
    if (chat == null) {
      await _ensureEngine();
      if (!FlutterGemma.hasActiveModel()) await _register(file);
      final model = await FlutterGemma.getActiveModel(maxTokens: 2048);
      chat = await model.createChat(
        modelType: ModelType.qwen3,
        systemInstruction: _systemPrompt,
        maxOutputTokens: 200,
        temperature: 0.6,
        topK: 20,
      );
      _chat = chat;
    }
    await chat.addQueryChunk(Message.text(text: prompt, isUser: true));
    final r = await chat.generateChatResponse();
    final text = r is TextResponse ? r.token : '';
    final clean = text.replaceAll(_thinkBlock, '').trim();
    return clean.isEmpty ? "Sorry, I couldn't come up with an answer offline." : clean;
  }
}
