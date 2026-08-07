package com.codeasachat.gajala

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.view.View
import android.widget.RemoteViews
import es.antonborri.home_widget.HomeWidgetBackgroundIntent
import es.antonborri.home_widget.HomeWidgetLaunchIntent
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
                val names = intArrayOf(R.id.codaur_name1, R.id.codaur_name2, R.id.codaur_name3, R.id.codaur_name4)
                val tokens = intArrayOf(R.id.codaur_tokens1, R.id.codaur_tokens2, R.id.codaur_tokens3, R.id.codaur_tokens4)
                val limits = intArrayOf(R.id.codaur_limit1, R.id.codaur_limit2, R.id.codaur_limit3, R.id.codaur_limit4)
                val progress = intArrayOf(R.id.codaur_progress1, R.id.codaur_progress2, R.id.codaur_progress3, R.id.codaur_progress4)
                val defaults = arrayOf("Codex", "Claude", "Gemini", "Qwen")
                for (i in 0..3) {
                    setTextViewText(names[i], widgetData.getString("codaur_name${i + 1}", defaults[i]))
                    setTextViewText(tokens[i], widgetData.getString("codaur_tokens${i + 1}", "— today"))
                    setTextViewText(limits[i], widgetData.getString("codaur_limit${i + 1}", if (i == 3) "LOCAL" else "—"))
                    val hasLimit = widgetData.getBoolean("codaur_has_limit${i + 1}", false)
                    setProgressBar(progress[i], 100, widgetData.getInt("codaur_progress${i + 1}", 0), false)
                    setViewVisibility(progress[i], if (hasLimit) View.VISIBLE else View.INVISIBLE)
                }
                setTextViewText(
                    R.id.codaur_summary,
                    widgetData.getString("codaur_summary", "4 providers · tap refresh"),
                )
                setTextViewText(R.id.codaur_updated, widgetData.getString("codaur_updated", null) ?: "")
                setOnClickPendingIntent(
                    R.id.codaur_refresh,
                    HomeWidgetBackgroundIntent.getBroadcast(context, Uri.parse("gajala://codaur")),
                )
                setOnClickPendingIntent(
                    R.id.widget_root,
                    HomeWidgetLaunchIntent.getActivity(
                        context, MainActivity::class.java, Uri.parse("gajala://usage"),
                    ),
                )
            }
            appWidgetManager.updateAppWidget(id, views)
        }
    }
}
