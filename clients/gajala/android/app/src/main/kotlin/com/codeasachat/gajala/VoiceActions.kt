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

    private var pending: Pending? = null
    private var disposed = false
    private var verifyHandler: Handler? = null
    private var verifyRunnable: Runnable? = null

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
            "musicAccess" -> result.success(musicAccess())
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
        val extras = searchExtras(text)
        val intent = Intent(MediaStore.INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH).apply {
            putExtra(android.app.SearchManager.QUERY, text)
            putExtras(extras)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            setPackage(targetPackage)
        }
        val manager = mediaManager()
        val before = activeMedia(manager, targetPackage)
        val beforeToken = before?.sessionToken
        val beforeTitle = before?.metadata?.getString(MediaMetadata.METADATA_KEY_TITLE).orEmpty()
        val beforeArtist = before?.metadata?.getString(MediaMetadata.METADATA_KEY_ARTIST).orEmpty()
        if (targetPackage == YOUTUBE_MUSIC &&
            YouTubeMusicAccessibilityService.isEnabled(activity) &&
            YouTubeMusicAccessibilityService.start(text) { outcome ->
                if (disposed) return@start
                when (outcome) {
                    YouTubeMusicAccessibilityService.Outcome.SELECTED ->
                        verifyPlaybackAsync(manager, targetPackage, text, extras,
                            beforeToken, beforeTitle, beforeArtist, result, allowSessionNudge = false)
                    YouTubeMusicAccessibilityService.Outcome.SELECTED_COLLECTION ->
                        verifyPlaybackAsync(manager, targetPackage, text, extras,
                            beforeToken, beforeTitle, beforeArtist, result,
                            allowSessionNudge = false, selectedCollection = true)
                    YouTubeMusicAccessibilityService.Outcome.SEARCHED ->
                        result.success(mapOf("status" to "searched", "package" to targetPackage,
                            "query" to text,
                            "message" to "Searched YouTube Music, but no safe matching song result was selected. Playback was not verified."))
                    YouTubeMusicAccessibilityService.Outcome.FAILED ->
                        result.success(mapOf("status" to "unsupported", "package" to targetPackage,
                            "query" to text, "message" to "YouTube Music could not be opened for controlled playback."))
                    YouTubeMusicAccessibilityService.Outcome.CANCELLED ->
                        result.success(mapOf("status" to "cancelled", "package" to targetPackage,
                            "query" to text, "message" to "YouTube Music playback was cancelled."))
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
                    "query" to text,
                    "message" to "Opened YouTube Music and searched for $text. Playback was not verified."))
                return
            }
            result.success(mapOf("status" to "unsupported", "package" to targetPackage,
                "query" to text, "message" to "Music app cannot start playback search."))
            return
        } catch (_: SecurityException) {
            if (targetPackage == YOUTUBE_MUSIC && openYouTubeMusicSearch(text)) {
                result.success(mapOf("status" to "searched", "package" to targetPackage,
                    "query" to text,
                    "message" to "Opened YouTube Music and searched for $text. Playback was not verified."))
                return
            }
            result.success(mapOf("status" to "unsupported", "package" to targetPackage,
                "query" to text, "message" to "Music app rejected playback search."))
            return
        }
        val access = musicAccess()
        if (access["sessionReadable"] != true) {
            result.success(mapOf("status" to "requested", "package" to targetPackage, "query" to text,
                "access" to access,
                "message" to "Opened the search, but Gajala cannot start playback without notification " +
                    "access (it plays through the app's media session). ${access["message"]}"))
            return
        }
        verifyPlaybackAsync(manager, targetPackage, text, extras, beforeToken, beforeTitle, beforeArtist, result)
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
                                    allowSessionNudge: Boolean = true, selectedCollection: Boolean = false) {
        if (manager == null) {
            result.success(mapOf("status" to "unverified", "package" to packageName,
                "message" to "Playback was requested but no media session is available."))
            return
        }
        val handler = Handler(Looper.getMainLooper())
        verifyHandler = handler
        val started = System.currentTimeMillis()
        var nudged = false
        val check = object : Runnable {
            override fun run() {
                if (disposed) return
                val current = activeMedia(manager, packageName)
                val state = current?.playbackState?.state
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
                if (state == PlaybackState.STATE_PLAYING && title.isNotBlank() && matched && changed) {
                    verifyRunnable = null
                    result.success(mapOf("status" to "playing", "package" to packageName,
                        "title" to title, "artist" to artist, "message" to "Playback verified."))
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
                    result.success(mapOf("status" to "unverified", "package" to packageName,
                        "title" to title, "artist" to artist,
                        "message" to "Playback was requested, but Gajala could not verify the matching track started."))
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
    private fun musicAccess(): Map<String, Any?> {
        val component = ComponentName(activity, GajalaNotificationListener::class.java)
        val enabled: Boolean? = try {
            if (Build.VERSION.SDK_INT >= 27) {
                (activity.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
                    .isNotificationListenerAccessGranted(component)
            } else {
                Settings.Secure.getString(activity.contentResolver, "enabled_notification_listeners")
                    .orEmpty().split(':').any { ComponentName.unflattenFromString(it) == component }
            }
        } catch (_: Exception) { null }
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
        return mapOf("enabled" to enabled, "sessionReadable" to readable,
            "musicControlEnabled" to YouTubeMusicAccessibilityService.isEnabled(activity),
            "musicControlConnected" to (YouTubeMusicAccessibilityService.startProbe()),
            "listenerConnected" to (GajalaNotificationListener.instance != null),
            "activePlayers" to sessions?.map { it.packageName }?.distinct(),
            "error" to error, "message" to message)
    }

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
