package com.codeasachat.gajala

import android.app.AlarmManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.media.AudioManager
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.BatteryManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.provider.Settings
import android.text.format.DateFormat
import org.json.JSONObject
import java.util.Date
import java.util.UUID
import kotlin.math.roundToInt

internal class EverydayVoice(private val context: Context) {
    fun execute(command: VoiceCommand, active: () -> Boolean, reply: (String) -> Unit) {
        if (!active()) { reply("Voice session ended."); return }
        try {
            if (command.kind == "media") {
                media(command.label, active, reply)
                return
            }
            reply(when (command.kind) {
                "time" -> "It’s ${DateFormat.getTimeFormat(context).format(Date())}."
                "battery" -> {
                    val battery = context.getSystemService(BatteryManager::class.java)
                    val level = battery.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
                    if (level !in 0..100) "I couldn’t read the battery level."
                    else "Battery is at $level percent and ${if (battery.isCharging) "charging" else "not charging"}."
                }
                "volume" -> volume(command.value)
                "torch" -> torch(command.value == 1)
                "timer_start" -> VoiceTimers(context).start(command.label, command.value)
                "timer_read" -> VoiceTimers(context).manage(command.label, false)
                "timer_cancel" -> VoiceTimers(context).manage(command.label, true)
                else -> "That command is unavailable."
            })
        } catch (_: Exception) {
            reply("Android couldn’t complete that phone action.")
        }
    }

    private fun volume(percent: Int): String {
        if (percent !in 0..100) return "Choose a media volume between zero and one hundred percent."
        val audio = context.getSystemService(AudioManager::class.java)
        val stream = AudioManager.STREAM_MUSIC
        val min = if (Build.VERSION.SDK_INT >= 28) audio.getStreamMinVolume(stream) else 0
        val max = audio.getStreamMaxVolume(stream)
        if (max <= min || audio.isVolumeFixed) return "This output does not allow media volume changes."
        val target = mediaVolumeStep(min, max, percent)
        audio.setStreamVolume(stream, target, 0)
        val current = (100.0 * (audio.getStreamVolume(stream) - min) / (max - min)).roundToInt()
        return "Media volume is $current percent."
    }

    private fun torch(on: Boolean): String {
        val manager = context.getSystemService(CameraManager::class.java)
        val id = manager.cameraIdList.firstOrNull {
            manager.getCameraCharacteristics(it).get(CameraCharacteristics.FLASH_INFO_AVAILABLE) == true
        } ?: return "This phone has no flashlight."
        manager.setTorchMode(id, on)
        return if (on) "Flashlight on." else "Flashlight off."
    }

    private fun media(action: String, active: () -> Boolean, reply: (String) -> Unit) {
        val manager = context.getSystemService(MediaSessionManager::class.java)
        val sessions = try {
            manager.getActiveSessions(ComponentName(context, GajalaNotificationListener::class.java))
        } catch (_: SecurityException) {
            reply("Allow notification access in Voice settings so I can control and verify playback.")
            return
        }
        val controller = sessions.firstOrNull {
            it.playbackState?.state == PlaybackState.STATE_PLAYING
        } ?: sessions.firstOrNull { it.playbackState?.state == PlaybackState.STATE_PAUSED }
        if (controller == null) {
            reply("There’s no active player I can control.")
            return
        }
        val beforeTitle = controller.metadata?.description?.let { it.mediaId ?: it.title?.toString() }
        val beforeQueueId = controller.playbackState?.activeQueueItemId
        if (!active()) { reply("Voice session ended."); return }
        when (action) {
            "pause" -> controller.transportControls.pause()
            "play" -> controller.transportControls.play()
            "next" -> controller.transportControls.skipToNext()
            "previous" -> controller.transportControls.skipToPrevious()
        }
        Handler(Looper.getMainLooper()).postDelayed({
            if (!active()) { reply("Voice session ended."); return@postDelayed }
            val state = controller.playbackState
            val afterTitle = controller.metadata?.description?.let { it.mediaId ?: it.title?.toString() }
            val changed = (beforeTitle != null && afterTitle != null && beforeTitle != afterTitle) ||
                (beforeQueueId != null && state != null && state.activeQueueItemId != beforeQueueId &&
                    state.activeQueueItemId >= 0)
            reply(when {
                action == "pause" && state?.state == PlaybackState.STATE_PAUSED -> "Playback is paused."
                action == "play" && state?.state == PlaybackState.STATE_PLAYING -> "Playback is playing."
                action in setOf("next", "previous") && changed -> "The track changed."
                else -> "I sent the playback control, but couldn’t verify the result."
            })
        }, 850)
    }
}

internal class VoiceTimers(private val context: Context) {
    private val storage = context.getSharedPreferences("gajala_voice_timers", Context.MODE_PRIVATE)
    private val alarms = context.getSystemService(AlarmManager::class.java)
    private val notifications = context.getSystemService(NotificationManager::class.java)
    private val boot = Settings.Global.getInt(context.contentResolver, Settings.Global.BOOT_COUNT, -1)
    private data class Timer(val id: String, val label: String, val deadline: Long)

