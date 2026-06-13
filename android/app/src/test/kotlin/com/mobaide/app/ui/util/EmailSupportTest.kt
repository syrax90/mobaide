package com.mobaide.app.ui.util

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for the pure support-email builders. These are plain JVM
 * functions (no Android framework), so no Robolectric is needed.
 *
 * The diagnostics `Account:` line is gated on `!userEmail.isNullOrBlank()`,
 * so the present-iff-non-blank contract is pinned across null, empty, and
 * whitespace-only inputs to keep the body valid for anonymous reporters.
 */
class EmailSupportTest {

    @Test
    fun subject_containsVersionString() {
        val subject = buildSupportEmailSubject("1.4.2")

        assertTrue(
            "subject must carry the version, got: $subject",
            subject.contains("1.4.2"),
        )
    }

    @Test
    fun body_containsVersionDeviceNameAndDeviceId() {
        val body = buildSupportEmailBody(
            versionName = "2.0.0",
            deviceName = "Pixel 8 Pro",
            deviceId = "device-uuid-1234",
            userEmail = null,
        )

        assertTrue("body must contain the version", body.contains("2.0.0"))
        assertTrue("body must contain the device name", body.contains("Pixel 8 Pro"))
        assertTrue("body must contain the device id", body.contains("device-uuid-1234"))
    }

    @Test
    fun body_includesAccountLine_whenUserEmailPresent() {
        val body = buildSupportEmailBody(
            versionName = "1.0.0",
            deviceName = "Pixel 8",
            deviceId = "id-1",
            userEmail = "user@example.com",
        )

        assertTrue(
            "Account line must appear for a signed-in reporter, got: $body",
            body.contains("Account:") && body.contains("user@example.com"),
        )
    }

    @Test
    fun body_omitsAccountLine_whenUserEmailNull() {
        val body = buildSupportEmailBody(
            versionName = "1.0.0",
            deviceName = "Pixel 8",
            deviceId = "id-1",
            userEmail = null,
        )

        assertFalse(
            "Account line must be absent for an anonymous reporter, got: $body",
            body.contains("Account:"),
        )
    }

    @Test
    fun body_omitsAccountLine_whenUserEmailEmpty() {
        val body = buildSupportEmailBody(
            versionName = "1.0.0",
            deviceName = "Pixel 8",
            deviceId = "id-1",
            userEmail = "",
        )

        assertFalse(
            "Account line must be absent for an empty email, got: $body",
            body.contains("Account:"),
        )
    }

    @Test
    fun body_omitsAccountLine_whenUserEmailBlank() {
        val body = buildSupportEmailBody(
            versionName = "1.0.0",
            deviceName = "Pixel 8",
            deviceId = "id-1",
            userEmail = "   ",
        )

        assertFalse(
            "Account line must be absent for a whitespace-only email, got: $body",
            body.contains("Account:"),
        )
    }
}
