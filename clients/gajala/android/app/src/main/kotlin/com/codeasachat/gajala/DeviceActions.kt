package com.codeasachat.gajala

import android.Manifest
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.os.Build
import android.os.SystemClock
import android.provider.MediaStore
import android.provider.Settings
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import android.telephony.SmsManager
import android.view.KeyEvent
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/**
 * Phone actions Flutter plugins don't cover, for the `device` skill. Every
 * method answers {ok, ...}; failures carry a plain-language `error`. Sensitive
 * ones (SMS, Do Not Disturb, notifications) check their Android permission and
 * report `needsAccess` rather than failing silently.
 */
class DeviceActions(private val activity: MainActivity) : MethodChannel.MethodCallHandler {
    private val ctx: Context get() = activity
    private var pendingSms: Pair<MethodCall, MethodChannel.Result>? = null

    override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
        try {
            when (call.method) {
                "flashlight" -> result.success(flashlight(call.argument<Boolean>("on") == true))
                "mediaKey" -> result.success(mediaKey(call.argument<String>("key") ?: ""))
                "volume" -> result.success(volume(call.argument<String>("change") ?: ""))
                "dnd" -> result.success(dnd(call.argument<Boolean>("on") == true))
                "notifications" -> result.success(notifications())
                "smsSend" -> smsSend(call, result)
                "apps" -> result.success(launchableApps())
                "launch" -> result.success(launch(call.argument<String>("package") ?: ""))
                "settingsPanel" -> result.success(settingsPanel(call.argument<String>("panel") ?: ""))
                "playFromSearch" -> result.success(
                    playFromSearch(call.argument<String>("query") ?: "", call.argument<String>("package")))
                "openAccessSettings" -> result.success(openAccessSettings(call.argument<String>("which") ?: ""))
                else -> result.notImplemented()
            }
        } catch (e: Exception) {
            result.success(mapOf("ok" to false, "error" to (e.message ?: e.javaClass.simpleName)))
        }
    }

    private fun ok(vararg extra: Pair<String, Any?>) = mapOf("ok" to true) + extra
    private fun fail(error: String, vararg extra: Pair<String, Any?>) =
        mapOf("ok" to false, "error" to error) + extra

    // ── flashlight ────────────────────────────────────────────────────────────
    private fun flashlight(on: Boolean): Map<String, Any?> {
        val cm = ctx.getSystemService(CameraManager::class.java)
        val id = cm.cameraIdList.firstOrNull {
            cm.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
        } ?: return fail("This phone has no flashlight.")
        cm.setTorchMode(id, on)
        return ok()
    }

    // ── media + volume ────────────────────────────────────────────────────────
    private fun mediaKey(key: String): Map<String, Any?> {
        val code = when (key) {
            "pause" -> KeyEvent.KEYCODE_MEDIA_PAUSE
            "play" -> KeyEvent.KEYCODE_MEDIA_PLAY
            "next" -> KeyEvent.KEYCODE_MEDIA_NEXT
            "previous" -> KeyEvent.KEYCODE_MEDIA_PREVIOUS
            "toggle" -> KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE
            else -> return fail("Unknown media key $key")
        }
        val am = ctx.getSystemService(AudioManager::class.java)
        val now = SystemClock.uptimeMillis()
        am.dispatchMediaKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_DOWN, code, 0))
        am.dispatchMediaKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_UP, code, 0))
        return ok("music_active" to am.isMusicActive)
    }

    private fun volume(change: String): Map<String, Any?> {
        val am = ctx.getSystemService(AudioManager::class.java)
        val dir = when (change) {
            "up" -> AudioManager.ADJUST_RAISE
            "down" -> AudioManager.ADJUST_LOWER
            "mute" -> AudioManager.ADJUST_MUTE
            "unmute" -> AudioManager.ADJUST_UNMUTE
            else -> return fail("Unknown volume change $change")
        }
        am.adjustStreamVolume(AudioManager.STREAM_MUSIC, dir, AudioManager.FLAG_SHOW_UI)
        val max = am.getStreamMaxVolume(AudioManager.STREAM_MUSIC)
        val now = am.getStreamVolume(AudioManager.STREAM_MUSIC)
        return ok("level_pct" to if (max > 0) now * 100 / max else 0)
    }

    // ── Do Not Disturb (needs "Do Not Disturb access") ───────────────────────
    private fun dnd(on: Boolean): Map<String, Any?> {
        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (!nm.isNotificationPolicyAccessGranted) {
            return fail("Gajala doesn't have Do Not Disturb access yet.", "needsAccess" to "dnd")
        }
        nm.setInterruptionFilter(
            if (on) NotificationManager.INTERRUPTION_FILTER_PRIORITY
            else NotificationManager.INTERRUPTION_FILTER_ALL)
        return ok()
    }

    // ── notifications (needs notification access) ────────────────────────────
    private fun notifications(): Map<String, Any?> {
        val listener = GajalaNotificationListener.instance
            ?: return fail("Gajala doesn't have notification access yet.", "needsAccess" to "notifications")
        val pm = ctx.packageManager
        val items = listener.activeNotifications
            .filter { it.packageName != ctx.packageName && !it.isOngoing }
            .sortedByDescending { it.postTime }
            .take(30)
            .mapNotNull { describe(it, pm) }
        return ok("notifications" to items)
    }

    private fun describe(sbn: StatusBarNotification, pm: PackageManager): Map<String, Any?>? {
        val extras = sbn.notification.extras
        val title = extras.getCharSequence("android.title")?.toString()
        val text = (extras.getCharSequence("android.bigText") ?: extras.getCharSequence("android.text"))
            ?.toString()
        if (title.isNullOrBlank() && text.isNullOrBlank()) return null
        val app = try {
            pm.getApplicationLabel(pm.getApplicationInfo(sbn.packageName, 0)).toString()
        } catch (e: PackageManager.NameNotFoundException) {
            sbn.packageName
        }
        return mapOf("app" to app, "title" to title, "text" to text?.take(300), "posted_at" to sbn.postTime)
    }

    // ── SMS (needs SEND_SMS, asked the first time) ───────────────────────────
    private fun smsSend(call: MethodCall, result: MethodChannel.Result) {
        if (ctx.checkSelfPermission(Manifest.permission.SEND_SMS) != PackageManager.PERMISSION_GRANTED) {
            pendingSms?.second?.success(fail("Cancelled."))
            pendingSms = call to result
            activity.requestPermissions(arrayOf(Manifest.permission.SEND_SMS), SMS_PERMISSION)
            return
        }
        val number = call.argument<String>("number") ?: ""
        val text = call.argument<String>("text") ?: ""
        if (number.isBlank() || text.isBlank()) {
            result.success(fail("Need a number and a message."))
            return
        }
        val sms = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) ctx.getSystemService(SmsManager::class.java)
        else @Suppress("DEPRECATION") SmsManager.getDefault()
        val parts = sms.divideMessage(text)
        sms.sendMultipartTextMessage(number, null, parts, null, null)
        result.success(ok("parts" to parts.size))
    }

    fun onPermissionResult(requestCode: Int): Boolean {
        if (requestCode != SMS_PERMISSION) return false
        val (call, result) = pendingSms ?: return true
        pendingSms = null
        if (ctx.checkSelfPermission(Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED) {
            smsSend(call, result)
        } else {
            result.success(fail("SMS permission was not granted."))
        }
        return true
    }

    // ── apps ──────────────────────────────────────────────────────────────────
    private fun launchableApps(): Map<String, Any?> {
        val pm = ctx.packageManager
        val main = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val apps = pm.queryIntentActivities(main, 0).map {
            mapOf("label" to it.loadLabel(pm).toString(), "package" to it.activityInfo.packageName)
        }.distinctBy { it["package"] }
        return ok("apps" to apps)
    }

    private fun launch(pkg: String): Map<String, Any?> {
        val intent = ctx.packageManager.getLaunchIntentForPackage(pkg)
            ?: return fail("That app can't be opened.")
        ctx.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        return ok()
    }

    // ── settings panels & access screens ─────────────────────────────────────
    private fun settingsPanel(panel: String): Map<String, Any?> {
        val q = Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q
        val action = when (panel) {
            "wifi" -> if (q) Settings.Panel.ACTION_WIFI else Settings.ACTION_WIFI_SETTINGS
            "internet" -> if (q) Settings.Panel.ACTION_INTERNET_CONNECTIVITY else Settings.ACTION_WIRELESS_SETTINGS
            "volume" -> if (q) Settings.Panel.ACTION_VOLUME else Settings.ACTION_SOUND_SETTINGS
            "nfc" -> if (q) Settings.Panel.ACTION_NFC else Settings.ACTION_NFC_SETTINGS
            "bluetooth" -> Settings.ACTION_BLUETOOTH_SETTINGS
            else -> return fail("Unknown settings panel $panel")
        }
        ctx.startActivity(Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        return ok()
    }

    private fun openAccessSettings(which: String): Map<String, Any?> {
        val intent = when (which) {
            "dnd" -> Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)
            "notifications" -> if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS).putExtra(
                    Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME,
                    ComponentName(ctx, GajalaNotificationListener::class.java).flattenToString())
            } else {
                Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
            }
            else -> return fail("Unknown access screen $which")
        }
        ctx.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        return ok()
    }

    // ── music ─────────────────────────────────────────────────────────────────
    private fun playFromSearch(query: String, pkg: String?): Map<String, Any?> {
        fun intent(target: String?) = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH).apply {
            putExtra(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/*")
            putExtra("query", query)
            if (target != null) setPackage(target)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        return try {
            ctx.startActivity(intent(pkg))
            ok("app" to pkg)
        } catch (e: ActivityNotFoundException) {
            if (pkg == null) return fail("No music app on this phone can play by search.")
            try {
                ctx.startActivity(intent(null))   // the asked-for app is missing: any player
                ok("app" to null, "fallback" to true)
            } catch (e2: ActivityNotFoundException) {
                fail("No music app on this phone can play by search.")
            }
        }
    }

    companion object {
        const val SMS_PERMISSION = 7320
    }
}

/** Gives Gajala read access to the notifications currently showing, once the
 *  owner grants notification access. Nothing is stored or forwarded here; the
 *  list is read only when the owner asks. */
class GajalaNotificationListener : NotificationListenerService() {
    override fun onListenerConnected() {
        instance = this
    }

    override fun onListenerDisconnected() {
        instance = null
    }

    companion object {
        @Volatile var instance: GajalaNotificationListener? = null
    }
}
