package com.codeasachat.gajala

import android.appwidget.AppWidgetManager
import android.content.Context
import android.content.SharedPreferences
import android.net.Uri
import android.text.format.DateUtils
import android.view.View
import android.util.TypedValue
import android.widget.RemoteViews
import es.antonborri.home_widget.HomeWidgetBackgroundIntent
import es.antonborri.home_widget.HomeWidgetLaunchIntent
import es.antonborri.home_widget.HomeWidgetProvider

/** Compact launcher view of token activity and separately labelled subscription windows. */
class CodaurWidget : HomeWidgetProvider() {
    override fun onUpdate(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetIds: IntArray,
        widgetData: SharedPreferences,
    ) {
        render(context, appWidgetManager, appWidgetIds, widgetData)
    }

    override fun onAppWidgetOptionsChanged(
        context: Context,
        appWidgetManager: AppWidgetManager,
        appWidgetId: Int,
        newOptions: android.os.Bundle,
    ) {
        super.onAppWidgetOptionsChanged(context, appWidgetManager, appWidgetId, newOptions)
        val preferences = context.getSharedPreferences("HomeWidgetPreferences", Context.MODE_PRIVATE)
        render(context, appWidgetManager, intArrayOf(appWidgetId), preferences)
    }

    private fun render(
        context: Context,
        manager: AppWidgetManager,
        ids: IntArray,
        widgetData: SharedPreferences,
    ) {
        ids.forEach { id ->
            val views = RemoteViews(context.packageName, R.layout.widget_codaur).apply {
                val names = intArrayOf(
                    R.id.codaur_name1, R.id.codaur_name2,
                    R.id.codaur_name3, R.id.codaur_name4,
                )
                val tokens = intArrayOf(
                    R.id.codaur_tokens1, R.id.codaur_tokens2,
                    R.id.codaur_tokens3, R.id.codaur_tokens4,
                )
                val windows = intArrayOf(
                    R.id.codaur_windows1, R.id.codaur_windows2,
                    R.id.codaur_windows3, R.id.codaur_windows4,
                )
                val localNotes = intArrayOf(
                    R.id.codaur_local1, R.id.codaur_local2,
                    R.id.codaur_local3, R.id.codaur_local4,
                )
                val primaryLabels = intArrayOf(
                    R.id.codaur_primary_label1, R.id.codaur_primary_label2,
                    R.id.codaur_primary_label3, R.id.codaur_primary_label4,
                )
                val primaryBars = intArrayOf(
                    R.id.codaur_primary_progress1, R.id.codaur_primary_progress2,
                    R.id.codaur_primary_progress3, R.id.codaur_primary_progress4,
                )
                val secondaryLabels = intArrayOf(
                    R.id.codaur_secondary_label1, R.id.codaur_secondary_label2,
                    R.id.codaur_secondary_label3, R.id.codaur_secondary_label4,
                )
                val secondaryBars = intArrayOf(
                    R.id.codaur_secondary_progress1, R.id.codaur_secondary_progress2,
                    R.id.codaur_secondary_progress3, R.id.codaur_secondary_progress4,
                )
                val defaults = arrayOf("Codex", "Claude", "Gemini", "Qwen")
                for (index in 0..3) {
                    val suffix = index + 1
                    val provider = widgetData.getString("codaur_name$suffix", defaults[index])
                        ?.takeIf { it.isNotBlank() } ?: defaults[index]
                    val isLocal = provider.equals("Qwen", ignoreCase = true)
                    val hasPrimary = widgetData.getBoolean("codaur_has_primary$suffix", false)
                    val hasSecondary = widgetData.getBoolean("codaur_has_secondary$suffix", false)
                    val primary = widgetData.getInt("codaur_primary_pct$suffix", 0).coerceIn(0, 100)
                    val secondary = widgetData.getInt("codaur_secondary_pct$suffix", 0).coerceIn(0, 100)
                    val tokenActivity = widgetData.getString("codaur_tokens$suffix", null)
                        ?.takeIf { it.isNotBlank() } ?: "Tokens today —"
                    val primaryLabel = widgetData.getString("codaur_primary_label$suffix", "5h") ?: "5h"
                    val primaryText = if (hasPrimary) "$primaryLabel ${primary}%" else "$primaryLabel —"
                    val secondaryText = if (hasSecondary) "Week ${secondary}%" else "Week —"

                    setTextViewText(names[index], provider)
                    setTextViewText(tokens[index], tokenActivity)
                    setContentDescription(
                        windows[index],
                        widgetData.getString("codaur_limit$suffix", "$primaryText · $secondaryText")
                            ?: "$primaryText · $secondaryText",
                    )
                    setTextViewText(primaryLabels[index], primaryText)
                    setTextViewText(secondaryLabels[index], secondaryText)
                    setProgressBar(primaryBars[index], 100, primary, false)
                    setProgressBar(secondaryBars[index], 100, secondary, false)
                    setViewVisibility(primaryBars[index], if (hasPrimary) View.VISIBLE else View.GONE)
                    setViewVisibility(secondaryBars[index], if (hasSecondary) View.VISIBLE else View.GONE)

                    if (isLocal || (!hasPrimary && !hasSecondary)) {
                        setViewVisibility(windows[index], View.GONE)
                        setViewVisibility(localNotes[index], View.VISIBLE)
                        setTextViewText(localNotes[index], if (isLocal) "LOCAL · no subscription quota" else "Quota unavailable")
                    } else {
                        setViewVisibility(windows[index], View.VISIBLE)
                        setViewVisibility(localNotes[index], View.GONE)
                    }
                }

                setTextViewText(
                    R.id.codaur_summary,
                    widgetData.getString("codaur_summary", "Token activity · subscription quotas"),
                )
                setTextViewText(R.id.codaur_updated, updatedText(widgetData))
                val error = widgetData.getString("codaur_error", null)?.trim().orEmpty()
                val epoch = widgetData.getLong("codaur_updated_epoch", 0L)
                val old = epoch > 0L && System.currentTimeMillis() - epoch > STALE_AFTER_MS
                val stale = widgetData.getBoolean("codaur_stale", false) || old
                val showBadge = stale || error.isNotEmpty()
                setViewVisibility(R.id.codaur_stale_badge, if (showBadge) View.VISIBLE else View.GONE)
                if (showBadge) {
                    setTextViewText(R.id.codaur_stale_badge, if (error.isNotEmpty()) "UPDATE FAILED" else "STALE")
                    setContentDescription(
                        R.id.codaur_stale_badge,
                        error.ifEmpty { "Usage data may be stale" },
                    )
                }
                val options = manager.getAppWidgetOptions(id)
                val availableWidth = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0)
                val availableHeight = options.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_HEIGHT, 0)
                if (availableWidth in 1 until COMPACT_WIDTH_DP || availableHeight in 1 until COMPACT_HEIGHT_DP) {
                    setTextViewTextSize(R.id.codaur_summary, TypedValue.COMPLEX_UNIT_SP, 9f)
                    for (viewId in tokens) setTextViewTextSize(viewId, TypedValue.COMPLEX_UNIT_SP, 8f)
                    for (viewId in primaryLabels + secondaryLabels) {
                        setTextViewTextSize(viewId, TypedValue.COMPLEX_UNIT_SP, 8f)
                    }
                }

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
            manager.updateAppWidget(id, views)
        }
    }

    private fun updatedText(data: SharedPreferences): String {
        val epoch = data.getLong("codaur_updated_epoch", 0L)
        if (epoch <= 0L) {
            return data.getString("codaur_updated", null)?.takeIf { it.isNotBlank() }
                ?.replace("SYNC", "Updated") ?: "Updated —"
        }
        val relative = DateUtils.getRelativeTimeSpanString(
            epoch,
            System.currentTimeMillis(),
            DateUtils.MINUTE_IN_MILLIS,
        )
        return "Updated $relative"
    }

    private companion object {
        const val STALE_AFTER_MS = 15 * 60 * 1000L
        const val COMPACT_WIDTH_DP = 230
        const val COMPACT_HEIGHT_DP = 225
    }
}
