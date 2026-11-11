package com.mobaide.app.ui.agents

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Unit tests for [parseMoba1] -- the pure parser for `MOBA1|...` QR
 * payloads. Mirrors the agent-side `parse_qr_payload` strict-rejection
 * rules so a malformed scan never leaks into the claim flow.
 */
class QrPairingPayloadParserTest {

    @Test
    fun parseMoba1_happyPath_returnsParsedPayload() {
        val raw = "MOBA1|https://relay.test|nonce-abc|${"f".repeat(64)}|1700000000"

        val parsed = parseMoba1(raw)!!

        assertEquals("MOBA1", parsed.version)
        assertEquals("https://relay.test", parsed.relayUrl)
        assertEquals("nonce-abc", parsed.nonce)
        assertEquals("f".repeat(64), parsed.trustKeyFp)
        assertEquals(1700000000L, parsed.expUnix)
    }

    @Test
    fun parseMoba1_wrongVersion_returnsNull() {
        val raw = "MOBA0|https://relay.test|n|${"f".repeat(64)}|1"
        assertNull(parseMoba1(raw))
    }

    @Test
    fun parseMoba1_tooFewFields_returnsNull() {
        val raw = "MOBA1|https://relay.test|nonce-abc|fp"
        assertNull(parseMoba1(raw))
    }

    @Test
    fun parseMoba1_tooManyFields_returnsNull() {
        val raw = "MOBA1|https://relay.test|n|${"f".repeat(64)}|1|extra"
        assertNull(parseMoba1(raw))
    }

    @Test
    fun parseMoba1_blankRelayUrl_returnsNull() {
        val raw = "MOBA1||n|${"f".repeat(64)}|1"
        assertNull(parseMoba1(raw))
    }

    @Test
    fun parseMoba1_blankTrustKeyFp_returnsNull() {
        val raw = "MOBA1|https://relay.test|n||1"
        assertNull(parseMoba1(raw))
    }

    @Test
    fun parseMoba1_nonNumericExp_returnsNull() {
        val raw = "MOBA1|https://relay.test|n|${"f".repeat(64)}|notanumber"
        assertNull(parseMoba1(raw))
    }

    @Test
    fun parseMoba1_unrelatedQrText_returnsNull() {
        // Bystander content: a plain URL or marketing QR must not parse.
        assertNull(parseMoba1("https://example.com/promo"))
        assertNull(parseMoba1("WIFI:T:WPA;S:my-net;P:secret;;"))
    }
}
