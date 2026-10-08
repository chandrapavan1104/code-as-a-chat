package com.codeasachat.gajala

import android.Manifest
import android.app.Activity
import android.app.NotificationManager
import android.os.Build
import android.os.Bundle
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.media.browse.MediaBrowser
import android.media.session.MediaController as PlatformMediaController
import android.media.session.MediaSession
import java.util.UUID
import android.provider.ContactsContract
import android.provider.MediaStore
import android.provider.Settings
import android.telephony.PhoneNumberUtils
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import io.flutter.plugin.common.MethodCall
import io.flutter.plugin.common.MethodChannel

/** Native phone actions used by the voice sheet. Every external action returns
 * a typed result; placing a call always requires a separate explicit retry
 * after Android grants CALL_PHONE. */
class VoiceActions(private val activity: Activity) {
    private data class Pending(val kind: String, val result: MethodChannel.Result, val query: String? = null)
    private inner class OnceMusicResult(
        private val requestId: String,
        private val delegate: MethodChannel.Result,
    ) : MethodChannel.Result {
        private var completed = false
        override fun success(result: Any?) = finish {
            val diagnostic = (result as? Map<*, *>)?.get("diagnostics") as? Map<*, *>
            if (diagnostic != null) {
                try {
                    activity.getSharedPreferences("music_diagnostics", Context.MODE_PRIVATE).edit()
                        .putString("last_result", org.json.JSONObject(diagnostic).toString().take(12000)).apply()
                } catch (_: Exception) {}
            }
            delegate.success(result)
        }
        override fun error(errorCode: String, errorMessage: String?, errorDetails: Any?) =
            finish { delegate.error(errorCode, errorMessage, errorDetails) }
        override fun notImplemented() = finish { delegate.notImplemented() }
        private fun finish(action: () -> Unit) {
            if (completed) return
            completed = true
            pendingMusicResults.remove(requestId)
            action()
        }
    }

    private var pending: Pending? = null
    private var disposed = false
    private var verifyHandler: Handler? = null
    private var verifyRunnable: Runnable? = null
    private val musicBrowsers = mutableSetOf<MediaBrowser>()
    private val pendingMusicResults = mutableMapOf<String, OnceMusicResult>()
    private var lastMusicProbe: Map<String, Any?> = mapOf("status" to "not_checked", "reason" to "Probe has not run.")

    fun handle(call: MethodCall, result: MethodChannel.Result): Boolean {
        if (disposed) {
            result.success(mapOf("status" to "cancelled", "message" to "Activity is no longer active."))
            return true
        }
        when (call.method) {
            "resolveContact" -> resolveContact(call.argument<String>("query"), result)
            "call" -> callNumber(call.argument<String>("number"), result)
            "playMusic" -> playMusic(call.argument<String>("query"), call.argument<String>("package"), result)
            "musicApps" -> result.success(musicApps())
            "musicAccess" -> {
                val id = UUID.randomUUID().toString()
                val once = OnceMusicResult(id, result)
                pendingMusicResults[id] = once
                probeYtMusic(id) { probe ->
                    finishMusicBrowser(probe)
                    if (disposed) once.success(mapOf("status" to "cancelled", "message" to "Activity is no longer active."))
                    else once.success(musicAccess(probe))
                }
            }
            "openMusicControlAccess" -> {
                try {
                    activity.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                    result.success(mapOf("status" to "requested"))
                } catch (_: android.content.ActivityNotFoundException) {
                    result.success(mapOf("status" to "unsupported",
                        "message" to "This phone has no accessibility settings."))
                }
            }
            "openMusicAccess" -> {
                try {
                    activity.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                    result.success(mapOf("status" to "requested"))
                } catch (_: android.content.ActivityNotFoundException) {
                    result.success(mapOf("status" to "unsupported", "message" to "This phone has no notification access settings."))
                }
            }
            else -> return false
        }
        return true
    }

