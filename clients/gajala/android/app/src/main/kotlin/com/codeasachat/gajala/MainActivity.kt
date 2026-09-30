package com.codeasachat.gajala

import android.content.Intent
import android.os.Bundle
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    private var channel: MethodChannel? = null
    private var pendingAssist = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Only a fresh launch counts; a recreated activity (rotation, process
        // restore) must not reopen the voice sheet.
        if (savedInstanceState == null) pendingAssist = isAssist(intent)
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        channel = MethodChannel(flutterEngine.dartExecutor.binaryMessenger, "gajala/assist").apply {
            setMethodCallHandler { call, result ->
                if (call.method == "consumeLaunch") {
                    result.success(pendingAssist)
                    pendingAssist = false
                } else {
                    result.notImplemented()
                }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        if (isAssist(intent)) channel?.invokeMethod("assist", null)
    }

    private fun isAssist(intent: Intent?): Boolean =
        intent?.action == Intent.ACTION_ASSIST || intent?.action == ACTION_VOICE

    companion object {
        const val ACTION_VOICE = "com.codeasachat.gajala.action.VOICE"
    }
}
