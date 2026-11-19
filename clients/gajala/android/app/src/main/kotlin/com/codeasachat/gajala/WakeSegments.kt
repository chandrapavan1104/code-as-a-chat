package com.codeasachat.gajala

import kotlin.math.abs
import kotlin.math.sqrt

/** Bounds inference to speech segments and uses the same endpoints during enrollment. */
internal class WakeSegments {
    private val frame = ShortArray(320)
    private val segment = ShortArray(MAX_WORD + 320 * 9)
    private var frameSize = 0
    private var size = 0
    private var quiet = 0
    private var tooLong = false
    private var warmup = 5
    private var noiseRms = 80.0

    fun accept(bytes: ByteArray, length: Int, onWord: (ShortArray) -> Unit) {
        for (i in 0 until length - 1 step 2) {
            frame[frameSize++] = (((bytes[i + 1].toInt() shl 8) or (bytes[i].toInt() and 255))).toShort()
            if (frameSize != frame.size) continue
            frameSize = 0
            val rms = sqrt(frame.sumOf { it.toLong() * it }.toDouble() / frame.size)
            if (warmup > 0) {
                noiseRms = if (warmup == 5) rms else minOf(noiseRms, rms)
                warmup--
                continue
            }
            val voiced = rms >= maxOf(180.0, noiseRms * 2.3)
            if (size == 0 && !voiced) {
                noiseRms = 0.96 * noiseRms + 0.04 * rms
                continue
            }
            if (size + frame.size <= segment.size && !tooLong) {
                frame.copyInto(segment, size)
                size += frame.size
            } else {
                tooLong = true
            }
            quiet = if (voiced) 0 else quiet + 1
            if (quiet < 8) continue
            val end = size - quiet * frame.size
            if (!tooLong && end in MIN_WORD..MAX_WORD) {
                onWord(segment.copyOf(end))
            }
            resetSegment()
        }
    }

    fun reset() {
        frame.fill(0)
        frameSize = 0
        warmup = 5
        noiseRms = 80.0
        resetSegment()
    }

    private fun resetSegment() {
        segment.fill(0)
        size = 0
        quiet = 0
        tooLong = false
    }

    companion object {
        const val MIN_WORD = 5600
        const val MAX_WORD = 20800

        fun sample(bytes: ByteArray): ShortArray {
            require(bytes.size >= 2)
            var clipped = 0
            for (i in 0 until bytes.size - 1 step 2) {
                val value = (bytes[i + 1].toInt() shl 8) or (bytes[i].toInt() and 255)
                if (abs(value) >= 32700) clipped++
            }
            require(clipped < bytes.size / 200) { "That recording clipped. Move the phone slightly farther away." }
            val words = mutableListOf<ShortArray>()
            WakeSegments().accept(bytes, bytes.size) { words.add(it) }
            if (words.size != 1) words.forEach { it.fill(0) }
            require(words.size == 1) { "I need one complete “Gajala.” Pause before and after the word, then try this recording again." }
            return words.single()
        }
    }
}
