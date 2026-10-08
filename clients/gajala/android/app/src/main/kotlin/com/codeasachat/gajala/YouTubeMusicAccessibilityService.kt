package com.codeasachat.gajala

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.os.Bundle
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.util.Log
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/**
 * Performs one explicit, short-lived YouTube Music search requested by voice.
 * Android restricts events to the YouTube Music package in the service XML.
 * This service never stores window content or uses gestures. It conservatively
 * rejects controls and results whose accessible labels identify advertising.
 */
class YouTubeMusicAccessibilityService : AccessibilityService() {
    enum class Outcome { SELECTED, SEARCHED, FAILED, CANCELLED }

    private enum class Stage { OPEN_SEARCH, ENTER_QUERY, SUBMIT_QUERY, CHOOSE_RESULT, PLAY_COLLECTION }
    private enum class MatchKind { TRACK, COLLECTION }

    private data class Request(
        val id: Long,
        val query: String,
        val tokens: List<String>,
        val callback: (Outcome) -> Unit,
        var stage: Stage = Stage.OPEN_SEARCH,
        var collectionTokens: List<String> = emptyList(),
    )

    private val handler = Handler(Looper.getMainLooper())
    private var request: Request? = null
    private var serial = 0L
    private var processRunnable: Runnable? = null
    private var timeoutRunnable: Runnable? = null

