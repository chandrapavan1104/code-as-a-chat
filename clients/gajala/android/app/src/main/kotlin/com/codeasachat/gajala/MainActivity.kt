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

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Only a fresh launch counts; a recreated activity (rotation, process
        // restore) must not reopen the voice sheet.
        if (savedInstanceState == null) pendingAssist = isAssist(intent)
    }

    override fun onResume() {
        super.onResume()
        visible = this
        // Android 14+ only allows starting the microphone service from the
        // foreground, so every app open is the moment to (re)start it.
        if (WakeWordService.isEnabled(this)) WakeWordService.start(this)
    }

    override fun onPause() {
        if (visible === this) visible = null
        super.onPause()
    }

    override fun onDestroy() {
        // Swiping Gajala away mid-voice-turn kills the Dart side that would have
        // released its mic hold; don't leave hands-free paused forever.
        if (isFinishing) WakeWordService.hold(false)
        super.onDestroy()
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        val messenger = flutterEngine.dartExecutor.binaryMessenger
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
                else -> result.notImplemented()
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
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
        if (requestCode != WAKEWORD_PERMISSIONS) return
        pendingEnable?.let(::finishEnable)
        pendingEnable = null
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
