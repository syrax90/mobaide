package com.mobaide.app.data.repository

import com.mobaide.app.BuildConfig
import com.mobaide.app.data.network.dto.VersionInfoResponse
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit-test the pure ``classify`` decision tree (no OkHttp, no Retrofit).
 * The async I/O path lives in [com.mobaide.app.data.network.CentralApi]
 * and is exercised by [com.mobaide.app.data.network.CentralApiTest].
 */
class VersionGateRepositoryTest {

    private fun versionInfo(
        protocols: List<Int>,
        minClientVersion: Int,
        latestAgent: String? = null,
    ) = VersionInfoResponse(
        name = "central_server",
        server_version = "0.1.0",
        protocol_versions = protocols,
        min_client_version = minClientVersion,
        capabilities = JsonObject(emptyMap()),
        latest_agent_version = latestAgent,
    )

    @Test
    fun `compatible when app protocol matches server range`() {
        val outcome = VersionGateRepository.classify(
            versionInfo(protocols = listOf(BuildConfig.APP_PROTOCOL_VERSION), minClientVersion = 1)
        )
        assertTrue(outcome is ServerCompatibility.Compatible)
    }

    @Test
    fun `incompatible when app below minimum`() {
        val outcome = VersionGateRepository.classify(
            versionInfo(
                protocols = listOf(BuildConfig.APP_PROTOCOL_VERSION + 1),
                minClientVersion = BuildConfig.APP_PROTOCOL_VERSION + 1,
            )
        )
        assertTrue(outcome is ServerCompatibility.Incompatible)
        outcome as ServerCompatibility.Incompatible
        assertEquals(BuildConfig.APP_PROTOCOL_VERSION + 1, outcome.minClientVersion)
        assertTrue(outcome.playStoreUrl.startsWith("market://details"))
    }

    @Test
    fun `incompatible when app version not in supported set`() {
        val outcome = VersionGateRepository.classify(
            versionInfo(protocols = listOf(BuildConfig.APP_PROTOCOL_VERSION + 1), minClientVersion = 1)
        )
        assertTrue(outcome is ServerCompatibility.Incompatible)
    }
}