    init {
        if (Build.VERSION.SDK_INT >= 26) {
            notifications.createNotificationChannel(NotificationChannel(
                CHANNEL, "Voice timer alerts", NotificationManager.IMPORTANCE_HIGH
            ))
        }
    }

    fun permissionIssue(): String? = when {
        Build.VERSION.SDK_INT >= 31 && !alarms.canScheduleExactAlarms() ->
            "Allow Alarms & reminders in Voice settings to start an accurate timer."
        !notifications.areNotificationsEnabled() ||
            (Build.VERSION.SDK_INT >= 26 && notifications.getNotificationChannel(CHANNEL)?.importance == 0) ->
            "Allow Gajala timer notifications in Voice settings before starting a timer."
        else -> null
    }

    private fun timers(): List<Timer> =
        storage.all.mapNotNull { (id, value) ->
            runCatching {
                val data = JSONObject(value as String)
                if (data.getInt("boot") != boot) {
                    storage.edit().remove(id).apply()
                    null
                } else Timer(id, data.getString("label"), data.getLong("deadline"))
            }.getOrNull()
        }

    private fun intent(id: String): PendingIntent = PendingIntent.getBroadcast(
        context, 0, Intent(context, VoiceTimerReceiver::class.java)
            .setData(android.net.Uri.fromParts("gajala-timer", id, null)),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
    )

    fun start(label: String, seconds: Int): String {
        if (seconds !in 1..86400) return "Choose a timer between one second and 24 hours."
        permissionIssue()?.let { return it }
        val live = timers().filter { it.deadline > SystemClock.elapsedRealtime() }
        if (live.size >= 12) return "Cancel a timer before starting another."
        if (label.isNotEmpty() && live.any { it.label == label }) return "A $label timer is already running."
        val id = UUID.randomUUID().toString()
        val deadline = SystemClock.elapsedRealtime() + seconds * 1000L
        val data = JSONObject().put("label", label).put("boot", boot).put("deadline", deadline)
        if (!storage.edit().putString(id, data.toString()).commit()) return "I couldn’t save the timer."
        try {
            alarms.setExactAndAllowWhileIdle(AlarmManager.ELAPSED_REALTIME_WAKEUP, deadline, intent(id))
        } catch (_: Exception) {
            storage.edit().remove(id).commit()
            return "Android refused to schedule the timer."
        }
        return "Started ${if (label.isEmpty()) "a timer" else "the $label timer"} for ${duration(seconds)}."
    }

    fun manage(label: String, cancel: Boolean): String {
        val matches = timers().filter {
            it.deadline > SystemClock.elapsedRealtime() && (label.isEmpty() || it.label == label)
        }
        if (matches.isEmpty()) return if (label.isEmpty()) "No timers are running." else "No $label timer is running."
        if (matches.size > 1) return "Which timer? Say its name."
        val timer = matches.single()
        val name = if (timer.label.isEmpty()) "The timer" else "The ${timer.label} timer"
        if (!cancel) {
            val seconds = ((timer.deadline - SystemClock.elapsedRealtime() + 999) / 1000).toInt()
            return "$name has ${duration(seconds.coerceAtLeast(1))} left."
        }
        if (!storage.edit().remove(timer.id).commit()) return "I couldn’t cancel the timer."
        alarms.cancel(intent(timer.id))
        return "$name is cancelled."
    }

    fun fire(id: String) {
        val timer = timers().firstOrNull { it.id == id } ?: return
        if (!storage.edit().remove(id).commit()) return
        val builder = if (Build.VERSION.SDK_INT >= 26) Notification.Builder(context, CHANNEL)
            else @Suppress("DEPRECATION") Notification.Builder(context).setDefaults(Notification.DEFAULT_SOUND)
        notifications.notify(id, 1, builder.setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Gajala timer")
            .setContentText(if (timer.label.isEmpty()) "Time’s up." else "${timer.label} timer finished.")
            .setCategory(Notification.CATEGORY_ALARM).setVisibility(Notification.VISIBILITY_PRIVATE)
            .setAutoCancel(true).build())
    }

    private fun duration(seconds: Int): String {
        val minutes = seconds / 60
        val remainder = seconds % 60
        return listOfNotNull(
            if (minutes == 0) null else "$minutes minute${if (minutes == 1) "" else "s"}",
            if (remainder == 0 && minutes > 0) null else "$remainder second${if (remainder == 1) "" else "s"}"
        ).joinToString(" and ")
    }

    companion object { const val CHANNEL = "gajala_timer_alerts" }
}

class VoiceTimerReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        intent.data?.schemeSpecificPart?.let { VoiceTimers(context).fire(it) }
    }
}