    override fun onServiceConnected() {
        instance = this
        Log.i(TAG, "YouTube Music control connected")
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event?.packageName?.toString() != YOUTUBE_MUSIC || request == null) return
        scheduleProcess()
    }

    override fun onInterrupt() {
        finish(Outcome.CANCELLED, "interrupted")
    }

    override fun onDestroy() {
        if (instance === this) instance = null
        finish(Outcome.CANCELLED, "service destroyed")
        super.onDestroy()
    }

    private fun begin(query: String, callback: (Outcome) -> Unit) {
        finish(Outcome.CANCELLED, "superseded")
        val tokens = normalise(query).split(' ')
            .filter { it.length > 2 && it !in GENERIC_WORDS }
        request = Request(++serial, query, tokens, callback)
        Log.i(TAG, "music request ${request?.id} started")
        val launch = packageManager.getLaunchIntentForPackage(YOUTUBE_MUSIC)
        if (launch == null) {
            finish(Outcome.FAILED, "app unavailable")
            return
        }
        try {
            startActivity(launch.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (_: Exception) {
            finish(Outcome.FAILED, "app launch failed")
            return
        }
        timeoutRunnable = Runnable {
            val outcome = if (request?.stage in setOf(Stage.CHOOSE_RESULT, Stage.PLAY_COLLECTION)) {
                Outcome.SEARCHED
            } else Outcome.FAILED
            finish(outcome, "timed out")
        }.also {
            handler.postDelayed(it, REQUEST_TIMEOUT_MS)
        }
        scheduleProcess(LAUNCH_SETTLE_MS)
    }

    private fun scheduleProcess(delay: Long = EVENT_SETTLE_MS) {
        processRunnable?.let(handler::removeCallbacks)
        processRunnable = Runnable { processWindow() }.also { handler.postDelayed(it, delay) }
    }

    private fun processWindow() {
        processRunnable = null
        val current = request ?: return
        val root = rootInActiveWindow ?: return
        if (root.packageName?.toString() != YOUTUBE_MUSIC) return
        when (current.stage) {
            Stage.OPEN_SEARCH -> {
                val all = nodes(root)
                if (all.any { it.isEditable && it.isEnabled }) {
                    current.stage = Stage.ENTER_QUERY
                    scheduleProcess(0)
                    return
                }
                val search = all.firstOrNull {
                    it.isClickable && label(it).let { text ->
                        text == "search" || text.startsWith("search ")
                    }
                }
                if (search?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true) {
                    current.stage = Stage.ENTER_QUERY
                    Log.i(TAG, "music request ${current.id}: search opened")
                    scheduleProcess()
                }
            }
            Stage.ENTER_QUERY -> {
                val field = nodes(root).firstOrNull { it.isEditable && it.isEnabled }
                if (field != null) {
                    val args = Bundle().apply {
                        putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, current.query)
                    }
                    field.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
                    if (field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
                        current.stage = Stage.SUBMIT_QUERY
                        Log.i(TAG, "music request ${current.id}: query entered")
                        scheduleProcess()
                    }
                }
            }
            Stage.SUBMIT_QUERY -> {
                val all = nodes(root)
                val field = all.firstOrNull { it.isEditable && it.isEnabled }
                val submitted = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R && field != null) {
                    field.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
                } else {
                    val suggestion = all.firstOrNull {
                        it.isClickable && it.isEnabled && isExactSearchSuggestion(label(it), current.query)
                    }
                    suggestion?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true
                }
                if (submitted) {
                    current.stage = Stage.CHOOSE_RESULT
                    Log.i(TAG, "music request ${current.id}: search submitted")
                    scheduleProcess(RESULTS_SETTLE_MS)
                }
            }
            Stage.CHOOSE_RESULT -> {
                val candidate = nodes(root).asSequence()
                    .filter { it.isClickable && it.isEnabled && !it.isEditable }
                    .mapNotNull { node ->
                        val text = subtreeLabel(node)
                        classifyMedia(label(node), text, current.tokens)?.let { Triple(node, text, it) }
                    }
                    .minByOrNull { (_, text) -> text.length }
                if (candidate?.first?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true) {
                    if (candidate.third == MatchKind.TRACK) {
                        Log.i(TAG, "music request ${current.id}: matching track selected")
                        finish(Outcome.SELECTED, "track selected")
                    } else {
                        current.collectionTokens = current.tokens
                        current.stage = Stage.PLAY_COLLECTION
                        Log.i(TAG, "music request ${current.id}: matching collection opened")
                        scheduleProcess(COLLECTION_SETTLE_MS)
                    }
                }
            }
            Stage.PLAY_COLLECTION -> {
                val all = nodes(root)
                // A generic Play button is safe only after a non-clickable page
                // heading proves that the opened album/playlist still matches.
                if (all.any { it.isEditable && it.isVisibleToUser }) return
                val matchingHeading = all.any { node ->
                    collectionHeadingMatches(node, current.collectionTokens)
                }
                val collectionPage = all.asSequence().filter { !it.isClickable }
                    .map(::label).flatMap { it.split(' ').asSequence() }
                    .any(COLLECTION_WORDS::contains)
                if (!matchingHeading || !collectionPage) return
                val play = all.firstOrNull { node ->
                    node.isClickable && node.isEnabled && label(node) in COLLECTION_PLAY_LABELS
                }
                if (play?.performAction(AccessibilityNodeInfo.ACTION_CLICK) == true) {
                    Log.i(TAG, "music request ${current.id}: matching collection playback selected")
                    finish(Outcome.SELECTED, "collection playback selected")
                }
            }
        }
    }

    private fun classifyMedia(ownLabel: String, text: String, tokens: List<String>): MatchKind? {
        if (text.isBlank() || text.startsWith("search for ")) return null
        val words = text.split(' ').filter(String::isNotBlank).toSet()
        val ownWords = ownLabel.split(' ').filter(String::isNotBlank).toSet()
        if (UNSAFE_WORDS.any(text::contains) || "ad" in words) return null
        if (CONTROL_WORDS.any(ownWords::contains)) return null
        if (!matchesQuery(words, tokens)) return null
        return when {
            TRACK_WORDS.any(words::contains) -> MatchKind.TRACK
            COLLECTION_WORDS.any(words::contains) -> MatchKind.COLLECTION
            else -> null
        }
    }

    private fun collectionHeadingMatches(node: AccessibilityNodeInfo, tokens: List<String>): Boolean {
        if (node.isClickable || node.isEditable) return false
        val text = label(node)
        val words = text.split(' ').filter(String::isNotBlank).toSet()
        return (node.isHeading || COLLECTION_WORDS.any(words::contains)) && matchesQuery(words, tokens)
    }

    private fun matchesQuery(words: Set<String>, tokens: List<String>): Boolean =
        tokens.isNotEmpty() && tokens.all { token ->
            token in words || (token == "dsp" && DSP_FULL_NAME.all(words::contains))
        }

    private fun isExactSearchSuggestion(text: String, query: String): Boolean {
        val wanted = normalise(query)
        return text == wanted || text == "search for $wanted"
    }

    private fun nodes(root: AccessibilityNodeInfo): List<AccessibilityNodeInfo> {
        val found = ArrayList<AccessibilityNodeInfo>()
        val queue = ArrayDeque<AccessibilityNodeInfo>()
        queue.add(root)
        while (queue.isNotEmpty() && found.size < MAX_NODES) {
            val node = queue.removeFirst()
            if (node.isVisibleToUser) found += node
            for (index in 0 until node.childCount) node.getChild(index)?.let(queue::addLast)
        }
        return found
    }

    private fun label(node: AccessibilityNodeInfo): String = normalise(
        listOfNotNull(node.text?.toString(), node.contentDescription?.toString()).joinToString(" ")
    )

    private fun subtreeLabel(root: AccessibilityNodeInfo): String = nodes(root)
        .asSequence().map(::label).filter(String::isNotBlank).distinct().joinToString(" ")

    private fun finish(outcome: Outcome, reason: String) {
        val current = request ?: return
        request = null
        processRunnable?.let(handler::removeCallbacks)
        timeoutRunnable?.let(handler::removeCallbacks)
        processRunnable = null
        timeoutRunnable = null
        Log.i(TAG, "music request ${current.id}: $reason")
        current.callback(outcome)
    }

    companion object {
        private const val TAG = "GajalaMusicControl"
        private const val YOUTUBE_MUSIC = "com.google.android.apps.youtube.music"
        private const val REQUEST_TIMEOUT_MS = 12_000L
        private const val LAUNCH_SETTLE_MS = 450L
        private const val EVENT_SETTLE_MS = 120L
        private const val RESULTS_SETTLE_MS = 700L
        private const val COLLECTION_SETTLE_MS = 700L
        private const val MAX_NODES = 600
        private val GENERIC_WORDS = setOf("play", "music", "song", "songs", "track", "tracks", "listen", "please")
        private val UNSAFE_WORDS = setOf("sponsored", "advertisement", "promotion", "promoted")
        private val TRACK_WORDS = setOf("song", "video", "single", "track")
        private val COLLECTION_WORDS = setOf("album", "playlist")
        private val CONTROL_WORDS = setOf("more", "options", "menu", "share", "download", "save")
        private val COLLECTION_PLAY_LABELS = setOf("play", "shuffle play")
        private val DSP_FULL_NAME = setOf("devi", "sri", "prasad")
        @Volatile private var instance: YouTubeMusicAccessibilityService? = null

        private fun normalise(value: String): String = value.lowercase()
            .replace(Regex("[^\\p{L}\\p{N}]+"), " ").trim()

        fun isEnabled(context: Context): Boolean {
            val wanted = ComponentName(context, YouTubeMusicAccessibilityService::class.java)
            return Settings.Secure.getString(context.contentResolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES)
                .orEmpty().split(':').any { ComponentName.unflattenFromString(it) == wanted }
        }

        fun start(query: String, callback: (Outcome) -> Unit): Boolean {
            val service = instance ?: return false
            service.handler.post { service.begin(query, callback) }
            return true
        }

        fun startProbe(): Boolean = instance != null

        fun cancel() {
            instance?.handler?.post { instance?.finish(Outcome.CANCELLED, "caller cancelled") }
        }
    }
}
