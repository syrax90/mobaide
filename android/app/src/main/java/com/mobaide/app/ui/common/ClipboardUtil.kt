package com.mobaide.app.ui.common

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

/**
 * True when the clipboard advertises pasteable text. Checks the clip
 * *description* only — never the contents — so it does not fire the
 * Android 12+ "app pasted from your clipboard" toast and is safe to call
 * from menu-visibility gates. The filter is deliberately strict
 * (plain/HTML only): a `text/uri-list` or intent clip would pass a
 * `text/&#42;` wildcard, but coercing it to text can synchronously open a
 * ContentProvider on the main thread.
 */
fun Context.hasClipboardText(): Boolean {
    val manager = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    val description = manager.primaryClipDescription ?: return false
    return description.hasMimeType(ClipDescription.MIMETYPE_TEXT_PLAIN) ||
        description.hasMimeType(ClipDescription.MIMETYPE_TEXT_HTML)
}

/**
 * Read the clipboard's first item as text, or null when the clipboard has
 * no [hasClipboardText]-eligible clip or the coerced text is empty.
 * Reading the clip *contents* fires the Android 12+ clipboard-access
 * toast — call only in response to an explicit user gesture (e.g. a
 * "Paste" tap), never from gating or rendering code.
 *
 * URI-only items are rejected even when the clip *description* advertises
 * text (`ClipData.newUri` puts the provider's MIME type in the description,
 * and a description can lie outright): coercing a URI-backed item to text
 * opens its ContentProvider synchronously on the calling thread — a
 * jank/ANR and malicious-provider DoS vector on the paste tap path.
 */
fun Context.readClipboardText(): String? {
    if (!hasClipboardText()) return null
    val manager = getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    val item = manager.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0) ?: return null
    if (item.text == null && item.uri != null) return null
    return item.coerceToText(this)?.toString()?.takeIf { it.isNotEmpty() }
}
