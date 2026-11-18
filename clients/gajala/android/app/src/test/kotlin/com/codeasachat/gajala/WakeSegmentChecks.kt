package com.codeasachat.gajala

import java.nio.ByteBuffer
import java.nio.ByteOrder

private fun recording(speechFrames: Int, background: Int, speech: Int, tail: Int): ByteArray {
    val signal = ByteBuffer.allocate((10 + speechFrames + tail) * 640).order(ByteOrder.LITTLE_ENDIAN)
    repeat(10 + speechFrames + tail) { frame ->
        repeat(320) { sample ->
            val level = if (frame in 10 until 10 + speechFrames) speech else background
            signal.putShort((if (sample % 2 == 0) level else -level).toShort())
        }
    }
    return signal.array()
}

fun main() {
    val pcm = recording(30, 430, 4500, 12)
    check(WakeSegments.sample(pcm).size == 30 * 320)
    val segments = WakeSegments()
    var hits = 0
    pcm.asList().chunked(640).forEach { frame ->
        segments.accept(frame.toByteArray(), frame.size) { hits++ }
    }
    check(hits == 1)
    check(runCatching { WakeSegments.sample(recording(30, 430, 430, 12)) }.isFailure)
    check(runCatching { WakeSegments.sample(recording(30, 100, 32700, 12)) }.isFailure)
    check(runCatching { WakeSegments.sample(recording(30, 100, 4500, 0)) }.isFailure)
    println("Wake segmentation and per-clip quality checks passed.")
}
