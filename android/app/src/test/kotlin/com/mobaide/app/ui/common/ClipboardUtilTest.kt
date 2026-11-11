package com.mobaide.app.ui.common

import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.net.Uri
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * Unit tests for [hasClipboardText] / [readClipboardText] — the two halves of the
 * terminal selection-toolbar Paste gate.
 *
 * [hasClipboardText] backs the menu-visibility gate and must stay strict: only
 * `text/plain` or `text/html` clip *descriptions* count. A `text/uri-list` clip
 * must be rejected even though it is nominally "text" — coercing it would open a
 * ContentProvider synchronously on the main thread, and merely reading clip
 * contents at menu-build time would fire the Android 12+ clipboard-access toast.
 *
 * [readClipboardText] is the on-tap read and must return null (paste no-op) for
 * anything the gate would not have offered, and for text that coerces to empty.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class ClipboardUtilTest {

    private val context: Context = RuntimeEnvironment.getApplication()
    private val clipboard: ClipboardManager
        get() = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager

    @Test
    fun hasClipboardText_plainTextClip_returnsTrue() {
        clipboard.setPrimaryClip(ClipData.newPlainText("label", "hello"))

        assertTrue(context.hasClipboardText())
    }

    @Test
    fun hasClipboardText_uriClip_returnsFalse() {
        clipboard.setPrimaryClip(
            ClipData.newRawUri("label", Uri.parse("content://com.example.provider/item/1")),
        )

        // Pins the strict plain/html filter: text/uri-list must NOT count as
        // pasteable text — a wildcard text/* match would admit clips whose
        // coerceToText hits a ContentProvider on the main thread.
        assertFalse(context.hasClipboardText())
    }

    @Test
    fun readClipboardText_plainTextClip_returnsText() {
        clipboard.setPrimaryClip(ClipData.newPlainText("label", "paste me"))

        assertEquals("paste me", context.readClipboardText())
    }

    @Test
    fun readClipboardText_emptyString_returnsNull() {
        clipboard.setPrimaryClip(ClipData.newPlainText("label", ""))

        // The description advertises text, but an empty coerced string must read
        // as null so the paste sink no-ops instead of pasting nothing.
        assertNull(context.readClipboardText())
    }

    @Test
    fun readClipboardText_uriOnlyItemWithTextPlainDescription_returnsNull() {
        clipboard.setPrimaryClip(
            ClipData(
                ClipDescription("label", arrayOf(ClipDescription.MIMETYPE_TEXT_PLAIN)),
                ClipData.Item(Uri.parse("content://com.example.provider/item/1")),
            ),
        )

        // ClipData.newUri-style clips put the provider MIME type in the
        // description, so the description gate passes...
        assertTrue(context.hasClipboardText())
        // ...and it is the URI-only item guard that must block the read: coercing
        // a URI-backed item would open its ContentProvider synchronously on the
        // paste tap. Without the guard this returns the uri string, not null.
        assertNull(context.readClipboardText())
    }

    @Test
    fun readClipboardText_itemWithBothTextAndUri_returnsText() {
        clipboard.setPrimaryClip(
            ClipData(
                ClipDescription("label", arrayOf(ClipDescription.MIMETYPE_TEXT_PLAIN)),
                ClipData.Item("attached text", null, Uri.parse("content://com.example.provider/item/1")),
            ),
        )

        // The guard rejects URI-only items; an item that also carries text must
        // still paste it.
        assertEquals("attached text", context.readClipboardText())
    }
}
