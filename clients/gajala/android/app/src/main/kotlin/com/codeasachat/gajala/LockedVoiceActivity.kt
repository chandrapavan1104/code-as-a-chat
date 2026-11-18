package com.codeasachat.gajala

import android.Manifest
import android.app.Activity
import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.media.AudioManager
import android.media.ToneGenerator
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.telecom.TelecomManager
import android.telephony.TelephonyManager
import android.view.Gravity
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID

internal object LockedVoice {
    fun prefs(context: Context) = context.getSharedPreferences("gajala_locked_voice", Context.MODE_PRIVATE)
    fun locked(context: Context): Boolean = context.getSystemService(KeyguardManager::class.java).let {
        it.isKeyguardLocked || it.isDeviceLocked
    }
    fun enabled(context: Context) = prefs(context).getBoolean("enabled", false)
    fun unsupported(context: Context): String? = when {
        GajalaVoiceInteractionService.instance == null -> "Select Gajala as Android’s default assistant first."
        !WakeWordService.hasMic(context) -> "Allow Gajala to use the microphone first."
        Build.VERSION.SDK_INT < 31 || !SpeechRecognizer.isOnDeviceRecognitionAvailable(context) ->
            "This phone does not offer on-device speech recognition. Install an offline recognition engine or open Gajala after unlocking."
        else -> null
    }

    fun contacts(context: Context) = prefs(context).all.mapNotNull { (key, raw) ->
        if (key.startsWith("contact:") && raw is String) key.removePrefix("contact:") to raw else null
    }.toMap()

    fun log(context: Context, request: String, reply: String) {
        val prefs = prefs(context)
        val previous = JSONArray(prefs.getString("journal", "[]"))
        val entries = JSONArray()
        for (i in (previous.length() - 63).coerceAtLeast(0) until previous.length()) {
            entries.put(previous.getJSONObject(i))
        }
        entries.put(JSONObject().put("id", "locked-voice-${UUID.randomUUID()}")
            .put("time", System.currentTimeMillis()).put("request", request.take(300)).put("reply", reply.take(400)))
        prefs.edit().putString("journal", entries.toString()).commit()
    }

    fun acknowledge(context: Context, id: String) {
        val prefs = prefs(context)
        val previous = JSONArray(prefs.getString("journal", "[]"))
        val entries = JSONArray()
        for (i in 0 until previous.length()) {
            val entry = previous.getJSONObject(i)
            if (entry.getString("id") != id) entries.put(entry)
        }
        prefs.edit().putString("journal", entries.toString()).commit()
    }
}

