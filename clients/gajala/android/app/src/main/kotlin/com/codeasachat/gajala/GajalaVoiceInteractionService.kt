package com.codeasachat.gajala

import android.content.Intent
import android.os.PowerManager
import android.os.Bundle
import android.service.voice.VoiceInteractionService
import android.service.voice.VoiceInteractionSession
import android.service.voice.VoiceInteractionSessionService

/**
 * Makes Gajala selectable as Android's "Digital assistant app". The system
 * binds this while Gajala holds the assistant role; it needs no logic of its
 * own — the work happens in [GajalaVoiceSession].
 */
class GajalaVoiceInteractionService : VoiceInteractionService() {
    // Set only while Gajala holds the assistant role and the system has bound
    // us; "Hey Gajala" then opens voice mode through showSession().
    override fun onReady() {
        super.onReady()
        instance = this
    }

    override fun onLaunchVoiceAssistFromKeyguard() {
        if (LockedVoice.enabled(this) && LockedVoice.unsupported(this) == null) showSession(Bundle(), 0)
    }

    override fun onShutdown() {
        instance = null
        super.onShutdown()
    }

    companion object {
        @Volatile var instance: GajalaVoiceInteractionService? = null
    }
}

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
        val locked = LockedVoice.locked(context) || !context.getSystemService(PowerManager::class.java).isInteractive
        if (locked && (!LockedVoice.enabled(context) || LockedVoice.unsupported(context) != null)) {
            hide()
            return
        }
        val target = if (locked) LockedVoiceActivity::class.java else MainActivity::class.java
        val intent = Intent(context, target).apply {
            if (!locked) action = MainActivity.ACTION_VOICE
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        }
        try {
            startAssistantActivity(intent)
        } catch (error: RuntimeException) {
            android.util.Log.w("GajalaVoiceSession", "Android refused the assistant surface", error)
        } finally {
            hide()
        }
    }
}
