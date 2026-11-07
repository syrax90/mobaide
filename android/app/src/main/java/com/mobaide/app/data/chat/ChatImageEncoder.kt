package com.mobaide.app.data.chat

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Matrix
import android.media.ExifInterface
import android.net.Uri
import android.util.Base64
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.math.max
import kotlin.math.roundToInt

/** A picked image, downscaled and JPEG-encoded for a `prompt`, plus a small preview. */
class EncodedChatImage(
    val payload: ChatImagePayload,
    val thumbnail: Bitmap,
)

/**
 * Turns a picked image into what a chat prompt may carry: the longest side at most
 * [MAX_SIDE] px, JPEG, and base64 no larger than [MAX_BASE64]. EXIF rotation is
 * applied first so a phone photo is not sent sideways.
 */
@Singleton
class ChatImageEncoder @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    suspend fun encode(uri: Uri): EncodedChatImage? = withContext(Dispatchers.IO) {
        runCatching { encodeBlocking(uri) }.getOrNull()
    }

    private fun encodeBlocking(uri: Uri): EncodedChatImage? {
        val resolver = context.contentResolver
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        // A bounds-only decode always returns null; the result is in `bounds`.
        val header = resolver.openInputStream(uri) ?: return null
        header.use { BitmapFactory.decodeStream(it, null, bounds) }
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return null

        var sample = 1
        while (max(bounds.outWidth, bounds.outHeight) / (sample * 2) >= MAX_SIDE) sample *= 2
        val decoded = resolver.openInputStream(uri)?.use {
            BitmapFactory.decodeStream(it, null, BitmapFactory.Options().apply { inSampleSize = sample })
        } ?: return null

        val rotation = runCatching {
            resolver.openInputStream(uri)?.use { stream ->
                when (ExifInterface(stream).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
                    ExifInterface.ORIENTATION_ROTATE_90 -> 90f
                    ExifInterface.ORIENTATION_ROTATE_180 -> 180f
                    ExifInterface.ORIENTATION_ROTATE_270 -> 270f
                    else -> 0f
                }
            }
        }.getOrNull() ?: 0f

        var bitmap = scaled(decoded, MAX_SIDE, rotation)
        if (bitmap.hasAlpha()) bitmap = onWhite(bitmap)

        var quality = 80
        var data = jpegBase64(bitmap, quality)
        while (data.length > MAX_BASE64) {
            if (quality > 50) {
                quality -= 15
            } else {
                val side = (max(bitmap.width, bitmap.height) * 0.75f).roundToInt()
                if (side < MIN_SIDE) return null
                bitmap = scaled(bitmap, side, 0f)
            }
            data = jpegBase64(bitmap, quality)
        }
        return EncodedChatImage(
            payload = ChatImagePayload(mime = "image/jpeg", data = data),
            thumbnail = scaled(bitmap, THUMB_SIDE, 0f),
        )
    }

    private fun scaled(source: Bitmap, maxSide: Int, rotation: Float): Bitmap {
        val longest = max(source.width, source.height)
        val factor = if (longest > maxSide) maxSide.toFloat() / longest else 1f
        if (factor == 1f && rotation == 0f) return source
        val matrix = Matrix().apply {
            postScale(factor, factor)
            if (rotation != 0f) postRotate(rotation)
        }
        return Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, true)
    }

    private fun onWhite(source: Bitmap): Bitmap {
        val out = Bitmap.createBitmap(source.width, source.height, Bitmap.Config.ARGB_8888)
        Canvas(out).apply {
            drawColor(Color.WHITE)
            drawBitmap(source, 0f, 0f, null)
        }
        return out
    }

    private fun jpegBase64(bitmap: Bitmap, quality: Int): String {
        val bytes = ByteArrayOutputStream().use { out ->
            bitmap.compress(Bitmap.CompressFormat.JPEG, quality, out)
            out.toByteArray()
        }
        return Base64.encodeToString(bytes, Base64.NO_WRAP)
    }

    companion object {
        const val MAX_IMAGES = 4
        const val MAX_SIDE = 1568
        private const val MIN_SIDE = 256
        private const val THUMB_SIDE = 192
        private const val MAX_BASE64 = 1_500_000
    }
}
