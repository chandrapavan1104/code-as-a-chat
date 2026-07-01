import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'api.dart';

/// FCM push wiring for Gajala.
///
/// On Android the Firebase config is read from android/app/google-services.json
/// by the Gradle google-services plugin, so [Firebase.initializeApp] needs no
/// explicit options. This module handles permission, token registration to the
/// Code-as-a-Chat server, and showing foreground pushes as system notifications.

const _channel = AndroidNotificationChannel(
  'gajala_default', // must match AndroidManifest default_notification_channel_id
  'Gajala',
  description: 'Reminders and messages from Gajala',
  importance: Importance.high,
);

final _localNotifications = FlutterLocalNotificationsPlugin();

/// Must be a top-level function — invoked in a background isolate.
@pragma('vm:entry-point')
Future<void> _bgHandler(RemoteMessage message) async {
  // System tray handles display when the app is backgrounded; nothing to do,
  // but the handler must exist and be registered for delivery.
}

class Push {
  static bool _inited = false;

  /// Initialise Firebase + local notifications. Safe to call more than once.
  static Future<void> init() async {
    if (_inited) return;
    try {
      await Firebase.initializeApp();
      FirebaseMessaging.onBackgroundMessage(_bgHandler);
      await _localNotifications.initialize(const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      ));
      await _localNotifications
          .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
          ?.createNotificationChannel(_channel);
      // Show foreground pushes as a real system notification.
      FirebaseMessaging.onMessage.listen(_showLocal);
      _inited = true;
    } catch (e) {
      debugPrint('Push.init failed: $e');
    }
  }

  static void _showLocal(RemoteMessage m) {
    final n = m.notification;
    if (n == null) return;
    _localNotifications.show(
      n.hashCode,
      n.title ?? 'Gajala',
      n.body ?? '',
      const NotificationDetails(
        android: AndroidNotificationDetails('gajala_default', 'Gajala',
            channelDescription: 'Reminders and messages from Gajala',
            importance: Importance.high, priority: Priority.high, icon: '@mipmap/ic_launcher'),
      ),
    );
  }

  /// Ask for notification permission, fetch the FCM token, and register it with
  /// the server so it can push to this device. Returns the token (or null).
  static Future<String?> registerWith(GajalaApi api) async {
    try {
      await init();
      final fm = FirebaseMessaging.instance;
      final settings = await fm.requestPermission();
      if (settings.authorizationStatus == AuthorizationStatus.denied) {
        debugPrint('Push: notifications denied by user');
        return null;
      }
      final token = await fm.getToken();
      if (token != null) {
        await api.registerDevice(token, label: 'android');
      }
      // Keep the server in sync if the token rotates.
      fm.onTokenRefresh.listen((t) => api.registerDevice(t, label: 'android'));
      return token;
    } catch (e) {
      debugPrint('Push.registerWith failed: $e');
      return null;
    }
  }
}
