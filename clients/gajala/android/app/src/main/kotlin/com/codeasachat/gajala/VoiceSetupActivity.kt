package com.codeasachat.gajala

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.ContactsContract.CommonDataKinds.Phone
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.Switch
import android.widget.TextView

/** Permission and contact setup is only reachable from the unlocked app. */
class VoiceSetupActivity : Activity() {
    private lateinit var content: LinearLayout

    override fun onResume() {
        super.onResume()
        if (LockedVoice.locked(this)) { finish(); return }
        render()
    }

    private fun render() {
        content = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(32, 32, 32, 32)
        }
        setContentView(ScrollView(this).apply { addView(content) })
        label("Voice access", 24f)
        val issue = LockedVoice.unsupported(this)
        content.addView(Switch(this).apply {
            text = "Use while locked"
            isChecked = LockedVoice.enabled(this@VoiceSetupActivity)
            isEnabled = issue == null || isChecked
            setOnCheckedChangeListener { _, on ->
                if (!LockedVoice.locked(this@VoiceSetupActivity) && (!on || issue == null)) {
                    LockedVoice.prefs(this@VoiceSetupActivity).edit().putBoolean("enabled", on).apply()
                    WakeWordService.instance?.syncScreen()
                }
            }
        })
        label(issue ?: "Device compatibility is unverified until you try this on your phone. Anyone nearby can speak an allowed command or confirm an allowed call.")
        if (issue != null) {
            button("Android assistant settings") { open(Intent(Settings.ACTION_MANAGE_DEFAULT_APPS_SETTINGS)) }
            if (!WakeWordService.hasMic(this)) button("Allow microphone") {
                requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), 11)
            }
        }
        label("Locked call contacts", 20f)
        label("Choose individual phone numbers and unique spoken names while unlocked.")
        for (alias in LockedVoice.contacts(this).keys.sorted()) {
            button("Remove $alias") {
                AlertDialog.Builder(this).setMessage("Remove $alias from locked calls?")
                    .setNegativeButton("Keep", null)
                    .setPositiveButton("Remove") { _, _ ->
                        if (!LockedVoice.locked(this)) {
                            LockedVoice.prefs(this).edit().remove("contact:$alias").apply()
                            render()
                        }
                    }.show()
            }
        }
        button("Choose a contact") {
            open(Intent(Intent.ACTION_PICK, Phone.CONTENT_URI), CONTACT)
        }
        if (checkSelfPermission(Manifest.permission.CALL_PHONE) != PackageManager.PERMISSION_GRANTED) {
            button("Allow phone calls") { requestPermissions(arrayOf(Manifest.permission.CALL_PHONE), 12) }
        }
        label("Timer alerts", 20f)
        label(VoiceTimers(this).permissionIssue() ?: "Ready to schedule and announce timers.")
        if (Build.VERSION.SDK_INT >= 31) button("Alarms & reminders access") {
            open(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:$packageName")))
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) button("Allow timer notifications") {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 13)
        }
        if (Build.VERSION.SDK_INT >= 26) button("Timer notification settings") {
            open(Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
                .putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                .putExtra(Settings.EXTRA_CHANNEL_ID, VoiceTimers.CHANNEL))
        }
        button("Media notification access") { open(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) }
    }

    private fun open(intent: Intent, request: Int? = null) {
        if (LockedVoice.locked(this)) return
        try {
            if (request == null) startActivity(intent)
            else @Suppress("DEPRECATION") startActivityForResult(intent, request)
        } catch (_: Exception) {
            AlertDialog.Builder(this).setMessage("Android could not open that setting.")
                .setPositiveButton("OK", null).show()
        }
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != CONTACT || resultCode != RESULT_OK || LockedVoice.locked(this)) return
        val uri = data?.data ?: return
        val contact = runCatching {
            contentResolver.query(uri, arrayOf(Phone.DISPLAY_NAME, Phone.NUMBER), null, null, null)?.use {
                if (it.moveToFirst()) it.getString(0) to it.getString(1) else null
            }
        }.getOrNull() ?: return
        val number = contact.second.filterNot { it in " ()-." }
        if (!Regex("^\\+?[0-9]{7,15}$").matches(number)) {
            AlertDialog.Builder(this).setMessage("Choose a normal phone number without an extension.")
                .setPositiveButton("OK", null).show()
            return
        }
        val input = EditText(this).apply { setText(contact.first) }
        val dialog = AlertDialog.Builder(this).setTitle("Name to say")
            .setView(input).setNegativeButton("Cancel", null).setPositiveButton("Save", null).create()
        dialog.setOnShowListener {
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener {
                if (LockedVoice.locked(this)) { dialog.dismiss(); return@setOnClickListener }
                val alias = VoiceCommand.normalize(input.text.toString())
                if (!Regex("[a-z][a-z ]{0,39}").matches(alias)) {
                    input.error = "Use a spoken name with letters and spaces."
                } else if (LockedVoice.contacts(this).containsKey(alias)) {
                    input.error = "That spoken name is already in use."
                } else {
                    LockedVoice.prefs(this).edit().putString("contact:$alias", number).apply()
                    dialog.dismiss()
                    render()
                }
            }
        }
        dialog.show()
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (!LockedVoice.locked(this)) render()
    }

    private fun button(text: String, press: () -> Unit) {
        content.addView(Button(this).apply { this.text = text; setOnClickListener { press() } })
    }

    private fun label(text: String, size: Float = 16f) {
        content.addView(TextView(this).apply {
            this.text = text
            textSize = size
            setPadding(0, 12, 0, 12)
        })
    }

    companion object { private const val CONTACT = 70 }
}
