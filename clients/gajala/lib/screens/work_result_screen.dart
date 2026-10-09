import 'package:android_intent_plus/android_intent.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/api.dart';
import '../core/models.dart';
import '../core/state.dart';
import '../core/theme.dart';
import '../widgets/chat_content.dart';
import '../widgets/work_result_widgets.dart';

class WorkResultScreen extends ConsumerStatefulWidget {
  final int jobId;
  final String? title;
  const WorkResultScreen({super.key, required this.jobId, this.title});

  @override
  ConsumerState<WorkResultScreen> createState() => _WorkResultScreenState();
}

class _WorkResultScreenState extends ConsumerState<WorkResultScreen> {
  GajalaApi? _lastApi;
  Future<QueueJobResult>? _resultFuture;

  @override
  void didUpdateWidget(covariant WorkResultScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.jobId != oldWidget.jobId) {
      final api = ref.read(apiProvider);
      _lastApi = api;
      _resultFuture = api?.queueJobResult(widget.jobId);
    }
  }

  @override
  Widget build(BuildContext context) {
    final api = ref.watch(apiProvider);
    if (api != null && !identical(api, _lastApi)) {
      _lastApi = api;
      _resultFuture = api.queueJobResult(widget.jobId);
    }
    return Scaffold(
      appBar: AppBar(
        title: const Text('Work result'),
        actions: [
          IconButton(
            tooltip: 'Retry loading result',
            onPressed: api == null
                ? null
                : () => setState(
                    () => _resultFuture = api.queueJobResult(widget.jobId),
                  ),
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: api == null
          ? const Center(child: Text('Connect to Gajala to open this result.'))
          : FutureBuilder<QueueJobResult>(
              future: _resultFuture,
              builder: (context, snapshot) {
                if (snapshot.connectionState != ConnectionState.done) {
                  return const Center(child: CircularProgressIndicator());
                }
                if (snapshot.hasError) {
                  return Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text(
                        friendlyError(snapshot.error!),
                        style: const TextStyle(color: GajalaColors.danger),
                        textAlign: TextAlign.center,
                      ),
                    ),
                  );
                }
                final result = snapshot.data!;
                return _ResultBody(
                  title:
                      widget.title ??
                      result.title ??
                      'Work result #${widget.jobId}',
                  result: result,
                  api: api,
                );
              },
            ),
    );
  }
}

Future<void> showWorkResult(BuildContext context, int jobId, {String? title}) =>
    Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => WorkResultScreen(jobId: jobId, title: title),
      ),
    );

class _ResultBody extends StatelessWidget {
  final String title;
  final QueueJobResult result;
  final GajalaApi api;
  const _ResultBody({
    required this.title,
    required this.result,
    required this.api,
  });

  @override
  Widget build(BuildContext context) {
    final partial = result.completeness == 'partial';
    final unavailable =
        !result.available ||
        result.completeness == 'none' ||
        result.text.trim().isEmpty;
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 14, 16, 32),
      children: [
        Text(title, style: Theme.of(context).textTheme.titleLarge),
        const SizedBox(height: 8),
        Wrap(
          spacing: 8,
          children: [
            Chip(label: Text(result.kind.toUpperCase())),
            Chip(label: Text(result.completeness.toUpperCase())),
          ],
        ),
        if (result.text.trim().isNotEmpty)
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              onPressed: () async {
                await Clipboard.setData(ClipboardData(text: result.text));
                if (context.mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    const SnackBar(content: Text('Report copied.')),
                  );
                }
              },
              icon: const Icon(Icons.copy_outlined),
              label: const Text('Copy report'),
            ),
          ),
        if (partial)
          const _ResultNotice(
            icon: Icons.warning_amber_rounded,
            text:
                'Partial result: the work stopped before completion. Review the available output and attempt details.',
          ),
        if (unavailable)
          const _ResultNotice(
            icon: Icons.info_outline,
            text:
                'No result text was recorded for this work order. The attempt history may explain why.',
          ),
        if (result.text.trim().isNotEmpty) ...[
          const SizedBox(height: 12),
          ChatContent(text: result.text, api: api),
        ],
        if (result.artifacts.isNotEmpty) ...[
          const SizedBox(height: 18),
          Text('Artifacts', style: Theme.of(context).textTheme.titleMedium),
          for (final artifact in result.artifacts)
            _ArtifactCard(artifact: artifact, api: api),
        ],
        const SizedBox(height: 16),
        WorkAttemptTimeline(attempts: result.attempts),
      ],
    );
  }
}

class _ResultNotice extends StatelessWidget {
  final IconData icon;
  final String text;
  const _ResultNotice({required this.icon, required this.text});

  @override
  Widget build(BuildContext context) => Card(
    color: GajalaColors.amber.withValues(alpha: .12),
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(icon, color: GajalaColors.amber),
          const SizedBox(width: 10),
          Expanded(child: Text(text)),
        ],
      ),
    ),
  );
}

class _ArtifactCard extends StatelessWidget {
  final QueueResultArtifact artifact;
  final GajalaApi api;
  const _ArtifactCard({required this.artifact, required this.api});

  @override
  Widget build(BuildContext context) => Card(
    child: Padding(
      padding: const EdgeInsets.all(12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            artifact.kind.replaceAll('_', ' ').toUpperCase(),
            style: Theme.of(context).textTheme.labelLarge,
          ),
          const SizedBox(height: 6),
          if (artifact.ref.startsWith('/'))
            ChatContent(text: '[file: ${artifact.ref}]', api: api)
          else if (_webUri(artifact.ref) case final uri?)
            TextButton.icon(
              onPressed: () => _openWebUri(context, uri),
              icon: const Icon(Icons.open_in_new),
              label: Text(
                artifact.name.isEmpty ? uri.toString() : artifact.name,
              ),
            )
          else
            SelectableText(artifact.ref),
          if (artifact.size != null)
            Text(
              '${artifact.size} bytes',
              style: TextStyle(color: context.pal.textDim),
            ),
          for (final file in artifact.filesChanged)
            SelectableText(file, style: TextStyle(color: context.pal.textDim)),
        ],
      ),
    ),
  );

  Uri? _webUri(String value) {
    final uri = Uri.tryParse(value);
    if (uri == null ||
        !{'http', 'https'}.contains(uri.scheme) ||
        uri.host.isEmpty) {
      return null;
    }
    return uri;
  }

  Future<void> _openWebUri(BuildContext context, Uri uri) async {
    try {
      if (Theme.of(context).platform != TargetPlatform.android) return;
      await AndroidIntent(
        action: 'android.intent.action.VIEW',
        data: uri.toString(),
      ).launch();
    } catch (_) {
      if (context.mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Could not open this artifact link.')),
        );
      }
    }
  }
}
