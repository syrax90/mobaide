package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.PairingClaimRequest
import com.mobaide.app.data.network.dto.PairingClaimResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.coVerifyOrder
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.fail
import org.junit.Test

/**
 * Unit tests for [AgentRepository.claimAgentByPairing].
 *
 * Pinned behaviours:
 *
 *  * Forwards to [CentralApi.claimAgentByPairing] with the user's bearer
 *    token and the parsed-QR-derived request fields.
 *  * Triggers a `refreshAgents` after a successful claim so the new agent
 *    appears in the cached list within one round-trip.
 *  * Surfaces [IllegalStateException] when the user is not logged in,
 *    mirroring every other token-gated repo method.
 */
class AgentRepositoryClaimTest {

    private fun agent(id: String): AgentSummaryResponse = AgentSummaryResponse(
        agent_id = id,
        name = "agent-$id",
        online = true,
        created_at = "2026-05-07T12:00:00Z",
        last_seen_at = null,
    )

    private data class Fixture(
        val repo: AgentRepository,
        val centralApi: CentralApi,
        val userIdentityStore: UserIdentityStore,
    )

    private fun newFixture(userToken: String? = "tok-user-123"): Fixture {
        val centralApi = mockk<CentralApi>(relaxed = true)
        val userIdentityStore = mockk<UserIdentityStore>()
        every { userIdentityStore.getUserAccessToken() } returns userToken
        return Fixture(
            repo = AgentRepository(centralApi, userIdentityStore),
            centralApi = centralApi,
            userIdentityStore = userIdentityStore,
        )
    }

    @Test
    fun claimAgentByPairing_throwsWhenUserNotLoggedIn() = runTest {
        val (repo) = newFixture(userToken = null)

        try {
            repo.claimAgentByPairing(
                nonce = "n", trustKeyFp = "fp", mobileDeviceId = "dev",
                hostnameHint = null, friendlyName = null,
            )
            fail("expected IllegalStateException")
        } catch (e: IllegalStateException) {
            assertEquals("User not logged in", e.message)
        }
    }

    @Test
    fun claimAgentByPairing_delegatesToApi_andReturnsSamePayload() = runTest {
        val (repo, api) = newFixture()
        val response = PairingClaimResponse(
            agent_id = "a-new", agent_friendly_name = "lab-host",
        )
        val captured = slot<PairingClaimRequest>()
        coEvery { api.claimAgentByPairing("tok-user-123", capture(captured)) } returns response
        coEvery { api.listAgents("tok-user-123") } returns listOf(agent("a-new"))

        val out = repo.claimAgentByPairing(
            nonce = "n1",
            trustKeyFp = "fp1",
            mobileDeviceId = "dev-1",
            hostnameHint = "host-hint",
            friendlyName = "lab-host",
        )

        assertSame(response, out)
        assertEquals("n1", captured.captured.nonce)
        assertEquals("fp1", captured.captured.trust_key_fp)
        assertEquals("dev-1", captured.captured.mobile_device_id)
        assertEquals("host-hint", captured.captured.hostname_hint)
        assertEquals("lab-host", captured.captured.friendly_name)
    }

    @Test
    fun claimAgentByPairing_triggersRefreshAgents_onSuccess() = runTest {
        // After a successful claim, the repo MUST refresh the agent
        // list so the new agent appears in `observeAgents` without a
        // manual pull-to-refresh on the screen.
        val (repo, api) = newFixture()
        coEvery {
            api.claimAgentByPairing(any(), any())
        } returns PairingClaimResponse(
            agent_id = "a-new", agent_friendly_name = "lab",
        )
        coEvery { api.listAgents("tok-user-123") } returns listOf(agent("a-new"))

        repo.claimAgentByPairing(
            nonce = "n", trustKeyFp = "fp", mobileDeviceId = "dev",
            hostnameHint = null, friendlyName = null,
        )

        coVerifyOrder {
            api.claimAgentByPairing("tok-user-123", any())
            api.listAgents("tok-user-123")
        }
        assertEquals(listOf("a-new"), repo.observeAgents().value.map { it.agent_id })
    }

    @Test
    fun claimAgentByPairing_passesNullableFields_unchanged() = runTest {
        val (repo, api) = newFixture()
        val captured = slot<PairingClaimRequest>()
        coEvery { api.claimAgentByPairing(any(), capture(captured)) } returns
            PairingClaimResponse(agent_id = "a", agent_friendly_name = "x")
        coEvery { api.listAgents(any()) } returns emptyList()

        repo.claimAgentByPairing(
            nonce = "n", trustKeyFp = "fp", mobileDeviceId = "dev",
            hostnameHint = null, friendlyName = null,
        )

        assertEquals(null, captured.captured.hostname_hint)
        assertEquals(null, captured.captured.friendly_name)
    }

    @Test
    fun claimAgentByPairing_rethrowsApiException_withoutRefresh() = runTest {
        // Failure modes (replay 410, 4xx) must NOT trigger a refresh —
        // the cache is unchanged so the agent list does not flicker.
        val (repo, api) = newFixture()
        coEvery { api.claimAgentByPairing(any(), any()) } throws RuntimeException("410")
        coEvery { api.listAgents(any()) } returns listOf(agent("untouched"))

        // Pre-warm the cache so we can observe non-mutation.
        repo.refreshAgents()
        assertEquals(1, repo.observeAgents().value.size)

        try {
            repo.claimAgentByPairing(
                nonce = "n", trustKeyFp = "fp", mobileDeviceId = "dev",
                hostnameHint = null, friendlyName = null,
            )
            fail("expected exception")
        } catch (e: RuntimeException) {
            assertEquals("410", e.message)
        }

        // Exactly one listAgents call (the pre-warm) — no second refresh.
        coVerify(exactly = 1) { api.listAgents("tok-user-123") }
    }
}