    fun onPermissionResult(requestCode: Int, grantResults: IntArray): Boolean {
        val current = pending ?: return false
        val expected = when (requestCode) {
            CONTACTS_PERMISSION -> "contacts"
            CALL_PERMISSION -> "call"
            else -> return false
        }
        if (current.kind != expected) return false
        pending = null // exactly once, even if Android delivers a duplicate callback
        val granted = grantResults.isNotEmpty() &&
            grantResults.all { it == PackageManager.PERMISSION_GRANTED }
        if (!granted) {
            current.result.success(mapOf("status" to "permission_denied", "permission" to
                if (expected == "contacts") Manifest.permission.READ_CONTACTS else Manifest.permission.CALL_PHONE))
        } else if (expected == "call") {
            // Deliberately do not place the call as a permission callback side effect.
            current.result.success(mapOf("status" to "permission_granted_retry",
                "message" to "Call permission granted. Confirm the call once more."))
        } else {
            resolveContactNow(current.query, current.result)
        }
        return true
    }

    fun cancelPending() {
        pending = null
        YouTubeMusicAccessibilityService.cancel()
        pendingMusicResults.values.toList().forEach {
            it.success(mapOf("status" to "cancelled", "message" to "Activity is no longer active."))
        }
        musicBrowsers.toList().forEach { try { it.disconnect() } catch (_: Exception) {} }
        musicBrowsers.clear()
        verifyRunnable?.let { verifyHandler?.removeCallbacks(it) }
        verifyRunnable = null
        verifyHandler = null
        disposed = true
    }

    private fun resolveContact(query: String?, result: MethodChannel.Result) {
        val text = query?.trim().orEmpty()
        if (text.isEmpty()) {
            result.success(mapOf("status" to "invalid", "message" to "Contact query is empty."))
            return
        }
        if (!has(Manifest.permission.READ_CONTACTS)) {
            request("contacts", Manifest.permission.READ_CONTACTS, result, text)
            return
        }
        resolveContactNow(text, result)
    }

