package com.codeasachat.gajala

import android.content.Context
import android.util.AtomicFile
import java.io.File
import org.json.JSONObject
import org.json.JSONArray
import org.tensorflow.lite.Interpreter
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.abs
import kotlin.math.sqrt

/** Private, version-bound examples. Only normalized features are persisted. */
internal object PersonalWakeProfile {
    const val DIMENSIONS = 480
    const val MAX_EXAMPLES = 8
    const val THRESHOLD = 0.93
    private const val VERSION = "oww-v051-anchors-v1"

    private fun file(context: Context) =
        AtomicFile(File(context.noBackupFilesDir, "personal_wake_profile.json"))

    fun load(context: Context): List<FloatArray> = runCatching {
        val stored = JSONObject(file(context).readFully().toString(Charsets.UTF_8))
        if (stored.optString("version") != VERSION) return emptyList()
        val array = stored.getJSONArray("examples")
        require(array.length() in 3..MAX_EXAMPLES)
        List(array.length()) { index ->
            val example = array.getJSONArray(index)
            require(example.length() == DIMENSIONS)
            FloatArray(DIMENSIONS) { example.getDouble(it).toFloat() }.also { require(valid(it)) }
        }
    }.getOrDefault(emptyList())

    private fun valid(example: FloatArray) = example.size == DIMENSIONS &&
        example.all { it.isFinite() && abs(it) <= 1.001f }

    fun save(context: Context, examples: List<FloatArray>): Boolean {
        if (examples.size !in 3..MAX_EXAMPLES || !examples.all(::valid)) return false
        val array = JSONArray()
        for (example in examples) array.put(JSONArray(example.map { it.toDouble() }))
        val target = file(context)
        val output = runCatching { target.startWrite() }.getOrNull() ?: return false
        return try {
            output.write(JSONObject().put("version", VERSION).put("examples", array)
                .toString().toByteArray(Charsets.UTF_8))
            target.finishWrite(output)
            true
        } catch (_: Exception) {
            target.failWrite(output)
            false
        }
    }

    fun reset(context: Context): Boolean {
        val target = file(context)
        target.delete()
        return !target.baseFile.exists()
    }

    fun score(candidate: FloatArray, examples: List<FloatArray>): Double =
        examples.maxOfOrNull { example ->
            candidate.indices.sumOf { candidate[it].toDouble() * example[it] } / 5
        } ?: 0.0
}

internal class PersonalWakeEncoder(context: Context) : AutoCloseable {
    private val mel = interpreter(context, "melspectrogram")
    private val embed = try {
        interpreter(context, "embedding_model")
    } catch (error: Throwable) {
        mel.close()
        throw error
    }

    init {
        mel.resizeInput(0, intArrayOf(1, WAVE_SIZE))
        mel.allocateTensors()
        embed.allocateTensors()
    }

    fun features(samples: ShortArray): FloatArray {
        require(samples.size in WakeSegments.MIN_WORD..WakeSegments.MAX_WORD)
        val peak = samples.maxOf { abs(it.toInt()) }.coerceAtLeast(1)
        val gain = (11000f / peak).coerceIn(0.5f, 3f)
        val input = Array(1) { FloatArray(WAVE_SIZE) }
        val leading = (WAVE_SIZE - samples.size) / 2
        for (i in samples.indices) input[0][leading + i] = samples[i] * gain
        val melOut = floats(mel.getOutputTensor(0).numBytes())
        mel.run(input, melOut)
        melOut.rewind()
        val rows = FloatArray(melOut.remaining() / 4) { melOut.float / 10f + 2f }
        val count = rows.size / 32
        require(count >= 76)
        val out = FloatArray(PersonalWakeProfile.DIMENSIONS)
        val embedIn = floats(76 * 32 * 4)
        val embedOut = floats(embed.getOutputTensor(0).numBytes())
        require(embedOut.capacity() == 96 * 4)
        for ((index, position) in listOf(0.18, 0.34, 0.50, 0.66, 0.82).withIndex()) {
            val center = ((leading + samples.size * position) / 160).toInt()
            val start = (center - 38).coerceIn(0, count - 76)
            embedIn.clear()
            for (i in 0 until 76 * 32) embedIn.putFloat(rows[start * 32 + i])
            embedIn.rewind()
            embedOut.clear()
            embed.run(embedIn, embedOut)
            embedOut.rewind()
            val vector = FloatArray(96) { embedOut.float }
            val norm = sqrt(vector.sumOf { it.toDouble() * it }).coerceAtLeast(1e-9)
            for (i in vector.indices) out[index * 96 + i] = (vector[i] / norm).toFloat()
        }
        return out
    }

    override fun close() {
        embed.close()
        mel.close()
    }

    companion object {
        private const val WAVE_SIZE = 25600

        private fun floats(bytes: Int) = ByteBuffer.allocateDirect(bytes).order(ByteOrder.nativeOrder())

        private fun interpreter(context: Context, name: String): Interpreter {
            val bytes = context.assets.open("personal_wake/$name.tflite").use { it.readBytes() }
            val model = floats(bytes.size).put(bytes)
            model.rewind()
            return Interpreter(model, Interpreter.Options().setNumThreads(1))
        }
    }
}

