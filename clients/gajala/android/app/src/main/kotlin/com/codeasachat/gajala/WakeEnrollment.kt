package com.codeasachat.gajala

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Handler
import android.os.Looper
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.util.concurrent.atomic.AtomicBoolean

/** Short, in-memory microphone captures used only to tune wake-word sensitivity. */
class WakeEnrollment(private val context: Context) {
    private val main = Handler(Looper.getMainLooper())
    private val recording = AtomicBoolean(false)
    @Volatile private var cancelToken: AtomicBoolean? = null
    @Volatile private var recorder: AudioRecord? = null
    private val restart = Runnable {
        if (MainActivity.visible != null && WakeWordService.isEnabled(context)) {
            WakeWordService.start(context)
        }
    }

    fun cancel() {
        cancelToken?.set(true)
        runCatching { recorder?.stop() }
        main.removeCallbacks(restart)
    }

    @SuppressLint("MissingPermission")
    fun capture(result: MethodChannel.Result) {
        if (!WakeWordService.hasMic(context)) {
            result.error("mic_permission", "Microphone permission is required.", null)
            return
        }
        if (!recording.compareAndSet(false, true)) {
            result.error("recording_busy", "A sample is already recording.", null)
            return
        }
        val cancelled = AtomicBoolean(false)
        cancelToken = cancelled
        Thread {
            val bytes = ByteArray(RATE * SECONDS * 2)
            var offset = 0
            var localRecorder: AudioRecord? = null
            try {
                val min = AudioRecord.getMinBufferSize(
                    RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
                val rec = AudioRecord(
                    MediaRecorder.AudioSource.VOICE_RECOGNITION, RATE,
                    AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
                    maxOf(min, 3200))
                localRecorder = rec
                recorder = rec
                if (rec.state != AudioRecord.STATE_INITIALIZED) error("Microphone unavailable")
                rec.startRecording()
                while (offset < bytes.size && !cancelled.get()) {
                    val n = rec.read(bytes, offset, bytes.size - offset)
                    if (n > 0) offset += n
                    else if (n < 0 && !cancelled.get()) error("Microphone read failed")
                }
                if (cancelled.get()) main.post {
                    result.error("cancelled", "Recording cancelled.", null)
                } else main.post { result.success(bytes) }
            } catch (e: Exception) {
                main.post {
                    if (cancelled.get()) {
                        result.error("cancelled", "Recording cancelled.", null)
                    } else {
                        result.error("recording_failed", e.message ?: "Recording failed", null)
                    }
                }
            } finally {
                localRecorder?.let { runCatching { it.stop() }; it.release() }
                if (recorder === localRecorder) recorder = null
                if (cancelToken === cancelled) cancelToken = null
                recording.set(false)
            }
        }.apply { name = "gajala-wake-enrollment"; start() }
    }

    fun modelDir(): String {
        val dir = File(context.filesDir, "wakeword")
        dir.mkdirs()
        for (name in WakeWordService.MODEL_FILES) {
            val target = File(dir, name)
            if (!target.exists()) context.assets.open("wakeword/$name").use { input ->
                target.outputStream().use { input.copyTo(it) }
            }
        }
        return dir.absolutePath
    }

    fun save(threshold: Double, boost: Double) {
        WakeWordService.saveCalibration(context, threshold, boost)
        val enabled = WakeWordService.isEnabled(context)
        WakeWordService.stop(context)
        if (enabled && MainActivity.visible != null) main.postDelayed(restart, 300)
    }

    fun reset() {
        WakeWordService.resetCalibration(context)
        val enabled = WakeWordService.isEnabled(context)
        WakeWordService.stop(context)
        if (enabled && MainActivity.visible != null) main.postDelayed(restart, 300)
    }

    companion object { private const val RATE = 16_000; private const val SECONDS = 3 }
}
