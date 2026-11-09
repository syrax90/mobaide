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
 * [MAX_SIDE] px, JPEG, and base64 no larger than [MAX_BASE64]. The EXIF orientation
 * (rotation and mirroring) is applied first: the JPEG carries no EXIF, so a phone
 * photo would otherwise be sent sideways or mirrored.
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

        val orientation = runCatching {
            resolver.openInputStream(uri)?.use { stream ->
                ExifInterface(stream).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)
            }
        }.getOrNull() ?: ExifInterface.ORIENTATION_NORMAL

        var bitmap = scaled(decoded, MAX_SIDE, orientationMatrix(orientation))
        if (bitmap.hasAlpha()) bitmap = onWhite(bitmap)

        var quality = 80
        var data = jpegBase64(bitmap, quality)
        while (data.length > MAX_BASE64) {
            if (quality > 50) {
                quality -= 15
            } else {
                val side = (max(bitmap.width, bitmap.height) * 0.75f).roundToInt()
                if (side < MIN_SIDE) return null
                bitmap = scaled(bitmap, side, null)
            }
            data = jpegBase64(bitmap, quality)
        }
        return EncodedChatImage(
            payload = ChatImagePayload(mime = "image/jpeg", data = data),
            thumbnail = scaled(bitmap, THUMB_SIDE, null),
        )
    }

    /** [source] at most [maxSide] px on its longest side, then turned by [orientation]. */
    private fun scaled(source: Bitmap, maxSide: Int, orientation: Matrix?): Bitmap {
        val longest = max(source.width, source.height)
        val factor = if (longest > maxSide) maxSide.toFloat() / longest else 1f
        if (factor == 1f && orientation == null) return source
        val matrix = Matrix().apply {
            setScale(factor, factor)
            if (orientation != null) postConcat(orientation)
        }
        return Bitmap.createBitmap(source, 0, 0, source.width, source.height, matrix, true)
    }

    /** The transform that shows an image stored with EXIF [orientation] upright; null when none is needed. */
    private fun orientationMatrix(orientation: Int): Matrix? = when (orientation) {
        ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> Matrix().apply { setScale(-1f, 1f) }
        ExifInterface.ORIENTATION_ROTATE_180 -> Matrix().apply { setRotate(180f) }
        ExifInterface.ORIENTATION_FLIP_VERTICAL -> Matrix().apply { setScale(1f, -1f) }
        ExifInterface.ORIENTATION_TRANSPOSE -> Matrix().apply {
            setRotate(90f)
            postScale(-1f, 1f)
        }
        ExifInterface.ORIENTATION_ROTATE_90 -> Matrix().apply { setRotate(90f) }
        ExifInterface.ORIENTATION_TRANSVERSE -> Matrix().apply {
            setRotate(-90f)
            postScale(-1f, 1f)
        }
        ExifInterface.ORIENTATION_ROTATE_270 -> Matrix().apply { setRotate(270f) }
        else -> null
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
