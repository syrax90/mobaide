package com.claudemobile.app.data.auth

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-JVM unit tests for [GoogleAuthClient] companion helpers.
 *
 * The `signIn()` flow itself depends on `CredentialManager` + Google Play services,
 * which cannot be meaningfully unit-tested in a local JVM suite — that surface is
 * covered by manual / on-device testing.
 */
class GoogleAuthClientTest {

    // ---- isClientIdConfigured ------------------------------------------------

    @Test
    fun isClientIdConfigured_blankString_isFalse() {
        assertFalse(GoogleAuthClient.isClientIdConfigured(""))
    }

    @Test
    fun isClientIdConfigured_whitespaceOnly_isFalse() {
        assertFalse(GoogleAuthClient.isClientIdConfigured("   "))
    }

    @Test
    fun isClientIdConfigured_placeholderValue_isFalse() {
        assertFalse(
            GoogleAuthClient.isClientIdConfigured("YOUR_WEB_CLIENT_ID.apps.googleusercontent.com"),
        )
    }

    @Test
    fun isClientIdConfigured_missingSuffix_isFalse() {
        assertFalse(GoogleAuthClient.isClientIdConfigured("123-abc.example.com"))
    }

    @Test
    fun isClientIdConfigured_validShape_isTrue() {
        assertTrue(
            GoogleAuthClient.isClientIdConfigured(
                "123456789-abcdefghijklmnop.apps.googleusercontent.com",
            ),
        )
    }

    // ---- generateNonce -------------------------------------------------------

    @Test
    fun generateNonce_returnsSha256HexLength() {
        // SHA-256 produces 32 bytes → 64 hex chars
        assertEquals(64, GoogleAuthClient.generateNonce().length)
    }

    @Test
    fun generateNonce_isHexOnly() {
        val hex = Regex("^[0-9a-f]+$")
        repeat(10) {
            assertTrue(hex.matches(GoogleAuthClient.generateNonce()))
        }
    }

    @Test
    fun generateNonce_isUniqueAcrossCalls() {
        // Practically impossible collision at 256 bits; assert diversity over 50 calls.
        val nonces = (1..50).map { GoogleAuthClient.generateNonce() }
        assertEquals(50, nonces.toSet().size)
        // And not all the same
        assertNotEquals(nonces.first(), nonces.last())
    }
}
