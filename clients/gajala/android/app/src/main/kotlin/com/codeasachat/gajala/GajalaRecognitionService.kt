package com.codeasachat.gajala

import android.content.ComponentName
import android.content.Intent
import android.os.Bundle
import android.os.RemoteException
import android.speech.RecognitionListener
import android.speech.RecognitionService
import android.speech.SpeechRecognizer

/**
 * Selecting a digital-assistant app also makes its recognition service the
 * phone's default speech recognizer, so every app's voice typing — including
 * Gajala's own mic — would route here. Gajala has no speech engine of its own,
 * so this forwards each request to the best other installed recognizer
 * (Google's when present) and relays its callbacks unchanged.
 */
class GajalaRecognitionService : RecognitionService() {
    private var delegate: SpeechRecognizer? = null

    override fun onStartListening(recognizerIntent: Intent, listener: Callback) {
        release()
        val target = pickDelegate()
        if (target == null) {
            safely { listener.error(SpeechRecognizer.ERROR_CLIENT) }
            return
        }
        delegate = SpeechRecognizer.createSpeechRecognizer(this, target).apply {
            setRecognitionListener(Relay(listener))
            startListening(recognizerIntent)
        }
    }

    override fun onStopListening(listener: Callback) {
        delegate?.stopListening()
    }

    override fun onCancel(listener: Callback) {
        release()
    }

    override fun onDestroy() {
        release()
        super.onDestroy()
    }

    private fun release() {
        delegate?.destroy()
        delegate = null
    }

    private fun pickDelegate(): ComponentName? {
        val services = packageManager
            .queryIntentServices(Intent(SERVICE_INTERFACE), 0)
            .map { it.serviceInfo }
            .filter { it.packageName != packageName }
        val preferred = services.firstOrNull { it.packageName == GOOGLE } ?: services.firstOrNull()
        return preferred?.let { ComponentName(it.packageName, it.name) }
    }

    private class Relay(private val out: Callback) : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = safely { out.readyForSpeech(params ?: Bundle()) }
        override fun onBeginningOfSpeech() = safely { out.beginningOfSpeech() }
        override fun onRmsChanged(rmsdB: Float) = safely { out.rmsChanged(rmsdB) }
        override fun onBufferReceived(buffer: ByteArray?) = safely { buffer?.let { out.bufferReceived(it) } }
        override fun onEndOfSpeech() = safely { out.endOfSpeech() }
        override fun onError(error: Int) = safely { out.error(error) }
        override fun onResults(results: Bundle?) = safely { out.results(results ?: Bundle()) }
        override fun onPartialResults(partialResults: Bundle?) =
            safely { out.partialResults(partialResults ?: Bundle()) }
        override fun onEvent(eventType: Int, params: Bundle?) {}
    }

    companion object {
        private const val GOOGLE = "com.google.android.googlequicksearchbox"

        // The calling app may have gone away mid-recognition.
        private inline fun safely(block: () -> Unit) {
            try {
                block()
            } catch (_: RemoteException) {
            }
        }
    }
}
