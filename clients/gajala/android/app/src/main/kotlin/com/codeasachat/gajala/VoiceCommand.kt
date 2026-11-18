package com.codeasachat.gajala

import java.util.Locale
import kotlin.math.roundToInt

internal data class VoiceCommand(val kind: String, val label: String = "", val value: Int = 0) {
    companion object {
        private val numbers = ("zero one two three four five six seven eight nine ten eleven twelve " +
            "thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split(' ')
        private val tens = "twenty thirty forty fifty sixty seventy eighty ninety".split(' ')

        fun normalize(text: String): String = text.lowercase(Locale.ROOT)
            .replace('’', '\'').replace('-', ' ').trim().trimEnd('.', '!', '?').replace(Regex("\\s+"), " ")

        private fun number(spoken: String): Int? {
            val raw = spoken.removePrefix("a ").removePrefix("an ")
            raw.toIntOrNull()?.let { return it }
            if (raw == "a" || raw == "an") return 1
            if (raw == "one hundred") return 100
            numbers.indexOf(raw).takeIf { it >= 0 }?.let { return it }
            val parts = raw.split(' ')
            val ten = tens.indexOf(parts.first()).takeIf { it >= 0 } ?: return null
            val unit = if (parts.size == 2) numbers.indexOf(parts[1]) else 0
            if (parts.size > 2 || unit !in 0..9) return null
            return (ten + 2) * 10 + unit
        }

        fun parse(raw: String): VoiceCommand? {
            val text = normalize(raw)
            when (text) {
                "time", "what time is it", "what is the time", "tell me the time" -> return VoiceCommand("time")
                "what's my battery", "whats my battery", "what is my battery", "battery", "battery level",
                "what is my battery level", "how much battery is left", "is my phone charging" -> return VoiceCommand("battery")
                "pause music", "pause playback", "pause the music" -> return VoiceCommand("media", "pause")
                "resume playback", "resume music", "resume the music", "resume" -> return VoiceCommand("media", "play")
                "next track", "next song", "skip this song", "skip track" -> return VoiceCommand("media", "next")
                "previous track", "previous song", "last song" -> return VoiceCommand("media", "previous")
                "flashlight on", "torch on", "turn on the flashlight", "turn on flashlight", "turn on the torch",
                "turn on torch" -> return VoiceCommand("torch", value = 1)
                "flashlight off", "torch off", "turn off the flashlight", "turn off flashlight", "turn off the torch",
                "turn off torch" -> return VoiceCommand("torch")
            }
            Regex("^set (?:the )?(?:media )?volume to (.+?)\\s*(?:percent|%)$").matchEntire(text)?.let {
                return VoiceCommand("volume", value = number(it.groupValues[1].trim()) ?: -1)
            }
            Regex("^set (.+?) (seconds?|minutes?|hours?) (?:([a-z0-9 ]{1,40}) )?timer(?: (?:for|called|named) ([a-z0-9 ]{1,40}))?$")
                .matchEntire(text)?.let {
                    val multiplier = when (it.groupValues[2].first()) { 'h' -> 3600; 'm' -> 60; else -> 1 }
                    val count = number(it.groupValues[1]) ?: return VoiceCommand("timer_start")
                    val label = it.groupValues[3].ifEmpty { it.groupValues[4] }
                    return VoiceCommand("timer_start", label, count.coerceIn(0, 86401) * multiplier)
                }
            val leadingTimer = Regex("^set (?:a |an |the )?timer for (.+?) (seconds?|minutes?|hours?)(?: (?:called|named) ([a-z0-9 ]{1,40}))?$")
                .matchEntire(text)
            if (leadingTimer != null) {
                val multiplier = when (leadingTimer.groupValues[2].first()) { 'h' -> 3600; 'm' -> 60; else -> 1 }
                val count = number(leadingTimer.groupValues[1]) ?: 0
                return VoiceCommand("timer_start", leadingTimer.groupValues[3], count.coerceIn(0, 86401) * multiplier)
            }
            Regex("^cancel (?:the |my )?(?:([a-z0-9 ]{1,40}) )?timer$").matchEntire(text)?.let {
                return VoiceCommand("timer_cancel", it.groupValues[1])
            }
            Regex("^how much time is left(?: on (?:the |my )?([a-z0-9 ]{1,40}))?$").matchEntire(text)?.let {
                return VoiceCommand("timer_read", it.groupValues[1].removeSuffix(" timer").let { label ->
                    if (label == "timer") "" else label
                })
            }
            return null
        }
    }
}

internal data class LockedCallOffer(val alias: String, val number: String) {
    fun confirmed(text: String, now: Long, expires: Long, contacts: Map<String, String>) =
        VoiceCommand.normalize(text) == "yes" && expires > 0 && now <= expires && contacts[alias] == number

    companion object {
        fun from(text: String, contacts: Map<String, String>): LockedCallOffer? {
            val normalized = VoiceCommand.normalize(text)
            if (!normalized.startsWith("call ")) return null
            val alias = normalized.removePrefix("call ")
            return contacts[alias]?.let { LockedCallOffer(alias, it) }
        }
    }
}

internal fun mediaVolumeStep(min: Int, max: Int, percent: Int): Int =
    min + ((max - min) * percent.coerceIn(0, 100) / 100.0).roundToInt()
