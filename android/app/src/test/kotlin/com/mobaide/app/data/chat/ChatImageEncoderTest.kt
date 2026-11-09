package com.mobaide.app.data.chat

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.media.ExifInterface
import android.net.Uri
import android.util.Base64
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * [ChatImageEncoder.encode] over a content Uri served by the shadow resolver, so the
 * encoder opens, bounds-decodes and decodes real PNG bytes the way a picked image does.
 * Native graphics give BitmapFactory a real codec; under the legacy shadow it reports
 * no size at all. SDK pinned to 33 to match the rest of the JVM/Robolectric suite.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class ChatImageEncoderTest {

    private lateinit var encoder: ChatImageEncoder

    @Before
    fun setUp() {
        encoder = ChatImageEncoder(RuntimeEnvironment.getApplication())
    }

    @Test
    fun aPickedPng_isEncodedAsAJpegOfTheSameSize() = runTest {
        val uri = serve("content://media/external/images/1", png(width = 40, height = 20))

        val image = encoder.encode(uri)

        assertNotNull("a bounds-only decode returns null by design; that is not a failure", image)
        assertEquals("image/jpeg", image!!.payload.mime)
        val bytes = Base64.decode(image.payload.data, Base64.NO_WRAP)
        val sent = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
        assertEquals(40, sent.width)
        assertEquals(20, sent.height)
        assertEquals(40, image.thumbnail.width)
        assertEquals(20, image.thumbnail.height)
    }

    @Test
    fun bytesThatAreNotAnImage_areRejected() = runTest {
        val uri = serve("content://media/external/images/2", "not an image".toByteArray())

        assertNull(encoder.encode(uri))
    }

    @Test
    fun everyExifOrientation_isAppliedBeforeEncoding() = runTest {
        // A 40x20 photo, red in its top-left quadrant: where the red lands
        // after encoding shows the rotation and the mirroring applied.
        val cases = listOf(
            Triple(ExifInterface.ORIENTATION_NORMAL, "top-left", 40),
            Triple(ExifInterface.ORIENTATION_FLIP_HORIZONTAL, "top-right", 40),
            Triple(ExifInterface.ORIENTATION_ROTATE_180, "bottom-right", 40),
            Triple(ExifInterface.ORIENTATION_FLIP_VERTICAL, "bottom-left", 40),
            Triple(ExifInterface.ORIENTATION_TRANSPOSE, "top-left", 20),
            Triple(ExifInterface.ORIENTATION_ROTATE_90, "top-right", 20),
            Triple(ExifInterface.ORIENTATION_TRANSVERSE, "bottom-right", 20),
            Triple(ExifInterface.ORIENTATION_ROTATE_270, "bottom-left", 20),
        )
        cases.forEachIndexed { index, (orientation, redCorner, width) ->
            val uri = serve("content://media/external/images/exif-$index", quadrantJpeg(orientation))

            val image = encoder.encode(uri)

            assertNotNull("orientation $orientation", image)
            val bytes = Base64.decode(image!!.payload.data, Base64.NO_WRAP)
            val sent = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
            assertEquals("width for orientation $orientation", width, sent.width)
            assertEquals("red corner for orientation $orientation", redCorner, redCorner(sent))
        }
    }

    /** Which quadrant of [bitmap] is mostly red. */
    private fun redCorner(bitmap: Bitmap): String {
        val xs = listOf(bitmap.width / 4, bitmap.width * 3 / 4)
        val ys = listOf(bitmap.height / 4, bitmap.height * 3 / 4)
        val names = mapOf(
            (0 to 0) to "top-left",
            (1 to 0) to "top-right",
            (0 to 1) to "bottom-left",
            (1 to 1) to "bottom-right",
        )
        val red = names.keys.filter { (cx, cy) ->
            val pixel = bitmap.getPixel(xs[cx], ys[cy])
            Color.red(pixel) > 200 && Color.blue(pixel) < 80
        }
        return red.singleOrNull()?.let { names.getValue(it) } ?: "none of ${red.size}"
    }

    /** A 40x20 JPEG, red top-left quadrant on blue, stored with EXIF [orientation]. */
    private fun quadrantJpeg(orientation: Int): ByteArray {
        val bitmap = Bitmap.createBitmap(40, 20, Bitmap.Config.ARGB_8888)
        bitmap.eraseColor(Color.BLUE)
        for (x in 0 until 20) for (y in 0 until 10) bitmap.setPixel(x, y, Color.RED)
        val file = File.createTempFile("exif", ".jpg")
        try {
            file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.JPEG, 100, it) }
            ExifInterface(file.absolutePath).apply {
                setAttribute(ExifInterface.TAG_ORIENTATION, orientation.toString())
                saveAttributes()
            }
            return file.readBytes()
        } finally {
            file.delete()
        }
    }

    /** The encoder opens the Uri more than once, so every open gets a fresh stream. */
    private fun serve(uri: String, bytes: ByteArray): Uri {
        val parsed = Uri.parse(uri)
        shadowOf(RuntimeEnvironment.getApplication().contentResolver)
            .registerInputStreamSupplier(parsed) { ByteArrayInputStream(bytes) }
        return parsed
    }

    private fun png(width: Int, height: Int): ByteArray {
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        bitmap.eraseColor(Color.RED)
        return ByteArrayOutputStream().use { out ->
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)
            out.toByteArray()
        }
    }
}
