import 'package:firebase_core/firebase_core.dart';
import 'package:firebase_messaging/firebase_messaging.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
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

  /// Navigator used to deep-link into a chat when a reply notification is tapped.
  /// Wired to MaterialApp.navigatorKey in main.dart.
  static final GlobalKey<NavigatorState> navigatorKey = GlobalKey<NavigatorState>();

  /// Session id of the chat the user is currently viewing in the foreground.
  /// ChatScreen sets this while active and clears it on leave/background. When a
  /// `chat_reply` push targets this exact session we skip the notification —
  /// the reply is already on screen. So the ping only fires when you're *out*
  /// of that chat (another screen, another app, or the phone locked).
  static String? activeSession;

  /// main.dart wires this to push the chat screen for [sessionId].
  static void Function(String sessionId)? onOpenChat;

  /// Initialise Firebase + local notifications. Safe to call more than once.
  static Future<void> init() async {
    if (_inited) return;
    try {
      await Firebase.initializeApp();
      FirebaseMessaging.onBackgroundMessage(_bgHandler);
      await _localNotifications.initialize(
        const InitializationSettings(
          android: AndroidInitializationSettings('@mipmap/ic_launcher'),
        ),
        onDidReceiveNotificationResponse: _onLocalTap,
      );
      await _localNotifications
          .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
          ?.createNotificationChannel(_channel);
      // Show foreground pushes as a real system notification.
      FirebaseMessaging.onMessage.listen(_showLocal);
      // Tap on a system-tray push while the app is backgrounded → open the chat.
      FirebaseMessaging.onMessageOpenedApp.listen(_onOpened);
      _inited = true;
    } catch (e) {
      debugPrint('Push.init failed: $e');
    }
  }

  /// If the app was launched cold by tapping a push, route to the right chat.
  /// Call once after the first frame (navigator + onOpenChat must be ready).
  static Future<void> handleLaunchMessage() async {
    try {
      final m = await FirebaseMessaging.instance.getInitialMessage();
      if (m != null) _onOpened(m);
    } catch (e) {
      debugPrint('Push.handleLaunchMessage failed: $e');
    }
  }

  static void _showLocal(RemoteMessage m) {
    final n = m.notification;
    if (n == null) return;
    // Suppress a reply ping if the user is already looking at that chat.
    if (m.data['type'] == 'chat_reply' &&
        m.data['session_id'] != null &&
        m.data['session_id'] == activeSession) {
      return;
    }
    _localNotifications.show(
      n.hashCode,
      n.title ?? 'Gajala',
      n.body ?? '',
      const NotificationDetails(
        android: AndroidNotificationDetails('gajala_default', 'Gajala',
            channelDescription: 'Reminders and messages from Gajala',
            importance: Importance.high, priority: Priority.high, icon: '@mipmap/ic_launcher'),
      ),
      payload: m.data['session_id'] as String?,
    );
  }

  /// Tap on a notification we showed ourselves (app was foreground when it came).
  static void _onLocalTap(NotificationResponse r) {
    final sid = r.payload;
    if (sid != null && sid.isNotEmpty) onOpenChat?.call(sid);
  }

  /// Tap on a system-tray push (app was backgrounded / killed).
  static void _onOpened(RemoteMessage m) {
    final sid = m.data['session_id'];
    if (m.data['type'] == 'chat_reply' && sid is String && sid.isNotEmpty) {
      onOpenChat?.call(sid);
    }
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
