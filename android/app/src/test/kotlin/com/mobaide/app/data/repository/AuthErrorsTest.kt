package com.mobaide.app.data.repository

import org.junit.Assert.assertEquals
import org.junit.Test

/** Unit tests for [loginRateLimitedMessage]; the wording matches iOS and web. */
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
}
