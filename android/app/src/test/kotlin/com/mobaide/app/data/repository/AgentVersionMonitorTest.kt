package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.VersionInfoResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [AgentVersionMonitor] -- covers the pure decision tree
 * driven by [AgentVersionMonitor.probe]:
 *
 *  - mismatched current/latest -> emits [AgentVersionMonitor.Outdated]
 *  - matching versions or null/blank latest -> clears the flow
 *  - missing agent base URL -> probe is skipped, no API call
 *  - transient API failure -> previous state is preserved (no flap)
 *  - explicit [AgentVersionMonitor.reset] clears the flow
 */
class AgentVersionMonitorTest {

    private fun versionInfo(
        serverVersion: String,
        latestAgent: String?,
    ) = VersionInfoResponse(
        name = "mobaide_agent",
        server_version = serverVersion,
        protocol_versions = listOf(1),
        min_client_version = 1,
        capabilities = JsonObject(emptyMap()),
        latest_agent_version = latestAgent,
        manifest_url = null,
        manifest_signature = null,
    )

    private fun newMonitor(
        baseUrl: String? = "https://agent.example.com",
    ): Triple<AgentVersionMonitor, RestApi, TokenStore> {
        val restApi = mockk<RestApi>()
        val tokenStore = mockk<TokenStore>()
        every { tokenStore.getEffectiveBaseUrl() } returns baseUrl
        return Triple(AgentVersionMonitor(restApi, tokenStore), restApi, tokenStore)
    }

    @Test
    fun probe_mismatchedVersions_emitsOutdated_andReturnsTrue() = runTest {
        val (monitor, restApi, _) = newMonitor()
        coEvery { restApi.getAgentVersion() } returns
            versionInfo(serverVersion = "0.1.0", latestAgent = "0.2.0")

        monitor.outdated.test {
            assertNull(awaitItem())

            val applied = monitor.probe()
            assertTrue("probe must signal that a fresh response was applied", applied)

            val event = awaitItem()
            assertEquals(
                AgentVersionMonitor.Outdated(currentVersion = "0.1.0", latestVersion = "0.2.0"),
                event,
            )
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { restApi.getAgentVersion() }
    }

    @Test
    fun probe_matchingVersions_keepsOutdatedNull_andReturnsTrue() = runTest {
        val (monitor, restApi, _) = newMonitor()
        coEvery { restApi.getAgentVersion() } returns
            versionInfo(serverVersion = "0.2.0", latestAgent = "0.2.0")

        val applied = monitor.probe()

        assertTrue(applied)
        assertNull(monitor.outdated.value)
    }

    @Test
    fun probe_nullLatestAgentVersion_keepsOutdatedNull() = runTest {
        val (monitor, restApi, _) = newMonitor()
        coEvery { restApi.getAgentVersion() } returns
            versionInfo(serverVersion = "0.1.0", latestAgent = null)

        val applied = monitor.probe()

        assertTrue(applied)
        assertNull(monitor.outdated.value)
    }

    @Test
    fun probe_blankLatestAgentVersion_keepsOutdatedNull() = runTest {
        val (monitor, restApi, _) = newMonitor()
        coEvery { restApi.getAgentVersion() } returns
            versionInfo(serverVersion = "0.1.0", latestAgent = "")

        val applied = monitor.probe()

        assertTrue(applied)
        assertNull(monitor.outdated.value)
    }

    @Test
    fun probe_clearsPreviouslySetOutdated_whenServerNowReportsMatch() = runTest {
        val (monitor, restApi, _) = newMonitor()
        coEvery { restApi.getAgentVersion() } returnsMany listOf(
            versionInfo(serverVersion = "0.1.0", latestAgent = "0.2.0"),
            versionInfo(serverVersion = "0.2.0", latestAgent = "0.2.0"),
        )

        assertTrue(monitor.probe())
        assertEquals(
            AgentVersionMonitor.Outdated(currentVersion = "0.1.0", latestVersion = "0.2.0"),
            monitor.outdated.value,
        )

        assertTrue(monitor.probe())
        assertNull(monitor.outdated.value)
    }

    @Test
    fun probe_noBaseUrl_returnsFalse_andSkipsApiCall() = runTest {
        val (monitor, restApi, _) = newMonitor(baseUrl = null)

        val applied = monitor.probe()

        assertFalse("probe must report skipped when no base URL is available", applied)
        assertNull(monitor.outdated.value)
        coVerify(exactly = 0) { restApi.getAgentVersion() }
    }

    @Test
    fun probe_apiThrows_returnsFalse_andPreservesPreviousState() = runTest {
        val (monitor, restApi, _) = newMonitor()
        coEvery { restApi.getAgentVersion() } returnsMany listOf(
            versionInfo(serverVersion = "0.1.0", latestAgent = "0.2.0"),
        )
        assertTrue(monitor.probe())
        val firstSnapshot = monitor.outdated.value
        assertEquals(
            AgentVersionMonitor.Outdated(currentVersion = "0.1.0", latestVersion = "0.2.0"),
            firstSnapshot,
        )

        coEvery { restApi.getAgentVersion() } throws RuntimeException("offline")
        val applied = monitor.probe()

        assertFalse(applied)
        assertEquals(
            "transient failure must not blink the banner away",
            firstSnapshot,
            monitor.outdated.value,
        )
    }

    @Test
    fun reset_clearsOutdated() = runTest {
        val (monitor, restApi, _) = newMonitor()
        coEvery { restApi.getAgentVersion() } returns
            versionInfo(serverVersion = "0.1.0", latestAgent = "0.2.0")
        monitor.probe()
        assertEquals(
            AgentVersionMonitor.Outdated(currentVersion = "0.1.0", latestVersion = "0.2.0"),
            monitor.outdated.value,
        )

        monitor.reset()

        assertNull(monitor.outdated.value)
    }
}
