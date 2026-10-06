// Optional fingerprint / face / PIN lock for the whole app.
//
// Gajala can run commands on the owner's Mac, so a phone left unlocked should
// not be enough. Off by default; when on, the app asks on cold start and after
// more than a minute in the background. It wraps the navigator, so voice mode
// opened by the assistant gesture or wake word is covered too.

import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:local_auth/local_auth.dart';
import 'theme.dart';

class AppLock {
  static const _store = FlutterSecureStorage(
    aOptions: AndroidOptions(encryptedSharedPreferences: true),
  );
  static const _key = 'app_lock_enabled';
  static const relockAfter = Duration(minutes: 1);
  static final _auth = LocalAuthentication();

  /// Unreadable storage counts as "off": failing closed here would leave the
  /// owner staring at a blank screen with no way in.
  static Future<bool> enabled() async {
    try {
      return (await _store.read(key: _key)) == 'true';
    } catch (_) {
      return false;
    }
  }

  /// Turning the lock on requires one successful unlock first, so the owner
  /// cannot lock themselves out on a phone without a screen lock.
  static Future<String?> setEnabled(bool on) async {
    if (on) {
      if (!await _auth.isDeviceSupported()) {
        return 'This phone has no screen lock or biometrics set up.';
      }
      if (!await authenticate('Confirm to turn on the Gajala lock')) {
        return 'Not confirmed, so the lock stays off.';
      }
    }
    await _store.write(key: _key, value: on.toString());
    return null;
  }

  static Future<bool> authenticate(String reason) async {
    try {
      return await _auth.authenticate(localizedReason: reason);
    } catch (_) {
      return false;
    }
  }
}

class AppLockGate extends StatefulWidget {
  final Widget child;
  const AppLockGate({super.key, required this.child});
  @override
  State<AppLockGate> createState() => _AppLockGateState();
}

class _AppLockGateState extends State<AppLockGate> with WidgetsBindingObserver {
  bool? _locked; // null until the setting has been read: show nothing yet
  bool _authenticating = false;
  DateTime? _leftAt;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    AppLock.enabled().then((on) {
      if (!mounted) return;
      setState(() => _locked = on);
      if (on) _unlock();
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    // The system unlock dialog itself pauses the app; that must not re-lock.
    if (_authenticating) return;
    if (state == AppLifecycleState.paused || state == AppLifecycleState.hidden) {
      _leftAt ??= DateTime.now();
    } else if (state == AppLifecycleState.resumed) {
      final left = _leftAt;
      _leftAt = null;
      if (left == null || DateTime.now().difference(left) < AppLock.relockAfter) return;
      AppLock.enabled().then((on) {
        if (!on || !mounted) return;
        setState(() => _locked = true);
        _unlock();
      });
    }
  }

  Future<void> _unlock() async {
    if (_authenticating) return;
    _authenticating = true;
    final ok = await AppLock.authenticate('Unlock Gajala');
    _authenticating = false;
    if (ok && mounted) setState(() => _locked = false);
  }

  @override
  Widget build(BuildContext context) {
    final locked = _locked;
    return Stack(children: [
      // Kept mounted under the lock so a running chat turn keeps its state.
      Offstage(offstage: locked != false, child: widget.child),
      if (locked != false)
        Positioned.fill(
          child: Material(
            color: context.pal.bg,
            child: locked == null
                ? const SizedBox.shrink()
                : Center(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                      const Icon(Icons.lock_outline, size: 48),
                      const SizedBox(height: 12),
                      const Text('Gajala is locked'),
                      const SizedBox(height: 16),
                      FilledButton.icon(
                        icon: const Icon(Icons.fingerprint),
                        label: const Text('Unlock'),
                        onPressed: _unlock,
                      ),
                    ]),
                  ),
          ),
        ),
    ]);
  }
}
