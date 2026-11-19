package com.codeasachat.gajala

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import io.flutter.embedding.android.FlutterFragmentActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

// FragmentActivity: required by local_auth's biometric prompt (app lock).
class MainActivity : FlutterFragmentActivity() {
    private var channel: MethodChannel? = null
    private var pendingAssist = false
    private var pendingEnable: MethodChannel.Result? = null
    private var device: DeviceActions? = null
    private var phoneActions: VoiceActions? = null
    private var wakeEnrollment: WakeEnrollment? = null
    private lateinit var phoneImport: PhoneImport

    override fun onCreate(savedInstanceState: Bundle?) {
        // Android attaches the Activity context before onCreate. Register the
        // picker before Flutter configures its channel inside super.onCreate.
        phoneImport = PhoneImport(this)
        super.onCreate(savedInstanceState)
        // Only a fresh launch counts; a recreated activity (rotation, process
        // restore) must not reopen the voice sheet.
        if (savedInstanceState == null) pendingAssist = isAssist(intent)
        phoneImport.accept(intent)
    }

    override fun onResume() {
        super.onResume()
        visible = this
        // Android 14+ only allows starting the microphone service from the
        // foreground, so every app open is the moment to (re)start it.
        if (WakeWordService.isEnabled(this)) WakeWordService.start(this)
    }

    override fun onPause() {
        wakeEnrollment?.cancel()
        if (visible === this) visible = null
        super.onPause()
    }

