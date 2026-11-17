package com.codeasachat.gajala

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.widget.RemoteViews
import es.antonborri.home_widget.HomeWidgetLaunchIntent
import es.antonborri.home_widget.HomeWidgetProvider

/**
 * Home-screen quick actions:
 *  • Lock  → background call to /run (mac lock) — no need to open the app.
 *  • Wake  → background call to /run (mac wake) — remote unlock when no password
 *            is required after sleep / within the grace window.
 *  • Ask   → opens the Gajala chat.
 *  • Dump  → opens the Brain Dump note composer.
 * The mac status line is written back from Dart via HomeWidget.saveWidgetData.
 */
class GajalaActionsWidget : HomeWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        appWidgetIds.forEach { id ->
            val views = RemoteViews(context.packageName, R.layout.widget_gajala_actions).apply {
                setOnClickPendingIntent(
                    R.id.widget_root,
                    HomeWidgetLaunchIntent.getActivity(
                        context, MainActivity::class.java, Uri.parse("gajala://ask"),
                    ),
                )
                setOnClickPendingIntent(
                    R.id.tile_lock,
                    GajalaWidgetReceiver.pending(context, "gajala://lock"),
                )
                setOnClickPendingIntent(
                    R.id.tile_wake,
                    GajalaWidgetReceiver.pending(context, "gajala://wake"),
                )
                setOnClickPendingIntent(
                    R.id.tile_ask,
                    HomeWidgetLaunchIntent.getActivity(
                        context, MainActivity::class.java, Uri.parse("gajala://ask"),
                    ),
                )
                setOnClickPendingIntent(
                    R.id.tile_dump,
                    HomeWidgetLaunchIntent.getActivity(
                        context, MainActivity::class.java, Uri.parse("gajala://dump"),
                    ),
                )
                val busyUntil = widgetData.getLong("mac_busy_until", 0L)
                val abandoned = busyUntil > 0L && System.currentTimeMillis() > busyUntil
                val status = if (abandoned) "Previous action interrupted; check the Mac" else
                    widgetData.getString("mac_status", null) ?: "Ready"
                setTextViewText(
                    R.id.mac_status,
                    status,
                )
                val unhealthy = status.lowercase().contains("fail") ||
                    status.lowercase().contains("not connected") ||
                    status.lowercase().contains("interrupted") || status.lowercase().contains("uncertain")
                val busy = status.contains('…')
                setImageViewResource(
                    R.id.mac_status_dot,
                    if (unhealthy || busy) R.drawable.widget_dot_warn else R.drawable.widget_dot_ok,
                )
            }
            appWidgetManager.updateAppWidget(id, views)
        }
    }
}
