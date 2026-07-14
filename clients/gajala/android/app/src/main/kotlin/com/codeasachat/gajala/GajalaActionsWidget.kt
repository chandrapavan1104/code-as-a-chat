package com.codeasachat.gajala

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.widget.RemoteViews
import es.antonborri.home_widget.HomeWidgetBackgroundIntent
import es.antonborri.home_widget.HomeWidgetLaunchIntent
import es.antonborri.home_widget.HomeWidgetProvider

/**
 * Home-screen quick actions:
 *  • Lock  → background call to /run (mac lock) — no need to open the app.
 *  • Ask   → opens the Gajala chat.
 *  • Dump  → opens the Brain Dump note composer.
 * The lock status line is written back from Dart via HomeWidget.saveWidgetData.
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
                    R.id.tile_lock,
                    HomeWidgetBackgroundIntent.getBroadcast(context, Uri.parse("gajala://lock")),
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
                setTextViewText(
                    R.id.lock_status,
                    widgetData.getString("lock_status", null) ?: "Tap Lock to sleep the Mac",
                )
            }
            appWidgetManager.updateAppWidget(id, views)
        }
    }
}