    override fun onDestroy() {
        // Swiping Gajala away mid-voice-turn kills the Dart side that would have
        // released its mic hold; don't leave hands-free paused forever.
        if (isFinishing) WakeWordService.hold(false)
        phoneActions?.cancelPending()
        phoneActions = null
        super.onDestroy()
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val messenger = flutterEngine.dartExecutor.binaryMessenger
        phoneImport.connect(messenger)
        channel = MethodChannel(messenger, "gajala/assist").apply {
            setMethodCallHandler { call, result ->
                if (call.method == "consumeLaunch") {
                    result.success(pendingAssist)
                    pendingAssist = false
                } else {
                    result.notImplemented()
                }
            }
        }
        device = DeviceActions(this).also {
            MethodChannel(messenger, "gajala/device").setMethodCallHandler(it)
        }
        MethodChannel(messenger, "gajala/wakeword/control").setMethodCallHandler { call, result ->
            when (call.method) {
                "state" -> result.success(wakeWordState())
                "phrase" -> result.success(
                    if (PersonalWakeProfile.load(this).isNotEmpty()) "Gajala" else WakeWordService.phrase(this))
                "calibration" -> result.success(WakeWordService.calibration(this).let { listOf(it.first, it.second) })
                "enable" -> enableWakeWord(result)
                "disable" -> {
                    WakeWordService.setEnabled(this, false)
                    WakeWordService.stop(this)
                    result.success(null)
                }
                "pause" -> {
                    WakeWordService.hold(true)
                    result.success(null)
                }
                "resume" -> {
                    WakeWordService.hold(false)
                    result.success(null)
                }
                "enrollmentCapture" -> {
                    val enrollment = wakeEnrollment ?: WakeEnrollment(this).also { wakeEnrollment = it }
                    enrollment.capture(result)
                }
                "enrollmentCancel" -> {
                    wakeEnrollment?.cancel()
                    result.success(null)
                }
                "enrollmentModelDir" -> result.success(
                    (wakeEnrollment ?: WakeEnrollment(this).also { wakeEnrollment = it }).modelDir())
                "enrollmentSave" -> {
                    val args = call.arguments as Map<*, *>
                    (wakeEnrollment ?: WakeEnrollment(this).also { wakeEnrollment = it }).save(
                        (args["threshold"] as Number).toDouble(),
                        (args["boost"] as Number).toDouble())
                    result.success(null)
                }
                "enrollmentReset" -> {
                    (wakeEnrollment ?: WakeEnrollment(this).also { wakeEnrollment = it }).reset()
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
        MethodChannel(messenger, "gajala/wakeword/personal").setMethodCallHandler { call, result ->
            if (LockedVoice.locked(this) || visible !== this) {
                result.error("locked", "Open Gajala while unlocked to manage your wake word.", null)
                return@setMethodCallHandler
            }
            when (call.method) {
                "examples" -> result.success(PersonalWakeProfile.load(this).map { row -> row.map(Float::toDouble) })
                "capture" -> {
                    val enrollment = wakeEnrollment ?: WakeEnrollment(this).also { wakeEnrollment = it }
                    enrollment.capture(object : MethodChannel.Result {
                        override fun success(capture: Any?) {
                            val pcm = capture as ByteArray
                            Thread {
                                val outcome = runCatching {
                                    val speech = WakeSegments.sample(pcm)
                                    try {
                                        PersonalWakeEncoder(applicationContext).use { it.features(speech) }
                                    } finally { speech.fill(0) }
                                }
                                pcm.fill(0)
                                runOnUiThread {
                                    if (LockedVoice.locked(this@MainActivity) || visible !== this@MainActivity) {
                                        result.error("cancelled", "Recording cancelled.", null)
                                    } else outcome.fold(
                                        { row -> result.success(row.map(Float::toDouble)) },
                                        { error -> result.error("sample", error.message ?: "Could not use this recording.", null) }
                                    )
                                }
                            }.start()
                        }
                        override fun error(code: String, message: String?, details: Any?) =
                            result.error(code, message, details)
                        override fun notImplemented() = result.notImplemented()
                    })
                }
                "save" -> {
                    val examples = (call.arguments as? List<*>)?.mapNotNull { row ->
                        (row as? List<*>)?.mapNotNull { (it as? Number)?.toFloat() }?.toFloatArray()
                    }.orEmpty()
                    result.success(PersonalWakeProfile.save(this, examples))
                }
                "reset" -> result.success(PersonalWakeProfile.reset(this))
                "match" -> {
                    val example = (call.arguments as? List<*>)?.mapNotNull { (it as? Number)?.toFloat() }?.toFloatArray()
                    result.success(example != null && example.size == PersonalWakeProfile.DIMENSIONS &&
                        PersonalWakeProfile.score(example, PersonalWakeProfile.load(this)) >= PersonalWakeProfile.THRESHOLD)
                }
                else -> result.notImplemented()
            }
        }
        MethodChannel(messenger, "gajala/voice/phone").setMethodCallHandler { call, result ->
            if (LockedVoice.locked(this) || visible !== this) {
                result.error("locked", "Unlock your phone and ask again.", null)
                return@setMethodCallHandler
            }
            when (call.method) {
                "unlocked" -> result.success(true)
                "setup" -> {
                    startActivity(Intent(this, VoiceSetupActivity::class.java))
                    result.success(null)
                }
                "execute" -> {
                    val command = VoiceCommand.parse(call.arguments as? String ?: "")
                    if (command == null) result.success(null)
                    else EverydayVoice(this).execute(command, {
                        visible === this && !LockedVoice.locked(this)
                    }) { reply -> result.success(reply) }
                }
                "saveVoice" -> {
                    LockedVoice.prefs(this).edit().putString("voice", call.arguments as? String).apply()
                    result.success(null)
                }
                "journal" -> result.success(LockedVoice.prefs(this).getString("journal", "[]"))
                "acknowledge" -> {
                    (call.arguments as? String)?.let { LockedVoice.acknowledge(this, it) }
                    result.success(null)
                }
                else -> result.notImplemented()
            }
        }
        phoneActions = VoiceActions(this)
        MethodChannel(messenger, "gajala/phone").setMethodCallHandler { call, result ->
            if (LockedVoice.locked(this)) {
                result.error("locked", "Unlock your phone and ask again.", null)
            } else if (phoneActions?.handle(call, result) != true) result.notImplemented()
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        phoneImport.accept(intent)
        if (isAssist(intent)) openVoice()
    }

    fun openVoice() {
        channel?.invokeMethod("assist", null)
    }

    private fun wakeWordState(): String {
        val service = WakeWordService.instance
        return when {
            !WakeWordService.supported -> "unsupported"
            !WakeWordService.isEnabled(this) -> "off"
            service?.listening == true -> "listening"
            else -> "paused"
        }
    }

    private fun enableWakeWord(result: MethodChannel.Result) {
        if (!WakeWordService.supported) {
            result.success("Hands-free needs a 64-bit ARM phone.")
            return
        }
        val wanted = buildList {
            if (!WakeWordService.hasMic(this@MainActivity)) add(Manifest.permission.RECORD_AUDIO)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
            ) add(Manifest.permission.POST_NOTIFICATIONS)
        }
        if (wanted.isEmpty()) {
            finishEnable(result)
            return
        }
        pendingEnable?.success("Cancelled.")
        pendingEnable = result
        requestPermissions(wanted.toTypedArray(), WAKEWORD_PERMISSIONS)
    }

    private fun finishEnable(result: MethodChannel.Result) {
        if (!WakeWordService.hasMic(this)) {
            result.success("Gajala needs the microphone permission to hear “Hey Gajala”.")
            return
        }
        WakeWordService.setEnabled(this, true)
        WakeWordService.start(this)
        result.success(null)
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (device?.onPermissionResult(requestCode) == true) return
        if (requestCode == WAKEWORD_PERMISSIONS) {
            pendingEnable?.let(::finishEnable)
            pendingEnable = null
            return
        }
        phoneActions?.onPermissionResult(requestCode, grantResults)
    }

    private fun isAssist(intent: Intent?): Boolean =
        intent?.action == Intent.ACTION_ASSIST || intent?.action == ACTION_VOICE

    companion object {
        const val ACTION_VOICE = "com.codeasachat.gajala.action.VOICE"
        private const val WAKEWORD_PERMISSIONS = 7310

        /** The activity currently on screen, if any. */
        @Volatile var visible: MainActivity? = null
    }
}
