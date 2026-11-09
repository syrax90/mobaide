package com.mobaide.app.data.chat

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import kotlin.math.max

/** The outcome of decoding an image the agent sent. */
sealed interface DecodedChatImage {
    data class Ready(val bitmap: Bitmap) : DecodedChatImage
    data object TooLarge : DecodedChatImage
    data object Unreadable : DecodedChatImage
}

/**
 * Decodes agent-supplied image bytes without trusting their header: the size is
 * read first, images over [MAX_PIXELS] are refused, and the rest are subsampled
 * as far as the box they are drawn in allows, never above [MAX_SIDE] px on the
 * longest side. Blocking: call off the main thread.
 */
object ChatImageDecoder {
    const val MAX_PIXELS = 40_000_000L
    const val MAX_SIDE = 2048

    /** [maxWidth] x [maxHeight] is the box, in px, the image is fitted into on screen. */
    fun decodeBase64(data: String, maxWidth: Int, maxHeight: Int): DecodedChatImage {
        val bytes = runCatching { Base64.decode(data, Base64.DEFAULT) }.getOrNull()
            ?: return DecodedChatImage.Unreadable
        return decode(bytes, maxWidth, maxHeight)
    }

    fun decode(bytes: ByteArray, maxWidth: Int, maxHeight: Int): DecodedChatImage {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds) }
        val width = bounds.outWidth
        val height = bounds.outHeight
        if (width <= 0 || height <= 0) return DecodedChatImage.Unreadable
        if (width.toLong() * height > MAX_PIXELS) return DecodedChatImage.TooLarge
        val sample = max(sampleSize(width, height, MAX_SIDE), fitSampleSize(width, height, maxWidth, maxHeight))
        val options = BitmapFactory.Options().apply { inSampleSize = sample }
        val bitmap = runCatching { BitmapFactory.decodeByteArray(bytes, 0, bytes.size, options) }.getOrNull()
        return bitmap?.let { DecodedChatImage.Ready(it) } ?: DecodedChatImage.Unreadable
    }

    /** The smallest power of two that brings the longest side down to [maxSide] or less. */
    fun sampleSize(width: Int, height: Int, maxSide: Int): Int {
        val longest = max(width, height)
        var sample = 1
        while ((longest + sample - 1) / sample > maxSide) sample *= 2
        return sample
    }

    /**
     * The largest power of two that still leaves the image no smaller than it is drawn
     * when fitted, without upscaling, into a [maxWidth] x [maxHeight] box.
     */
    fun fitSampleSize(width: Int, height: Int, maxWidth: Int, maxHeight: Int): Int {
        if (maxWidth <= 0 || maxHeight <= 0) return 1
        var sample = 1L
        while (width >= maxWidth * sample * 2 || height >= maxHeight * sample * 2) sample *= 2
        return sample.toInt()
    }
}
