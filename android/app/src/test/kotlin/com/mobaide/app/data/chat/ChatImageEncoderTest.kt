package com.mobaide.app.data.chat

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
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
