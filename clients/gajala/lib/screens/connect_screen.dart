import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../core/api.dart';
import '../core/state.dart';
import '../core/theme.dart';

class ConnectScreen extends ConsumerStatefulWidget {
  const ConnectScreen({super.key});
  @override
  ConsumerState<ConnectScreen> createState() => _ConnectScreenState();
}

class _ConnectScreenState extends ConsumerState<ConnectScreen> {
  final _url = TextEditingController(text: 'https://');
  final _token = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    final c = ref.read(configProvider);
    if (c != null) {
      _url.text = c.url;
      _token.text = c.token;
    }
  }

  Future<void> _connect() async {
    setState(() { _busy = true; _error = null; });
    final url = _url.text.trim();
    final token = _token.text.trim();
    try {
      if (!await GajalaApi.ping(url)) {
        throw 'Server not reachable at $url';
      }
      final api = GajalaApi(url, token);
      final skills = await api.skills();   // verifies the token
      await ref.read(configProvider.notifier).connect(url, token);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Connected — ${skills.length} skills live 🔥'),
              backgroundColor: GajalaColors.ok),
        );
      }
    } catch (e) {
      setState(() => _error = friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(28),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const SizedBox(height: 40),
                const Icon(Icons.bolt, size: 56, color: GajalaColors.accent),
                const SizedBox(height: 16),
                Text('Gajala',
                    textAlign: TextAlign.center,
                    style: Theme.of(context).textTheme.headlineMedium
                        ?.copyWith(fontWeight: FontWeight.w800)),
                const SizedBox(height: 6),
                Text('Connect to your Mac',
                    textAlign: TextAlign.center,
                    style: TextStyle(color: context.pal.textDim)),
                const SizedBox(height: 36),
                Text('Server URL', style: TextStyle(color: context.pal.textDim)),
                const SizedBox(height: 6),
                TextField(
                  controller: _url,
                  keyboardType: TextInputType.url,
                  autocorrect: false,
                  decoration: const InputDecoration(hintText: 'https://your-mac.ts.net'),
                ),
                const SizedBox(height: 6),
                Text('Tailscale URL or http://<lan-ip>:8000',
                    style: TextStyle(color: context.pal.textDim, fontSize: 11)),
                const SizedBox(height: 20),
                Text('API Token', style: TextStyle(color: context.pal.textDim)),
                const SizedBox(height: 6),
                TextField(
                  controller: _token,
                  obscureText: true,
                  autocorrect: false,
                  decoration: const InputDecoration(hintText: '~/.codeasachat/api_token'),
                ),
                const SizedBox(height: 28),
                FilledButton(
                  onPressed: _busy ? null : _connect,
                  child: _busy
                      ? const SizedBox(height: 20, width: 20,
                          child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Text('Connect', style: TextStyle(fontSize: 16, fontWeight: FontWeight.w600)),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 18),
                  Text(_error!,
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: GajalaColors.danger)),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
