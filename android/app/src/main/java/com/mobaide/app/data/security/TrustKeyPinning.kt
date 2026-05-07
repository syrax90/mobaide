package com.mobaide.app.data.security

import android.content.Context
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

/**
 * Trust-On-First-Scan (TOFS) trust-key pin store for QR pairing.
 *
 * Each scanned QR carries a `trust_key_fp` — the SHA-256 fingerprint of
 * the central server's manifest signing public key. The first scan
 * against a given relay URL pins that fingerprint locally; subsequent
 * scans against the same relay must match, otherwise we hard-fail with
 * a clear MITM warning. The store is a DataStore Preferences map of
 * `relay_url -> fingerprint`. The fingerprint is not a secret (it is
 * already in the QR payload and exposed by the server's
 * `/api/v1/agent/trust_key` endpoint), so plain DataStore is
 * appropriate — no need for EncryptedSharedPreferences.
 */
private val Context.trustPinDataStore by preferencesDataStore(name = "mobaide_trust_pins")

class TrustKeyPinning(private val context: Context) {

    sealed interface VerifyResult {
        /** No prior pin existed; scanned fingerprint has now been recorded. */
        data object Pinned : VerifyResult
        /** Existing pin matches the scanned fingerprint. */
        data object Match : VerifyResult
        /** Existing pin disagrees with the scanned fingerprint — fail-closed. */
        data class Mismatch(val pinned: String, val scanned: String) : VerifyResult
    }

    suspend fun getPinned(relayUrl: String): String? {
        val key = stringPreferencesKey(normalize(relayUrl))
        return context.trustPinDataStore.data.map { it[key] }.first()
    }

    suspend fun setPinned(relayUrl: String, fingerprint: String) {
        val key = stringPreferencesKey(normalize(relayUrl))
        context.trustPinDataStore.edit { it[key] = fingerprint }
    }

    /**
     * First-scan: pin and return [VerifyResult.Pinned].
     * Repeat-scan: compare and return [VerifyResult.Match] or
     * [VerifyResult.Mismatch].
     *
     * Callers MUST treat [VerifyResult.Mismatch] as a hard refusal — do
     * NOT prompt the user for confirmation. The legitimate "I rotated
     * my relay's trust key" recovery is a settings-screen action that
     * clears the pin.
     */
    suspend fun verifyOrPin(relayUrl: String, fingerprint: String): VerifyResult {
        val existing = getPinned(relayUrl)
        return when {
            existing == null -> {
                setPinned(relayUrl, fingerprint)
                VerifyResult.Pinned
            }
            existing == fingerprint -> VerifyResult.Match
            else -> VerifyResult.Mismatch(pinned = existing, scanned = fingerprint)
        }
    }

    private fun normalize(relayUrl: String): String {
        val trimmed = relayUrl.trim().trimEnd('/')
        return trimmed.lowercase()
    }
}
