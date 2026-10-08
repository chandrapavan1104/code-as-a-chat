package com.codeasachat.gajala

import android.Manifest
import android.annotation.SuppressLint
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.graphics.drawable.Icon
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import io.flutter.FlutterInjector
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.dart.DartExecutor
import io.flutter.plugin.common.BasicMessageChannel
import io.flutter.plugin.common.BinaryCodec
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Listens for "Hey Gajala" as a microphone foreground service.
 *
 * Android 14+ only lets an app keep the mic in the background through a
 * foreground service with a visible notification, and only lets it START one
 * while the app is on screen — so this is started from MainActivity and is not
 * restarted by the system if it gets killed (next app open restarts it).
 *
 * The mic is released (not just ignored) whenever listening is paused: screen
 * off or locked, Battery Saver on, or while Gajala itself is listening/speaking.
 */
class WakeWordService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private var engine: FlutterEngine? = null
    private var control: MethodChannel? = null
    private var audio: BasicMessageChannel<ByteBuffer>? = null
    private var engineReady = false
    private var recorder: AudioRecord? = null
    private var reader: Thread? = null
    private val pauses = mutableSetOf<String>()
    private val resumeAfterCooldown = Runnable { unpause(COOLDOWN) }

    private val screen = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                Intent.ACTION_SCREEN_OFF -> pause(SCREEN)
                Intent.ACTION_USER_PRESENT -> unpause(SCREEN)
                PowerManager.ACTION_POWER_SAVE_MODE_CHANGED -> syncPowerSave()
            }
        }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        instance = this
        createChannels(this)
        if (!startInForeground()) {
            stopSelf()
            return
        }
        registerReceiver(screen, IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_USER_PRESENT)
            addAction(PowerManager.ACTION_POWER_SAVE_MODE_CHANGED)
        })
        val power = getSystemService(PowerManager::class.java)
        if (!power.isInteractive) pause(SCREEN)
        if (appHoldsMic) pause(HELD)
        syncPowerSave()
        startEngine()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            setEnabled(this, false)
            stopSelf()
        }
        // The system may not restart a microphone service from the background.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        instance = null
        main.removeCallbacksAndMessages(null)
        stopRecording()
        runCatching { unregisterReceiver(screen) }
        engine?.destroy()
        engine = null
        super.onDestroy()
    }

    // ── state ─────────────────────────────────────────────────────────────────

    val listening: Boolean get() = recorder != null

    fun pause(reason: String) {
        pauses.add(reason)
        stopRecording()
        updateNotification()
    }

    fun unpause(reason: String) {
        pauses.remove(reason)
        if (pauses.isEmpty() && engineReady) startRecording()
        updateNotification()
    }

    private fun syncPowerSave() {
        if (getSystemService(PowerManager::class.java).isPowerSaveMode) pause(POWER_SAVE) else unpause(POWER_SAVE)
    }

    // ── headless Flutter engine running wakeWordMain ─────────────────────────

    private fun startEngine() {
        val dir = try {
            installModel()
        } catch (e: Exception) {
            stopSelf()
            return
        }
        val loader = FlutterInjector.instance().flutterLoader()
        loader.startInitialization(applicationContext)
        loader.ensureInitializationComplete(applicationContext, null)
        val e = FlutterEngine(applicationContext, null, false)
        control = MethodChannel(e.dartExecutor.binaryMessenger, "gajala/wakeword/engine").apply {
            setMethodCallHandler { call, result ->
                when (call.method) {
                    "ready" -> {
                            invokeMethod("configure", mapOf(
                                "dir" to dir.absolutePath,
                                "threshold" to calibration(this@WakeWordService).first,
                                "boost" to calibration(this@WakeWordService).second,
                            ), object : MethodChannel.Result {
                            override fun success(r: Any?) {
                                engineReady = true
                                if (pauses.isEmpty()) startRecording()
                                updateNotification()
                            }
                            override fun error(code: String, msg: String?, details: Any?) = stopSelf()
                            override fun notImplemented() = stopSelf()
                        })
                        result.success(null)
                    }
                    "detected" -> {
                        onDetected()
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
        }
        audio = BasicMessageChannel(e.dartExecutor.binaryMessenger, "gajala/wakeword/audio", BinaryCodec.INSTANCE)
        e.dartExecutor.executeDartEntrypoint(
            DartExecutor.DartEntrypoint(loader.findAppBundlePath(), "package:gajala/core/wake_word.dart", "wakeWordMain")
        )
        engine = e
    }

    /**
     * Copy the bundled model out of the APK (sherpa-onnx needs file paths), again
     * after each app update. Assets are compressed, so their size is unknowable
     * up front; the install timestamp says whether the copy is current.
     */
    private fun installModel(): File {
        val dir = File(filesDir, "wakeword")
        val stamp = File(dir, ".installed")
        @Suppress("DEPRECATION")
        val version = packageManager.getPackageInfo(packageName, 0).lastUpdateTime.toString()
        if (stamp.exists() && stamp.readText() == version) return dir
        dir.mkdirs()
        for (name in MODEL_FILES) {
            assets.open("wakeword/$name").use { input ->
                File(dir, name).outputStream().use { input.copyTo(it) }
            }
        }
        stamp.writeText(version)
        return dir
    }

    // ── microphone ────────────────────────────────────────────────────────────

    @SuppressLint("MissingPermission")
    private fun startRecording() {
        if (recorder != null || !hasMic(this)) return
        val minBytes = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = try {
            AudioRecord(
                MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE,
                AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                maxOf(minBytes, FRAME_BYTES * 4),
            )
        } catch (e: Exception) {
            return
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            return
        }
        rec.startRecording()
        recorder = rec
        reader = Thread {
            val buf = ByteArray(FRAME_BYTES)
            while (recorder === rec) {
                val n = rec.read(buf, 0, buf.size)
                if (n <= 0) continue
                val frame = ByteBuffer.allocateDirect(n).order(ByteOrder.LITTLE_ENDIAN)
                frame.put(buf, 0, n)
                main.post { if (recorder === rec) audio?.send(frame) }
            }
        }.apply { name = "gajala-wakeword"; start() }
    }

    private fun stopRecording() {
        val rec = recorder ?: return
        recorder = null
        runCatching { rec.stop() }
        reader?.join(500)
        reader = null
        rec.release()
    }

    // ── what "Hey Gajala" does ────────────────────────────────────────────────

    /**
     * Release the mic so voice mode can use it, then open voice mode by the most
     * direct route Android allows from the background:
     *  1. Gajala on screen → tell the app directly.
     *  2. Gajala is the default assistant → show the assistant session (the
     *     one background path Android permits for opening an activity).
     *  3. Otherwise → a heads-up notification the user taps.
     * Listening resumes after a short cooldown unless voice mode holds the mic.
     */
    private fun onDetected() {
        pause(COOLDOWN)
        main.removeCallbacks(resumeAfterCooldown)
        main.postDelayed(resumeAfterCooldown, COOLDOWN_MS)
        val activity = MainActivity.visible
        val assistant = GajalaVoiceInteractionService.instance
        when {
            activity != null -> activity.openVoice()
            assistant != null -> assistant.showSession(Bundle(), 0)
            else -> notifyTapToTalk()
        }
    }

    @Suppress("DEPRECATION")
    private fun notifyTapToTalk() {
        val n = builder(TRIGGER_CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Heard “Hey Gajala”")
            .setContentText("Tap to talk")
            .setContentIntent(voiceIntent(this))
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_CALL)
            .setPriority(Notification.PRIORITY_HIGH)
            .apply { if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) setTimeoutAfter(COOLDOWN_MS) }
            .build()
        getSystemService(NotificationManager::class.java).notify(TRIGGER_ID, n)
    }

    // ── foreground notification ───────────────────────────────────────────────

    private fun startInForeground(): Boolean = try {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(ONGOING_ID, ongoing(), ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
        } else {
            startForeground(ONGOING_ID, ongoing())
        }
        true
    } catch (e: Exception) {
        // Android 14+ refuses a microphone service started while Gajala is not
        // on screen, or without the mic permission.
        false
    }

    private fun updateNotification() {
        getSystemService(NotificationManager::class.java).notify(ONGOING_ID, ongoing())
    }

    private fun ongoing(): Notification {
        val status = when {
            !engineReady -> "Starting…"
            POWER_SAVE in pauses -> "Paused while Battery Saver is on"
            SCREEN in pauses -> "Paused while the screen is off"
            pauses.isNotEmpty() -> "Paused while Gajala is using the mic"
            else -> "Listening for “Hey Gajala” — audio stays on this phone"
        }
        val stop = PendingIntent.getService(
            this, 1, Intent(this, WakeWordService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        return builder(ONGOING_CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Hands-free Gajala")
            .setContentText(status)
            .setContentIntent(voiceIntent(this))
            .setOngoing(true)
            .setShowWhen(false)
            .addAction(Notification.Action.Builder(null as Icon?, "Turn off", stop).build())
            .build()
    }

    @Suppress("DEPRECATION")
    private fun builder(channel: String): Notification.Builder =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) Notification.Builder(this, channel)
        else Notification.Builder(this)

    companion object {
        @Volatile var instance: WakeWordService? = null

        /** Gajala is listening/speaking itself; survives the service not running yet. */
        @Volatile var appHoldsMic = false

        fun hold(on: Boolean) {
            appHoldsMic = on
            instance?.let { if (on) it.pause(HELD) else it.unpause(HELD) }
        }

        const val ACTION_STOP = "com.codeasachat.gajala.action.WAKEWORD_STOP"
        const val HELD = "held"
        private const val SCREEN = "screen"
        private const val POWER_SAVE = "power_save"
        private const val COOLDOWN = "cooldown"
        private const val COOLDOWN_MS = 15_000L
        private const val RATE = 16_000
        private const val FRAME_BYTES = RATE / 10 * 2 // 100 ms of 16-bit mono
        private const val ONGOING_CHANNEL = "gajala_wakeword"
        private const val TRIGGER_CHANNEL = "gajala_wakeword_trigger"
        private const val ONGOING_ID = 7301
        private const val TRIGGER_ID = 7302
        private const val PREFS = "gajala_wakeword"
        val MODEL_FILES = listOf(
            "encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx", "tokens.txt", "keywords.txt",
        )

        fun isEnabled(context: Context): Boolean =
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean("enabled", false)

        fun setEnabled(context: Context, on: Boolean) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putBoolean("enabled", on).apply()
        }

        fun calibration(context: Context): Pair<Double, Double> {
            val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            return prefs.getFloat("threshold", 0.3f).toDouble() to
                prefs.getFloat("boost", 1.5f).toDouble()
        }

        fun saveCalibration(context: Context, threshold: Double, boost: Double) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putFloat("threshold", threshold.toFloat())
                .putFloat("boost", boost.toFloat()).apply()
        }

        fun resetCalibration(context: Context) {
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .remove("threshold").remove("boost").apply()
        }

        fun hasMic(context: Context): Boolean =
            context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

        /** The engine's native library ships for 64-bit ARM phones only. */
        val supported: Boolean get() = Build.SUPPORTED_ABIS.contains("arm64-v8a")

        fun voiceIntent(context: Context): PendingIntent = PendingIntent.getActivity(
            context, 0,
            Intent(context, MainActivity::class.java)
                .setAction(MainActivity.ACTION_VOICE)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

        fun createChannels(context: Context) {
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
            val nm = context.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(
                NotificationChannel(ONGOING_CHANNEL, "Hands-free listening", NotificationManager.IMPORTANCE_LOW)
                    .apply { description = "Shown while Gajala listens for “Hey Gajala”." })
            nm.createNotificationChannel(
                NotificationChannel(TRIGGER_CHANNEL, "Hey Gajala", NotificationManager.IMPORTANCE_HIGH)
                    .apply { description = "Tap to talk after saying “Hey Gajala”." })
        }

        /** Start listening. Only valid while Gajala is on screen (Android 14+). */
        fun start(context: Context) {
            if (instance != null || !supported || !hasMic(context)) return
            val intent = Intent(context, WakeWordService::class.java)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) context.startForegroundService(intent)
            else context.startService(intent)
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, WakeWordService::class.java))
        }
    }
}
