package com.codeasachat.gajala

/** JVM checks for the same parser and confirmation predicates used while locked. */
fun main() {
    check(VoiceCommand.parse("Set a five-minute pasta timer") == VoiceCommand("timer_start", "pasta", 300))
    check(VoiceCommand.parse("Set a timer for 30 seconds named tea") == VoiceCommand("timer_start", "tea", 30))
    check(VoiceCommand.parse("Cancel the pasta timer") == VoiceCommand("timer_cancel", "pasta"))
    check(VoiceCommand.parse("How much time is left on pasta?") == VoiceCommand("timer_read", "pasta"))
    check(VoiceCommand.parse("Set media volume to thirty five percent") == VoiceCommand("volume", value = 35))
    check(VoiceCommand.parse("What’s my battery")?.kind == "battery")
    check(VoiceCommand.parse("Next track") == VoiceCommand("media", "next"))
    check(mediaVolumeStep(0, 15, 30) == 5)
    check(mediaVolumeStep(1, 16, 0) == 1)
    for (utterance in listOf("open my chats", "turn on the bedroom fan", "read my messages",
        "let me in", "buy stock", "play Rockstar songs", "pause music and call PSS")) {
        check(VoiceCommand.parse(utterance) == null) { utterance }
    }
    val contacts = mapOf("pss" to "+12025550123", "pss work" to "+12025550124")
    val offer = checkNotNull(LockedCallOffer.from("Call PSS!", contacts))
    check(LockedCallOffer.from("call p", contacts) == null)
    check(LockedCallOffer.from("call +12025550123", contacts) == null)
    check(offer.confirmed("Yes.", 100, 101, contacts))
    check(!offer.confirmed("yes maybe", 100, 101, contacts))
    check(!offer.confirmed("yes", 102, 101, contacts))
    check(!offer.confirmed("yes", 100, 101, emptyMap()))
    println("Voice command and locked confirmation checks passed.")
}
