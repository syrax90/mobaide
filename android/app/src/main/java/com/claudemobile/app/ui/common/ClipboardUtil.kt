package com.claudemobile.app.ui.common

import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.PersistableBundle

/**
 * Copy [text] to the system clipboard. When [isSensitive] is true and
 * the device runs API 33+, the clipboard preview is suppressed via
 * [ClipDescription.EXTRA_IS_SENSITIVE].
 */
fun Context.copyToClipboard(label: String, text: String, isSensitive: Boolean = false) {
    val clip = ClipData.newPlainText(label, text)
    if (isSensitive && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        clip.description.extras = PersistableBundle().apply {
            putBoolean(ClipDescription.EXTRA_IS_SENSITIVE, true)
        }
    }
    val manager = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    manager.setPrimaryClip(clip)
}