/** This surface has no Flutter engine, server credentials, or project tools. */
class LockedVoiceActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private var recognizer: SpeechRecognizer? = null
    private var speaker: TextToSpeech? = null
    private var tone: ToneGenerator? = null
    private lateinit var status: TextView
    private var active = false
    private var turn = 0
    private var speechId = ""
    private var pendingCall: LockedCallOffer? = null
    private var confirmBy = 0L
    private var resumed = false

    private val lockChanges = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == Intent.ACTION_SCREEN_OFF || !LockedVoice.locked(context)) end()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (savedInstanceState != null || !LockedVoice.locked(this) ||
            !LockedVoice.enabled(this) || LockedVoice.unsupported(this) != null
        ) {
            finish()
            return
        }
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        window.addFlags(WindowManager.LayoutParams.FLAG_SECURE or WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        status = TextView(this).apply { text = "Getting ready…"; textSize = 20f; gravity = Gravity.CENTER }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setPadding(40, 40, 40, 40)
            addView(status)
            addView(Button(this@LockedVoiceActivity).apply {
                text = "Stop listening"
                setOnClickListener { end() }
            })
        })
        active = true
        registerReceiver(lockChanges, IntentFilter().apply {
            addAction(Intent.ACTION_USER_PRESENT)
            addAction(Intent.ACTION_SCREEN_OFF)
        })
        WakeWordService.instance?.pause("locked_voice")
        handler.postDelayed({ if (eligible()) end() }, 110_000)
        speaker = TextToSpeech(this) { code ->
            handler.post {
                if (!eligible()) return@post
                if (code != TextToSpeech.SUCCESS || !configureVoice()) {
                    showUnavailable("Install an offline English voice in Gajala’s Voice settings.")
                } else beginListening(20_000)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        resumed = true
    }

    private fun eligible(): Boolean =
        active && !isFinishing && LockedVoice.enabled(this) && LockedVoice.locked(this) &&
            LockedVoice.unsupported(this) == null

    private fun configureVoice(): Boolean {
        val tts = speaker ?: return false
        val saved = LockedVoice.prefs(this).getString("voice", null)
        val offline = tts.voices.orEmpty().filter { voice ->
            !voice.isNetworkConnectionRequired && "notInstalled" !in voice.features && voice.locale.language == "en"
        }
        val voice = offline.firstOrNull { it.name == saved }
            ?: offline.sortedWith(compareByDescending<android.speech.tts.Voice> { it.locale.country == "IN" }
                .thenByDescending { it.quality }).firstOrNull() ?: return false
        if (tts.setVoice(voice) != TextToSpeech.SUCCESS) return false
        tts.setSpeechRate(0.94f)
        tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
            override fun onStart(id: String) = Unit
            override fun onDone(id: String) { handler.post { afterSpeech(id, true) } }
            @Deprecated("Required by Android")
            override fun onError(id: String) { handler.post { afterSpeech(id, false) } }
        })
        return true
    }

    private var nextSpeech: (() -> Unit)? = null

    private fun afterSpeech(id: String, success: Boolean) {
        if (id != speechId || !eligible()) return
        val next = nextSpeech
        nextSpeech = null
        if (success) next?.invoke() else showUnavailable("The offline voice could not speak.")
    }

    private fun speak(text: String, after: () -> Unit) {
        if (!eligible()) { end(); return }
        status.text = text
        speechId = UUID.randomUUID().toString()
        val id = speechId
        nextSpeech = after
        if (speaker?.speak(text, TextToSpeech.QUEUE_FLUSH, null, id) != TextToSpeech.SUCCESS) {
            afterSpeech(id, false)
        }
        handler.postDelayed({ if (speechId == id && nextSpeech != null) afterSpeech(id, false) }, 10_000)
    }

    private fun beginListening(window: Long) {
        if (!eligible()) { end(); return }
        val expected = ++turn
        val deadline = SystemClock.elapsedRealtime() + window
        status.text = if (pendingCall == null) "Listening…" else "Say yes to confirm"
        val currentTone = runCatching { ToneGenerator(AudioManager.STREAM_MUSIC, 30) }.getOrNull()
        tone?.release()
        tone = currentTone
        currentTone?.startTone(ToneGenerator.TONE_PROP_BEEP, 100)
        handler.postDelayed({
            if (eligible() && turn == expected) attempt(expected, deadline, false)
        }, 150)
    }

    @Suppress("MissingPermission")
    private fun attempt(expected: Int, deadline: Long, startedSpeech: Boolean) {
        if (!eligible() || expected != turn || Build.VERSION.SDK_INT < 31) { end(); return }
        if (SystemClock.elapsedRealtime() >= deadline) { end(); return }
        closeRecognizer()
        var heardSpeech = startedSpeech
        val instance = try {
            SpeechRecognizer.createOnDeviceSpeechRecognizer(this)
        } catch (_: Exception) {
            showUnavailable("On-device speech recognition is unavailable.")
            return
        }
        recognizer = instance
        val initialTimeout = Runnable {
            if (recognizer === instance && !heardSpeech) end()
        }
        handler.postDelayed(initialTimeout, (deadline - SystemClock.elapsedRealtime()).coerceAtLeast(0))
        handler.postDelayed({
            if (recognizer === instance) end()
        }, 60_000)
        fun current() = eligible() && expected == turn && recognizer === instance
        instance.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) = Unit
            override fun onBeginningOfSpeech() {
                if (current() && pendingCall == null) {
                    heardSpeech = true
                    handler.removeCallbacks(initialTimeout)
                }
            }
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(buffer: ByteArray?) = Unit
            override fun onEndOfSpeech() = Unit
            override fun onPartialResults(results: Bundle?) = Unit
            override fun onEvent(eventType: Int, params: Bundle?) = Unit
            override fun onResults(results: Bundle?) {
                if (!current()) return
                val text = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()?.trim().orEmpty()
                if (text.isBlank()) {
                    retry()
                } else {
                    // Only final transcripts can dispatch commands or confirmations.
                    closeRecognizer()
                    accept(text)
                }
            }

            override fun onError(error: Int) {
                if (!current()) return
                if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) retry()
                else showUnavailable("Offline speech recognition failed. Check the offline English (India) language pack.")
            }

            private fun retry() {
                closeRecognizer()
                handler.postDelayed({
                    if (expected == turn && eligible()) attempt(expected, deadline, false)
                }, 200)
            }
        })
        try {
            instance.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "en-IN")
                .putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1))
        } catch (_: Exception) {
            showUnavailable("Android did not open the microphone while locked.")
        }
    }

    private fun accept(raw: String) {
        if (!eligible()) { end(); return }
        val text = VoiceCommand.normalize(raw)
        if (text in setOf("stop listening", "stop", "cancel", "bye", "goodbye")) { end(); return }
        pendingCall?.let { call ->
            pendingCall = null
            if (!call.confirmed(text, SystemClock.elapsedRealtime(), confirmBy, LockedVoice.contacts(this))) { speak("No call was made.") { end() }; return }
            speak("Sending the call request.") {
                if (!eligible() || SystemClock.elapsedRealtime() > confirmBy) {
                    speak("Confirmation expired. No call was made.") { end() }
                } else {
                    val reply = requestCall(call)
                    LockedVoice.log(this, "Call ${call.alias} · confirmed", reply)
                    if (reply == "Call requested.") end() else speak(reply) { end() }
                }
            }
            return
        }
        if (text.startsWith("call ")) {
            val offer = LockedCallOffer.from(text, LockedVoice.contacts(this))
            if (offer == null) {
                speak("Unlock and ask again, or choose that contact in Locked call contacts.") { end() }
                return
            }
            if (checkSelfPermission(Manifest.permission.CALL_PHONE) != PackageManager.PERMISSION_GRANTED) {
                speak("Phone permission is required. Unlock and open Voice settings.") { end() }
                return
            }
            pendingCall = offer
            speak("Call ${offer.alias}?") {
                confirmBy = SystemClock.elapsedRealtime() + 12_000
                beginListening(12_000)
            }
            return
        }
        val command = VoiceCommand.parse(text)
        if (command == null) {
            speak("Unlock your phone and ask again.") { end() }
            return
        }
        EverydayVoice(this).execute(command, ::eligible) { reply ->
            if (eligible()) {
                LockedVoice.log(this, text, reply)
                speak(reply) { beginListening(12_000) }
            }
        }
    }

    @Suppress("MissingPermission")
    private fun requestCall(call: LockedCallOffer): String {
        if (!eligible() || LockedVoice.contacts(this)[call.alias] != call.number) return "No call was made."
        if (checkSelfPermission(Manifest.permission.CALL_PHONE) != PackageManager.PERMISSION_GRANTED)
            return "Phone permission is required. No call was made."
        return try {
            val telephony = getSystemService(TelephonyManager::class.java)
            if (telephony.isEmergencyNumber(call.number)) return "Use the phone’s emergency dialer."
            getSystemService(TelecomManager::class.java).placeCall(Uri.fromParts("tel", call.number, null), Bundle())
            "Call requested."
        } catch (_: Exception) {
            "Android refused the call request."
        }
    }

    private fun showUnavailable(message: String) {
        pendingCall = null
        ++turn
        closeRecognizer()
        status.text = message
        handler.postDelayed({ end() }, 5_000)
    }

    private fun closeRecognizer() {
        val old = recognizer
        recognizer = null
        runCatching { old?.cancel() }
        runCatching { old?.destroy() }
    }

    private fun end() {
        active = false
        pendingCall = null
        nextSpeech = null
        closeRecognizer()
        speaker?.stop()
        handler.removeCallbacksAndMessages(null)
        if (!isFinishing) finish()
    }

    override fun onPause() {
        if (resumed) end()
        super.onPause()
    }

    override fun onDestroy() {
        end()
        runCatching { unregisterReceiver(lockChanges) }
        speaker?.shutdown()
        tone?.release()
        WakeWordService.instance?.unpause("locked_voice")
        super.onDestroy()
    }
}
