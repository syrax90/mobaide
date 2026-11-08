package com.mobaide.app.data.repository

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Unit tests for the shared auth copy; the wording matches iOS and web. */
class AuthErrorsTest {

    @Test
    fun loginRateLimitedMessage_roundsTheWaitUpToWholeMinutes() {
        assertEquals(
            "Too many sign-in attempts. Try again in 15 minutes, or reset your password.",
            loginRateLimitedMessage(900),
        )
        assertEquals(
            "Too many sign-in attempts. Try again in 2 minutes, or reset your password.",
            loginRateLimitedMessage(61),
        )
    }

    @Test
    fun loginRateLimitedMessage_saysOneMinuteForAShortWait() {
        assertEquals(
            "Too many sign-in attempts. Try again in 1 minute, or reset your password.",
            loginRateLimitedMessage(1),
        )
        assertEquals(
            "Too many sign-in attempts. Try again in 1 minute, or reset your password.",
            loginRateLimitedMessage(60),
        )
    }

    @Test
    fun passwordLengthError_countsCodePointsLikeTheServer() {
        // Four emoji are eight UTF-16 units but four code points: too short.
        assertEquals(PASSWORD_TOO_SHORT_MESSAGE, passwordLengthError("😀😀😀😀"))
        assertNull(passwordLengthError("😀".repeat(8)))
    }

    @Test
    fun passwordLengthError_enforcesBothBounds() {
        assertEquals(PASSWORD_TOO_SHORT_MESSAGE, passwordLengthError("a".repeat(7)))
        assertNull(passwordLengthError("a".repeat(8)))
        assertNull(passwordLengthError("a".repeat(128)))
        assertEquals(PASSWORD_TOO_LONG_MESSAGE, passwordLengthError("a".repeat(129)))
    }
}