    private fun resolveContactNow(query: String?, result: MethodChannel.Result) {
        val text = query?.trim().orEmpty()
        val rows = mutableListOf<Map<String, String>>()
        val seen = HashSet<String>()
        val selection = if (text.isEmpty()) null else
            "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} LIKE ? OR " +
                "${ContactsContract.CommonDataKinds.Phone.NUMBER} LIKE ?"
        val args = if (text.isEmpty()) null else arrayOf("%$text%", "%$text%")
        try {
            activity.contentResolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                arrayOf(
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                    ContactsContract.CommonDataKinds.Phone.NUMBER,
                    ContactsContract.CommonDataKinds.Phone.TYPE,
                    ContactsContract.CommonDataKinds.Phone.LABEL,
                ), selection, args,
                "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME} COLLATE NOCASE ASC"
            )?.use { cursor ->
                val nameCol = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME)
                val numberCol = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
                val typeCol = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.TYPE)
                val labelCol = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.LABEL)
                while (cursor.moveToNext() && rows.size < MAX_CONTACTS) {
                    val name = cursor.getString(nameCol).orEmpty()
                    val number = cursor.getString(numberCol).orEmpty().trim()
                    if (number.isEmpty()) continue
                    val key = "${name.lowercase()}|${normalise(number)}"
                    if (!seen.add(key)) continue
                    val type = if (typeCol >= 0) cursor.getInt(typeCol) else 0
                    val custom = if (labelCol >= 0) cursor.getString(labelCol) else null
                    val label = ContactsContract.CommonDataKinds.Phone.getTypeLabel(
                        activity.resources, type, custom ?: ""
                    ).toString()
                    rows += mapOf("name" to name, "number" to number, "label" to label)
                }
            }
            result.success(mapOf("status" to "ok", "contacts" to rows))
        } catch (security: SecurityException) {
            result.success(mapOf("status" to "permission_denied", "permission" to Manifest.permission.READ_CONTACTS))
        }
    }

    private fun callNumber(raw: String?, result: MethodChannel.Result) {
        val number = raw?.trim().orEmpty()
        if (number.isEmpty() || !number.matches(Regex("^[+0-9][0-9 .()\\-]{2,}$"))) {
            result.success(mapOf("status" to "invalid", "message" to "Invalid phone number."))
            return
        }
        if (isEmergency(number)) {
            result.success(mapOf("status" to "rejected", "message" to "Emergency numbers are not callable from Gajala."))
            return
        }
        if (!has(Manifest.permission.CALL_PHONE)) {
            request("call", Manifest.permission.CALL_PHONE, result)
            return
        }
        try {
            activity.startActivity(Intent(Intent.ACTION_CALL).apply { data = android.net.Uri.parse("tel:${android.net.Uri.encode(number)}") })
            result.success(mapOf("status" to "called", "number" to number))
        } catch (security: SecurityException) {
            result.success(mapOf("status" to "permission_denied", "permission" to Manifest.permission.CALL_PHONE))
        } catch (missing: android.content.ActivityNotFoundException) {
            result.success(mapOf("status" to "unsupported", "message" to "No phone app can place calls."))
        }
    }

    private fun playMusic(query: String?, packageName: String?, result: MethodChannel.Result) {
        val text = query?.trim().orEmpty()
        if (text.isEmpty()) {
            result.success(mapOf("status" to "invalid", "message" to "Music query is empty."))
            return
        }
        val targetPackage = packageName?.takeIf { it.isNotBlank() } ?: YOUTUBE_MUSIC
        val requestId = UUID.randomUUID().toString()
        val once = OnceMusicResult(requestId, result)
        pendingMusicResults[requestId] = once
        val extras = searchExtras(text)
        val manager = mediaManager()
        val before = activeMedia(manager, targetPackage)
        val beforeToken = before?.sessionToken
        val beforeTitle = before?.metadata?.getString(MediaMetadata.METADATA_KEY_TITLE).orEmpty()
        val beforeArtist = before?.metadata?.getString(MediaMetadata.METADATA_KEY_ARTIST).orEmpty()
        if (targetPackage == YOUTUBE_MUSIC) {
            probeYtMusic(requestId) { probe ->
                if (disposed) return@probeYtMusic
                val browserConnected = probe["browserConnected"] == true
                val directSupported = probe["playFromSearchSupported"] == true
                if (browserConnected && directSupported) {
                    val browserToken = probe["sessionToken"] as? MediaSession.Token
                    val controller = try { browserToken?.let { PlatformMediaController(activity, it) } }
                    catch (_: Exception) { null }
                    if (controller != null) {
                        val diag = musicDiagnostic("direct_execution", "attempted", "playFromSearch dispatched", probe)
                        try {
                            controller.transportControls.playFromSearch(text, extras)
                            verifyPlaybackAsync(manager, targetPackage, text, extras, beforeToken,
                                beforeTitle, beforeArtist, once, diagnostic = diag) {
                                    finishMusicBrowser(probe)
                                }
                        } catch (e: Exception) {
                            finishMusicBrowser(probe)
                            once.success(mapOf("status" to "unverified", "package" to targetPackage,
                                "query" to text, "diagnostics" to diag,
                                "message" to "YouTube Music exposed play-from-search, but rejected the request (${e.javaClass.simpleName})."))
                        }
                        return@probeYtMusic
                    }
                }
                finishMusicBrowser(probe)
                // Retain existing explicit, user-visible fallbacks when the
                // official app does not expose a proven direct search action.
                playMusicFallback(text, targetPackage, extras, manager, beforeToken,
                    beforeTitle, beforeArtist, once, probe)
            }
            return
        }
        playMusicFallback(text, targetPackage, extras, manager, beforeToken,
            beforeTitle, beforeArtist, once, emptyMap())
    }

    private fun playMusicFallback(text: String, targetPackage: String, extras: Bundle,
                                  manager: MediaSessionManager?, beforeToken: android.media.session.MediaSession.Token?,
                                  beforeTitle: String, beforeArtist: String, result: MethodChannel.Result,
                                  probe: Map<String, Any?>) {
        val intent = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH).apply {
            putExtra(android.app.SearchManager.QUERY, text)
            putExtras(extras)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            setPackage(targetPackage)
        }
        if (targetPackage == YOUTUBE_MUSIC &&
            YouTubeMusicAccessibilityService.isEnabled(activity) &&
            YouTubeMusicAccessibilityService.start(text) { outcome ->
                if (disposed) return@start
                when (outcome) {
                    YouTubeMusicAccessibilityService.Outcome.SELECTED ->
                        verifyPlaybackAsync(manager, targetPackage, text, extras,
                            beforeToken, beforeTitle, beforeArtist, result, allowSessionNudge = false,
                            diagnostic = musicDiagnostic("accessibility_execution", "selected", "Accessible result selected", probe))
                    YouTubeMusicAccessibilityService.Outcome.SELECTED_COLLECTION ->
                        verifyPlaybackAsync(manager, targetPackage, text, extras,
                            beforeToken, beforeTitle, beforeArtist, result,
                            allowSessionNudge = false, selectedCollection = true,
                            diagnostic = musicDiagnostic("accessibility_execution", "selected_collection", "Accessible collection selected", probe))
                    YouTubeMusicAccessibilityService.Outcome.SEARCHED ->
                        result.success(mapOf("status" to "searched", "package" to targetPackage,
                            "query" to text, "diagnostics" to musicDiagnostic("accessibility_execution", "search_only", "No safe matching result selected", probe),
                            "message" to "Searched YouTube Music, but no safe matching song result was selected. Playback was not verified."))
                    YouTubeMusicAccessibilityService.Outcome.FAILED ->
                        result.success(mapOf("status" to "unsupported", "package" to targetPackage,
                            "query" to text, "diagnostics" to musicDiagnostic("accessibility_execution", "failed", "Could not open controlled playback UI", probe),
                            "message" to "YouTube Music could not be opened for controlled playback."))
                    YouTubeMusicAccessibilityService.Outcome.CANCELLED ->
                        result.success(mapOf("status" to "cancelled", "package" to targetPackage,
                            "query" to text, "diagnostics" to musicDiagnostic("accessibility_execution", "cancelled", "Accessibility action cancelled", probe),
                            "message" to "YouTube Music playback was cancelled."))
                }
            }
        ) return
        try {
            // Launch the handoff even when a readable media session is already
            // active. A transport-only playFromSearch call can disappear into
            // the background and gives the owner no visible query to act on.
            activity.startActivity(intent)
        } catch (_: android.content.ActivityNotFoundException) {
            if (targetPackage == YOUTUBE_MUSIC && openYouTubeMusicSearch(text)) {
                result.success(mapOf("status" to "searched", "package" to targetPackage,
                    "query" to text, "diagnostics" to musicDiagnostic("intent_execution", "search_opened", "Play-from-search intent unavailable; search opened", probe),
                    "message" to "Opened YouTube Music and searched for $text. Playback was not verified."))
                return
            }
            result.success(mapOf("status" to "unsupported", "package" to targetPackage,
                "query" to text, "diagnostics" to musicDiagnostic("intent_execution", "unsupported", "No matching play-from-search activity", probe),
                "message" to "Music app cannot start playback search."))
            return
        } catch (_: SecurityException) {
            if (targetPackage == YOUTUBE_MUSIC && openYouTubeMusicSearch(text)) {
                result.success(mapOf("status" to "searched", "package" to targetPackage,
                    "query" to text, "diagnostics" to musicDiagnostic("intent_execution", "search_opened", "Play-from-search intent denied; search opened", probe),
                    "message" to "Opened YouTube Music and searched for $text. Playback was not verified."))
                return
            }
            result.success(mapOf("status" to "unsupported", "package" to targetPackage,
                "query" to text, "diagnostics" to musicDiagnostic("intent_execution", "denied", "Play-from-search intent denied", probe),
                "message" to "Music app rejected playback search."))
            return
        }
        val access = musicAccess()
        if (access["sessionReadable"] != true) {
            result.success(mapOf("status" to "requested", "package" to targetPackage, "query" to text,
                "access" to access, "diagnostics" to musicDiagnostic("intent_execution", "search_opened", "Playback verification unavailable", probe),
                "message" to "Opened the search, but Gajala cannot start playback without notification " +
                    "access (it plays through the app's media session). ${access["message"]}"))
            return
        }
        verifyPlaybackAsync(manager, targetPackage, text, extras, beforeToken, beforeTitle, beforeArtist, result,
            allowSessionNudge = targetPackage != YOUTUBE_MUSIC,
            diagnostic = musicDiagnostic("intent_execution", "requested", "Intent dispatched; playback pending verification", probe))
    }

    /** Try a package-targeted search fallback; installed app support varies.
     * A successful dispatch is never evidence that audio began. */
    private fun openYouTubeMusicSearch(query: String): Boolean = try {
        activity.startActivity(Intent(Intent.ACTION_SEARCH).apply {
            setPackage(YOUTUBE_MUSIC)
            putExtra(android.app.SearchManager.QUERY, query)
        })
        true
    } catch (_: android.content.ActivityNotFoundException) {
        false
    } catch (_: SecurityException) {
        false
    }

    /**
     * Play-from-search extras. "X by Y" becomes a song request (title + artist),
     * anything else stays an unstructured query, as Android's media guidance
     * defines them.
     */
    private fun searchExtras(query: String): Bundle {
        val extras = Bundle()
        val songBy = Regex("^(.+?)\\s+by\\s+(.+)$", RegexOption.IGNORE_CASE).find(query.trim())
        if (songBy != null) {
            extras.putString(MediaStore.EXTRA_MEDIA_FOCUS, MediaStore.Audio.Media.ENTRY_CONTENT_TYPE)
            extras.putString(MediaStore.EXTRA_MEDIA_TITLE, songBy.groupValues[1].trim())
            extras.putString(MediaStore.EXTRA_MEDIA_ARTIST, songBy.groupValues[2].trim())
        } else {
            extras.putString(MediaStore.EXTRA_MEDIA_FOCUS, "vnd.android.cursor.item/*")
        }
        return extras
    }

    private fun verifyPlaybackAsync(manager: MediaSessionManager?, packageName: String,
                                    query: String, extras: Bundle,
                                    beforeToken: android.media.session.MediaSession.Token?,
                                    beforeTitle: String, beforeArtist: String,
                                    result: MethodChannel.Result,
                                    allowSessionNudge: Boolean = true, selectedCollection: Boolean = false,
                                    diagnostic: Map<String, Any?> = emptyMap(),
                                    onFinished: (() -> Unit)? = null) {
        if (manager == null) {
            result.success(mapOf("status" to "unverified", "package" to packageName,
                "diagnostics" to diagnostic + mapOf("stage" to "execution_verification", "outcome" to "unavailable", "reason" to "Media session manager unavailable"),
                "message" to "Playback was requested but no media session is available."))
            onFinished?.invoke()
            return
        }
        val handler = Handler(Looper.getMainLooper())
        verifyHandler = handler
        val started = System.currentTimeMillis()
        var nudged = false
        var firstPlayingPosition: Long? = null
        var firstPlayingAt: Long? = null
        val check = object : Runnable {
            override fun run() {
                if (disposed) {
                    onFinished?.invoke()
                    return
                }
                val current = activeMedia(manager, packageName)
                val playbackState = current?.playbackState
                val state = playbackState?.state
                val metadata = current?.metadata
                val title = metadata?.getString(MediaMetadata.METADATA_KEY_TITLE).orEmpty()
                val artist = metadata?.getString(MediaMetadata.METADATA_KEY_ARTIST).orEmpty()
                val album = metadata?.getString(MediaMetadata.METADATA_KEY_ALBUM).orEmpty()
                val needle = query.lowercase().split(Regex("\\s+"))
                    .filter { it.length > 2 && it !in GENERIC_MUSIC_WORDS }
                val matched = selectedCollection || needle.isEmpty() || needle.all {
                    title.lowercase().contains(it) || artist.lowercase().contains(it) || album.lowercase().contains(it) ||
                        (it == "dsp" && listOf("devi", "sri", "prasad").all { part ->
                            "$title $artist $album".lowercase().contains(part)
                        })
                }
                val changed = beforeToken == null || current?.sessionToken != beforeToken ||
                    title != beforeTitle || artist != beforeArtist
                val now = android.os.SystemClock.elapsedRealtime()
                val position = playbackState?.let {
                    when {
                        it.position < 0 -> null
                        it.lastPositionUpdateTime <= 0 -> it.position
                        else -> it.position +
                            ((now - it.lastPositionUpdateTime).coerceAtLeast(0) * it.playbackSpeed).toLong()
                    }
                }
                val eligible = state == PlaybackState.STATE_PLAYING && title.isNotBlank() && matched && changed
                if (eligible && position != null && firstPlayingPosition == null) {
                    firstPlayingPosition = position
                    firstPlayingAt = now
                }
                val firstPosition = firstPlayingPosition
                val firstAt = firstPlayingAt
                val positionAdvancedMs = position?.let { current -> firstPosition?.let { current - it } }
                val advancing = eligible && firstAt != null && now - firstAt >= 200 &&
                    positionAdvancedMs != null && positionAdvancedMs >= 200
                if (advancing) {
                    verifyRunnable = null
                    result.success(mapOf("status" to "playing", "package" to packageName,
                        "title" to title, "artist" to artist,
                        "diagnostics" to diagnostic + mapOf("stage" to "execution_verification", "outcome" to "verified",
                            "executionDurationMs" to (System.currentTimeMillis() - started),
                            "observedState" to state, "observedTitle" to title.take(120),
                            "positionAdvancedMs" to positionAdvancedMs),
                        "message" to "Playback verified."))
                    onFinished?.invoke()
                } else if (allowSessionNudge && current != null && !nudged &&
                    System.currentTimeMillis() - started >= NUDGE_AFTER_MS) {
                    // The launch intent only opens YouTube Music's search
                    // results. Assistant plays through the app's media
                    // session (onPlayFromSearch), which must start playback
                    // immediately, so ask the session directly.
                    nudged = true
                    try {
                        current.transportControls.playFromSearch(query, extras)
                    } catch (_: Exception) { /* verification below reports it */ }
                    handler.postDelayed(this, VERIFY_INTERVAL_MS)
                } else if (System.currentTimeMillis() - started >= VERIFY_MS) {
                    verifyRunnable = null
                    val positionAdvanced = position?.let { current -> firstPlayingPosition?.let { current - it } }
                    result.success(mapOf("status" to "unverified", "package" to packageName,
                        "title" to title, "artist" to artist,
                        "diagnostics" to diagnostic + mapOf("stage" to "execution_verification", "outcome" to "unverified",
                            "executionDurationMs" to (System.currentTimeMillis() - started),
                            "observedState" to state, "observedTitle" to title.take(120),
                            "positionAdvancedMs" to positionAdvanced),
                        "message" to "Playback was requested, but Gajala could not verify the matching track started."))
                    onFinished?.invoke()
                } else handler.postDelayed(this, VERIFY_INTERVAL_MS)
            }
        }
        verifyRunnable = check
        handler.post(check)
    }

    private fun mediaManager(): MediaSessionManager? = try {
        activity.getSystemService(Context.MEDIA_SESSION_SERVICE) as? MediaSessionManager
    } catch (_: Throwable) { null }

    private fun activeMedia(manager: MediaSessionManager?, packageName: String): MediaController? = try {
        val component = ComponentName(activity, GajalaNotificationListener::class.java)
        manager?.getActiveSessions(component)?.firstOrNull { it.packageName == packageName }
    } catch (_: SecurityException) { null }

    // Permission approval and media-session availability are distinct facts.
    private fun musicAccess(probe: Map<String, Any?> = lastMusicProbe): Map<String, Any?> {
        val component = ComponentName(activity, GajalaNotificationListener::class.java)
        val enabled = notificationListenerEnabled()
        var error: String? = null
        val sessions = try {
            val manager = mediaManager()
            if (manager == null) error = "Media service unavailable"
            manager?.getActiveSessions(component)
        } catch (e: Exception) {
            error = e.javaClass.simpleName
            null
        }
        val readable = sessions != null
        val message = when {
            readable -> "Notification access is working. Playback can be checked."
            enabled == true -> "Notification access is enabled, but Android music sessions are unavailable ($error). No additional permission is needed."
            enabled == false -> "Notification access is off for Gajala. Enable it in Android settings to verify playback."
            else -> "Gajala could not check notification access. This does not mean permission was denied."
        }
        val services = discoverYtMusicServices()
        return mapOf("enabled" to enabled, "sessionReadable" to readable,
            "musicControlEnabled" to YouTubeMusicAccessibilityService.isEnabled(activity),
            "musicControlConnected" to (YouTubeMusicAccessibilityService.startProbe()),
            "listenerConnected" to (GajalaNotificationListener.instance != null),
            "activePlayers" to sessions?.map { it.packageName }?.distinct(),
            "error" to error, "message" to message, "ytmServices" to services,
            "lastPlaybackDiagnostics" to activity.getSharedPreferences("music_diagnostics", Context.MODE_PRIVATE).getString("last_result", null),
            "directProbe" to probe.filterKeys { it != "sessionToken" && it != "_browser" })
    }

    private fun discoverYtMusicServices(): List<String> = try {
        val query = Intent(android.service.media.MediaBrowserService.SERVICE_INTERFACE)
        activity.packageManager.queryIntentServices(query, PackageManager.MATCH_ALL)
            .filter { it.serviceInfo.packageName == YOUTUBE_MUSIC }
            .map { it.serviceInfo.name }.take(12)
    } catch (e: Exception) { emptyList() }

    /** Connect only to YouTube Music's declared browser service; connection is
     * evidence of a service, never evidence of searchable catalog/playback. */
    private fun probeYtMusic(requestId: String = UUID.randomUUID().toString(), done: (Map<String, Any?>) -> Unit) {
        val started = System.currentTimeMillis()
        val services = discoverYtMusicServices()
        if (services.isEmpty()) {
            val probe = mapOf<String, Any?>("requestId" to requestId, "stage" to "service_discovery",
                "status" to "unsupported", "reason" to "No YouTube Music MediaBrowserService is visible.",
                "browserConnected" to false, "playFromSearchSupported" to false,
                "browseSearch" to "unknown (no browser service)", "durationMs" to (System.currentTimeMillis() - started))
            lastMusicProbe = probe
            done(probe); return
        }
        val component = ComponentName(YOUTUBE_MUSIC, services.first())
        var settled = false
        lateinit var browser: MediaBrowser
        try {
            browser = MediaBrowser(activity, component, object : MediaBrowser.ConnectionCallback() {
                override fun onConnected() {
                    if (settled) return
                    settled = true
                    if (disposed) {
                        finishMusicBrowser(mapOf("_browser" to browser))
                        return
                    }
                    val token = try { browser.sessionToken } catch (_: Exception) { null }
                    val controller = try { token?.let { PlatformMediaController(activity, it) } } catch (_: Exception) { null }
                    val playbackState = controller?.playbackState
                    val actions = playbackState?.actions ?: 0L
                    val supportsPlayFromSearch: Boolean? = playbackState?.let {
                        actions and PlaybackState.ACTION_PLAY_FROM_SEARCH != 0L
                    }
                    val base = linkedMapOf<String, Any?>("requestId" to requestId,
                        "app" to YOUTUBE_MUSIC, "appVersion" to packageVersion(YOUTUBE_MUSIC),
                        "service" to services.first(),
                        "stage" to "session_capabilities", "status" to "connected",
                        "browserConnected" to true, "sessionAvailable" to (token != null),
                        "browseRootAvailable" to true,
                        "browseRootExtraKeys" to browser.extras?.keySet()?.take(20),
                        "playFromSearchSupported" to supportsPlayFromSearch,
                        "supportedActions" to actions, "browseSearch" to "unknown (platform MediaBrowser has no search API)",
                        "durationMs" to (System.currentTimeMillis() - started),
                        "reason" to when (supportsPlayFromSearch) {
                            true -> "Session advertises playFromSearch; execution still requires playback verification."
                            false -> "Connected, but session does not advertise playFromSearch."
                            null -> "Connected, but no playback state is published, so supported commands are unknown."
                        })
                    lastMusicProbe = base
                    done(base + mapOf("sessionToken" to token, "_browser" to browser))
                }
                override fun onConnectionFailed() {
                    if (settled) return
                    settled = true
                    musicBrowsers.remove(browser)
                    try { browser.disconnect() } catch (_: Exception) {}
                    val probe = mapOf<String, Any?>("requestId" to requestId, "stage" to "browser_connection",
                        "status" to "failed", "browserConnected" to false, "playFromSearchSupported" to false,
                        "browseSearch" to "not_probed", "reason" to "YouTube Music's browser service rejected the connection.",
                        "durationMs" to (System.currentTimeMillis() - started))
                    lastMusicProbe = probe; done(probe)
                }
            }, null)
        } catch (e: Exception) {
            val probe = mapOf<String, Any?>("requestId" to requestId, "stage" to "browser_bind",
                "status" to "failed", "browserConnected" to false, "playFromSearchSupported" to false,
                "browseSearch" to "not_probed", "reason" to "${e.javaClass.simpleName}: browser bind failed.")
            lastMusicProbe = probe; done(probe); return
        }
        musicBrowsers.add(browser)
        try { browser.connect() } catch (e: Exception) {
            settled = true
            musicBrowsers.remove(browser)
            try { browser.disconnect() } catch (_: Exception) {}
            val probe = mapOf<String, Any?>("requestId" to requestId, "stage" to "browser_bind", "status" to "failed",
                "browserConnected" to false, "playFromSearchSupported" to false, "browseSearch" to "not_probed",
                "reason" to "${e.javaClass.simpleName}: browser connection failed.")
            lastMusicProbe = probe; done(probe)
        }
        Handler(Looper.getMainLooper()).postDelayed({
            if (!settled) {
                settled = true
                try { browser.disconnect() } catch (_: Exception) {}
                musicBrowsers.remove(browser)
                val probe = mapOf<String, Any?>("requestId" to requestId, "stage" to "browser_connection",
                    "status" to "timeout", "browserConnected" to false, "playFromSearchSupported" to false,
                    "browseSearch" to "not_probed", "reason" to "YouTube Music browser connection timed out.", "durationMs" to (System.currentTimeMillis() - started))
                lastMusicProbe = probe; done(probe)
            }
        }, 2500)
    }

    private fun finishMusicBrowser(probe: Map<String, Any?>) {
        val browser = probe["_browser"] as? MediaBrowser ?: return
        musicBrowsers.remove(browser)
        try { browser.disconnect() } catch (_: Exception) {}
    }

    private fun packageVersion(pkg: String): String? = try {
        @Suppress("DEPRECATION")
        activity.packageManager.getPackageInfo(pkg, 0).versionName
    } catch (_: Exception) { null }

    private fun musicDiagnostic(stage: String, outcome: String, reason: String,
                                probe: Map<String, Any?>): Map<String, Any?> = mapOf(
        "requestId" to (probe["requestId"] ?: UUID.randomUUID().toString()), "stage" to stage,
        "app" to YOUTUBE_MUSIC, "appVersion" to probe["appVersion"],
        "adapter" to when {
            stage == "direct_execution" -> "ytm_media_session"
            stage == "accessibility_execution" -> "ytm_accessibility"
            else -> "ytm_intent"
        },
        "outcome" to outcome, "reason" to reason, "probeStage" to probe["stage"],
        "probeDurationMs" to probe["durationMs"], "chosenMediaId" to null,
        "networkConnectivity" to "not_checked", "notificationAccess" to notificationListenerEnabled(),
        "browserConnected" to probe["browserConnected"],
        "playFromSearchSupported" to probe["playFromSearchSupported"],
        "browseSearch" to probe["browseSearch"])

    private fun notificationListenerEnabled(): Boolean? = try {
        val component = ComponentName(activity, GajalaNotificationListener::class.java)
        if (Build.VERSION.SDK_INT >= 27) {
            (activity.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                .isNotificationListenerAccessGranted(component)
        } else {
            Settings.Secure.getString(activity.contentResolver, "enabled_notification_listeners")
                .orEmpty().split(':').any { ComponentName.unflattenFromString(it) == component }
        }
    } catch (_: Exception) { null }

    private fun musicApps(): List<Map<String, String>> {
        val intent = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH)
        return activity.packageManager.queryIntentActivities(intent, PackageManager.MATCH_DEFAULT_ONLY)
            .map { mapOf("package" to it.activityInfo.packageName,
                "label" to it.loadLabel(activity.packageManager).toString()) }
            .distinctBy { it["package"] }
    }

    private fun request(kind: String, permission: String, result: MethodChannel.Result, query: String? = null) {
        pending?.result?.success(mapOf("status" to "cancelled", "message" to "Superseded by a newer phone action."))
        pending = Pending(kind, result, query)
        activity.requestPermissions(arrayOf(permission), if (kind == "contacts") CONTACTS_PERMISSION else CALL_PERMISSION)
    }

    private fun has(permission: String) = ContextCompat.checkSelfPermission(activity, permission) == PackageManager.PERMISSION_GRANTED

    private fun normalise(number: String) = number.filter { it.isDigit() || it == '+' }

    private fun isEmergency(number: String): Boolean {
        val compact = number.filter(Char::isDigit)
        if (compact in EMERGENCY_NUMBERS) return true
        return try {
            @Suppress("DEPRECATION")
            PhoneNumberUtils.isEmergencyNumber(number)
        } catch (_: Throwable) { false }
    }

    companion object {
        const val CONTACTS_PERMISSION = 7311
        const val CALL_PERMISSION = 7312
        const val MAX_CONTACTS = 8
        const val YOUTUBE_MUSIC = "com.google.android.apps.youtube.music"
        private const val VERIFY_MS = 9000L          // room for the session nudge to start audio
        private const val NUDGE_AFTER_MS = 1500L     // let the launched app create its session
        private const val VERIFY_INTERVAL_MS = 250L
        private val GENERIC_MUSIC_WORDS = setOf("play", "music", "song", "songs", "track", "tracks", "listen", "hindi", "please")
        private val EMERGENCY_NUMBERS = setOf("911", "112", "999", "100", "101", "102", "108")
    }
}
