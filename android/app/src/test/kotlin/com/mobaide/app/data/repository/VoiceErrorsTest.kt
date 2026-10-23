package com.mobaide.app.data.repository

import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.voice.VoiceApiException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

/**
 * [mapVoiceError]: voice refusals carry `{"detail": {"code", "message", …}}`
 * on any status, and the uploader branches on the code and `expected_seq`.
 */
class VoiceErrorsTest {

    @Test
    fun codedRefusal_liftsCodeMessageAndExpectedSeq() {
        val cause = ApiException(
            409,
            """{"detail":{"code":"seq_gap","message":"An earlier audio chunk is missing.","expected_seq":3}}""",
        )

        val mapped = mapVoiceError(cause)

        assertEquals(409, mapped.status)
        assertEquals("seq_gap", mapped.code)
        assertEquals("An earlier audio chunk is missing.", mapped.detailMessage)
        assertEquals(3, mapped.expectedSeq)
        assertFalse(mapped.network)
        assertSame(cause, mapped.cause)
    }

    @Test
    fun refusalWithoutACodedDetail_isAnUncodedStatus() {
        val mapped = mapVoiceError(ApiException(502, "<html>Bad gateway</html>"))

        assertEquals(502, mapped.status)
        assertNull(mapped.code)
        assertNull(mapped.expectedSeq)
        assertFalse(mapped.network)
    }

    @Test
    fun ioFailure_isNetwork() {
        val mapped = mapVoiceError(IOException("connection reset"))

        assertNull(mapped.status)
        assertTrue(mapped.network)
    }

    @Test
    fun voiceApiException_passesThrough() {
        val original = VoiceApiException(status = 409, code = "too_long", message = "x")

        assertSame(original, mapVoiceError(original))
    }
}
