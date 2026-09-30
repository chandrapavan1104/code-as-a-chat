package com.codeasachat.gajala

import android.content.Intent
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService

/**
 * Makes Gajala selectable as Android's "Digital assistant app". The system
 * binds this while Gajala holds the assistant role; it needs no logic of its
 * own — the work happens in [GajalaVoiceSession].
 */
class GajalaVoiceInteractionService : VoiceInteractionService()

class GajalaVoiceSessionService : VoiceInteractionSessionService() {
    override fun onNewSession(args: Bundle?): VoiceInteractionSession = GajalaVoiceSession(this)
}

/**
 * Long-press home / the assistant button: open Gajala's voice sheet, then get
 * out of the way. The Flutter app owns the whole conversation UI, so this
 * session never draws anything itself.
 */
class GajalaVoiceSession(service: VoiceInteractionSessionService) : VoiceInteractionSession(service) {
    override fun onShow(args: Bundle?, showFlags: Int) {
        super.onShow(args, showFlags)
        val intent = Intent(context, MainActivity::class.java).apply {
            action = MainActivity.ACTION_VOICE
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        }
        startAssistantActivity(intent)
        hide()
    }
}
