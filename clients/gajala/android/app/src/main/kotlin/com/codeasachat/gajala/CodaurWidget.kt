package com.codeasachat.gajala

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.widget.RemoteViews
import es.antonborri.home_widget.HomeWidgetBackgroundIntent
import es.antonborri.home_widget.HomeWidgetProvider

/**
 * At-a-glance Codaur LLM usage. Tapping the card triggers a background fetch of
 * /api/usage (via Dart) which writes the lines back and refreshes in place — no
 * need to open the app. The Dart side also refreshes it when the usage screen is
 * viewed, so the glance stays reasonably fresh.
 */
class CodaurWidget : HomeWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        appWidgetIds.forEach { id ->
            val views = RemoteViews(context.packageName, R.layout.widget_codaur).apply {
                setTextViewText(
                    R.id.codaur_line1,
                    widgetData.getString("codaur_line1", null) ?: "Tap to load usage…",
                )
                setTextViewText(R.id.codaur_line2, widgetData.getString("codaur_line2", null) ?: "")
                setTextViewText(R.id.codaur_line3, widgetData.getString("codaur_line3", null) ?: "")
                setTextViewText(R.id.codaur_updated, widgetData.getString("codaur_updated", null) ?: "")
                setOnClickPendingIntent(
                    R.id.widget_root,
                    HomeWidgetBackgroundIntent.getBroadcast(context, Uri.parse("gajala://codaur")),
                )
            }
            appWidgetManager.updateAppWidget(id, views)
        }
    }
}
