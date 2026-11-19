package com.codeasachat.gajala

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.work.CoroutineWorker
import androidx.work.Data
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import es.antonborri.home_widget.HomeWidgetPlugin
import io.flutter.FlutterInjector
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.embedding.engine.dart.DartExecutor
import io.flutter.plugin.common.MethodChannel
import io.flutter.view.FlutterCallbackInformation
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout

/** Wait for Dart completion: a tap is not finished when its callback is merely queued. */
class GajalaWidgetWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val uri = inputData.getString("uri") ?: return Result.failure()
        val action = Uri.parse(uri).host ?: return Result.failure()
        val prefs = applicationContext.getSharedPreferences("HomeWidgetPreferences", Context.MODE_PRIVATE)
        val dispatchKey = "widget_dispatch_$action"
        var engine: FlutterEngine? = null
        try {
            // WorkManager can restart an interrupted job with the SAME id.
            // Persist dispatch before executing: a lost process must not replay a mutation.
            check(prefs.getString(dispatchKey, null) != id.toString()) { "outcome_uncertain" }
            check(prefs.edit().putString(dispatchKey, id.toString()).commit()) { "journal_unavailable" }
            withTimeout(75000L) {
                val done = CompletableDeferred<Unit>()
                withContext(Dispatchers.Main) {
                    val loader = FlutterInjector.instance().flutterLoader()
                    loader.startInitialization(applicationContext)
                    loader.ensureInitializationComplete(applicationContext, null)
                    val dispatcher = HomeWidgetPlugin.getDispatcherHandle(applicationContext)
                    val callback = HomeWidgetPlugin.getHandle(applicationContext)
                    val info = FlutterCallbackInformation.lookupCallbackInformation(dispatcher)
                        ?: throw IllegalStateException("callback_missing")
                    check(callback != 0L) { "callback_missing" }
                    engine = FlutterEngine(applicationContext)
                    val channel = MethodChannel(engine!!.dartExecutor.binaryMessenger, "home_widget/background")
                    channel.setMethodCallHandler { call, result ->
                        if (call.method == "HomeWidget.backgroundInitialized") {
                            result.success(null)
                            channel.invokeMethod("", listOf(callback, uri), object : MethodChannel.Result {
                                override fun success(value: Any?) { done.complete(Unit) }
                                override fun error(code: String, message: String?, details: Any?) {
                                    done.completeExceptionally(IllegalStateException("callback_failed"))
                                }
                                override fun notImplemented() {
                                    done.completeExceptionally(IllegalStateException("callback_missing"))
                                }
                            })
                        } else result.notImplemented()
                    }
                    engine!!.dartExecutor.executeDartCallback(DartExecutor.DartCallback(
                        applicationContext.assets, loader.findAppBundlePath(), info))
                }
                done.await()
            }
            return Result.success()
        } catch (error: Exception) {
            // No automatic retry: a mutation may already have reached the Mac.
            val category = if (error.message == "callback_missing") "Open Gajala once to reconnect widgets"
                           else "Widget interrupted; outcome uncertain. Open Gajala to check"
            prefs.edit().putString("widget_native_error", category)
                .putLong("widget_native_error_at", System.currentTimeMillis()).apply()
            if (action == "codaur") {
                prefs.edit().putBoolean("codaur_stale", true).putString("codaur_error", category).apply()
            } else prefs.edit().putString("mac_status", category).putLong("mac_busy_until", 0L).apply()
            val manager = AppWidgetManager.getInstance(applicationContext)
            val provider = if (action == "codaur") CodaurWidget::class.java else GajalaActionsWidget::class.java
            val ids = manager.getAppWidgetIds(ComponentName(applicationContext, provider))
            applicationContext.sendBroadcast(Intent(applicationContext, provider)
                .setAction(AppWidgetManager.ACTION_APPWIDGET_UPDATE)
                .putExtra(AppWidgetManager.EXTRA_APPWIDGET_IDS, ids))
            return Result.failure()
        } finally {
            withContext(kotlinx.coroutines.NonCancellable + Dispatchers.Main) { engine?.destroy() }
        }
    }
}

/** Independent unique jobs recover after failure; KEEP coalesces repeated taps. */
class GajalaWidgetReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val uri = intent.data ?: return
        if (uri.host !in setOf("lock", "wake", "codaur")) return
        val request = OneTimeWorkRequestBuilder<GajalaWidgetWorker>()
            .setInputData(Data.Builder().putString("uri", uri.toString()).build()).build()
        val lane = if (uri.host == "codaur") "codaur" else "mac"
        WorkManager.getInstance(context).enqueueUniqueWork("gajala_widget_$lane", ExistingWorkPolicy.KEEP, request)
    }

    companion object {
        fun pending(context: Context, uri: String): PendingIntent = PendingIntent.getBroadcast(
            context, 0, Intent(context, GajalaWidgetReceiver::class.java).setData(Uri.parse(uri)),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }
}
