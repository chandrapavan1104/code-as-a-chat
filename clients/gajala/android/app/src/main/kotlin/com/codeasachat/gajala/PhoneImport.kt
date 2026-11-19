package com.codeasachat.gajala

import android.content.Intent
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.result.contract.ActivityResultContracts
import androidx.lifecycle.lifecycleScope
import io.flutter.plugin.common.BinaryMessenger
import io.flutter.plugin.common.MethodChannel
import java.io.File
import java.io.IOException
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Keep URI grants native and expose only selected files to the transfer UI. */
internal class PhoneImport(private val activity: MainActivity) {
    private data class Source(val id: String, val uri: Uri)
    private val selected = mutableMapOf<String, Source>()
    private val pending = mutableListOf<Source>()
    private val directory = File(activity.cacheDir, "phone-import")
    private var channel: MethodChannel? = null
    private var pickerResult: MethodChannel.Result? = null
    private val picker = activity.registerForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments(),
    ) { uris ->
        val result = pickerResult ?: return@registerForActivityResult
        pickerResult = null
        describe(sources(uris), result)
    }

    fun accept(intent: Intent?) {
        if (intent?.action !in setOf(Intent.ACTION_SEND, Intent.ACTION_SEND_MULTIPLE)) return
        @Suppress("DEPRECATION")
        val uris = if (intent?.action == Intent.ACTION_SEND_MULTIPLE) {
            intent.getParcelableArrayListExtra<Uri>(Intent.EXTRA_STREAM).orEmpty()
        } else {
            listOfNotNull(intent?.getParcelableExtra<Uri>(Intent.EXTRA_STREAM))
        }
        val clip = intent?.clipData
        val items = if (uris.isEmpty() && clip != null) {
            (0 until minOf(clip.itemCount, MAX_BATCH)).mapNotNull { clip.getItemAt(it).uri }
        } else uris
        pending.addAll(sources(items).take(MAX_BATCH - pending.size))
        // Once captured, activity recreation must not reopen the review.
        intent?.action = null
        if (pending.isNotEmpty()) channel?.invokeMethod("incoming", null)
    }

    fun connect(messenger: BinaryMessenger) {
        channel = MethodChannel(messenger, "gajala/phone_import").apply {
            setMethodCallHandler { call, result ->
                when (call.method) {
                    "consume" -> describe(pending.toList().also { pending.clear() }, result)
                    "pick" -> {
                        if (pickerResult != null) {
                            result.error("busy", "The file picker is already open.", null)
                        } else {
                            pickerResult = result
                            picker.launch(arrayOf("*/*"))
                        }
                    }
                    "stage" -> {
                        val source = selected[call.arguments as? String]
                        if (source == null) {
                            result.error("missing", "Select the file again to give Gajala access.", null)
                        } else {
                            activity.lifecycleScope.launch {
                                val staged = withContext(Dispatchers.IO) { runCatching { stage(source) } }
                                staged.fold(
                                    { result.success(it) },
                                    { result.error("unreadable", it.message ?: "Could not read the file.", null) },
                                )
                            }
                        }
                    }
                    "release" -> {
                        for (id in call.arguments as List<*>) {
                            val source = selected.remove(id) ?: continue
                            File(directory, source.id).delete()
                        }
                        result.success(null)
                    }
                    else -> result.notImplemented()
                }
            }
        }
    }

    private fun sources(uris: List<Uri>): List<Source> = uris
        .filter { it.scheme == "content" }.distinct().take(MAX_BATCH)
        .map { Source(UUID.randomUUID().toString().replace("-", ""), it) }

    private fun describe(items: List<Source>, result: MethodChannel.Result) {
        activity.lifecycleScope.launch {
            val files = withContext(Dispatchers.IO) {
                items.map { source ->
                    var name = "Shared file"
                    var size: Long? = null
                    runCatching {
                        val columns = arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE)
                        activity.contentResolver.query(source.uri, columns, null, null, null)?.use { cursor ->
                            if (cursor.moveToFirst()) {
                                name = cursor.getString(0) ?: name
                                if (!cursor.isNull(1)) size = cursor.getLong(1).takeIf { it >= 0 }
                            }
                        }
                    }
                    mapOf("id" to source.id, "name" to name, "size" to size)
                }
            }
            items.forEach { selected[it.id] = it }
            result.success(files)
        }
    }

    private fun stage(source: Source): String {
        directory.mkdirs()
        val cutoff = System.currentTimeMillis() - 24 * 60 * 60 * 1000L
        directory.listFiles()?.filter { it.lastModified() < cutoff }?.forEach { it.delete() }
        val final = File(directory, source.id)
        if (final.isFile) return final.path
        val temporary = File.createTempFile("pending-", null, directory)
        try {
            val input = activity.contentResolver.openInputStream(source.uri)
                ?: throw IOException("That file is no longer available on your phone.")
            input.use { stream ->
                temporary.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    var size = 0L
                    while (true) {
                        val count = stream.read(buffer)
                        if (count < 0) break
                        size += count
                        if (size > MAX_BYTES) throw IOException("Each file can be up to 200 MiB.")
                        output.write(buffer, 0, count)
                    }
                }
            }
            if (!temporary.renameTo(final)) throw IOException("Could not prepare the file for upload.")
            return final.path
        } catch (error: SecurityException) {
            throw IOException("Select the file again to give Gajala access.", error)
        } finally {
            temporary.delete()
        }
    }

    private companion object {
        const val MAX_BATCH = 10
        const val MAX_BYTES = 200L * 1024 * 1024
    }
}
