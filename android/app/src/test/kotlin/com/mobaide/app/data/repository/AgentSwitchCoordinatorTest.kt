package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.DeviceIdentityStore
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.MobileTokenResponse
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import io.mockk.verifyOrder
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * The coordinator's own scope is bound to `Dispatchers.Main.immediate`, which
 * [MainDispatcherRule] points at the test scheduler, so a constructor call
 * inside `runTest` is enough for `switchAndOpenSessions` to be deterministic.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AgentSwitchCoordinatorTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun agent(id: String, name: String = "agent-$id"): AgentSummaryResponse =
        AgentSummaryResponse(agent_id = id, name = name, online = true, created_at = "t")

    private class Fixture(
        val agentRepository: AgentRepository,
        val connectionStore: ConnectionStore,
        val tokenStore: TokenStore,
        val sessionManager: SessionManager,
        val agentEventBus: AgentEventBus,
        val coordinator: AgentSwitchCoordinator,
    )

    private fun newFixture(loggedIn: Boolean = true): Fixture {
        val agentRepository = mockk<AgentRepository>(relaxed = true)
        val connectionStore = mockk<ConnectionStore>(relaxed = true)
        val tokenStore = mockk<TokenStore>(relaxed = true)
        val sessionManager = mockk<SessionManager>(relaxed = true)
        val agentEventBus = mockk<AgentEventBus>(relaxed = true)
        val deviceIdentityStore = mockk<DeviceIdentityStore>(relaxed = true)

        every { sessionManager.isLoggedIn() } returns loggedIn
        every { sessionManager.tryMarkAgentConnected(any()) } answers { firstArg<() -> Unit>().invoke(); true }
        coEvery { deviceIdentityStore.getOrCreateDeviceId() } returns TEST_DEVICE_ID
        every { deviceIdentityStore.getDeviceName() } returns TEST_DEVICE_NAME
        every { connectionStore.listProfiles() } returns emptyList()
        coEvery { agentRepository.getMobileTokensForAgent(any(), TEST_DEVICE_ID, TEST_DEVICE_NAME) } returns TOKENS

        return Fixture(
            agentRepository, connectionStore, tokenStore, sessionManager, agentEventBus,
            AgentSwitchCoordinator(
                agentRepository, connectionStore, tokenStore,
                sessionManager, agentEventBus, deviceIdentityStore,
            ),
        )
    }

    companion object {
        private const val TEST_DEVICE_ID = "dev-uuid-coord"
        private const val TEST_DEVICE_NAME = "Test Device"
        private val TOKENS = MobileTokenResponse("access-tok", "refresh-tok", "bearer")
    }

    @Test
    fun switchTo_tearsDownBeforeMinting_thenSavesCredentialsAndRestartsTheBus() = runTest {
        val fx = newFixture()

        fx.coordinator.switchTo(agent("a-1", name = "TestAgent"))

        verifyOrder {
            fx.agentEventBus.stop()
            fx.sessionManager.agentDisconnect()
            fx.sessionManager.tryMarkAgentConnected(any())
            fx.tokenStore.saveRelayCredentials(
                serverUrl = any(),
                agentId = "a-1",
                pairingId = "a-1",
                deviceId = TEST_DEVICE_ID,
                accessToken = "access-tok",
                refreshToken = "refresh-tok",
            )
            fx.connectionStore.captureActiveProfile(fx.tokenStore, "Agent: TestAgent")
            fx.agentEventBus.start()
        }
        assertNull(fx.coordinator.switchingAgentId.value)
        assertFalse(fx.coordinator.pendingSessionsList.value)
    }

    @Test
    fun switchTo_withNoAgentConnected_skipsTheTeardown() = runTest {
        val fx = newFixture(loggedIn = false)

        fx.coordinator.switchTo(agent("a-1"))

        verify(exactly = 0) { fx.agentEventBus.stop() }
        verify(exactly = 0) { fx.sessionManager.agentDisconnect() }
        verify(exactly = 1) { fx.agentEventBus.start() }
    }

    @Test
    fun switchTo_refreshesAnExistingRelayProfile_insteadOfCapturingANewOne() = runTest {
        val fx = newFixture()
        val existing = ConnectionStore.ConnectionProfile(
            id = "p-1", label = "Agent: old", mode = "relay",
            agentId = "a-1", accessToken = "stale", refreshToken = "stale",
        )
        every { fx.connectionStore.listProfiles() } returns listOf(existing)

        fx.coordinator.switchTo(agent("a-1"))

        verify(exactly = 1) {
            fx.connectionStore.saveProfile(existing.copy(accessToken = "access-tok", refreshToken = "refresh-tok"))
        }
        verify(exactly = 1) { fx.connectionStore.setActiveProfileId("p-1") }
        verify(exactly = 0) { fx.connectionStore.captureActiveProfile(any(), any()) }
    }

    @Test
    fun switchTo_onFailure_rethrows_andClearsTheInFlightMark() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1", any(), any()) } throws
            RuntimeException("token minting failed")

        val outcome = runCatching { fx.coordinator.switchTo(agent("a-1")) }

        assertEquals("token minting failed", outcome.exceptionOrNull()?.message)
        assertNull(fx.coordinator.switchingAgentId.value)
        verify(exactly = 0) { fx.agentEventBus.start() }
    }

    @Test
    fun switchTo_whenSignedOutDuringTheMint_persistsNothing_andThrows() = runTest {
        val fx = newFixture()
        val gate = CompletableDeferred<MobileTokenResponse>()
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1", any(), any()) } coAnswers { gate.await() }

        val outcome = async { runCatching { fx.coordinator.switchTo(agent("a-1")) } }
        runCurrent()
        every { fx.sessionManager.tryMarkAgentConnected(any()) } returns false
        gate.complete(TOKENS)
        advanceUntilIdle()

        val error = outcome.await().exceptionOrNull()
        assertTrue(error is SignedOutException)
        assertEquals("Signed out while connecting", error?.message)
        verify(exactly = 0) {
            fx.tokenStore.saveRelayCredentials(any(), any(), any(), any(), any(), any())
        }
        verify(exactly = 0) { fx.connectionStore.captureActiveProfile(any(), any()) }
        verify(exactly = 0) { fx.agentEventBus.start() }
        assertNull(fx.coordinator.switchingAgentId.value)
    }

    @Test
    fun switchTo_refusesASecondCallWhileOneIsInFlight() = runTest {
        val fx = newFixture()
        val gate = CompletableDeferred<MobileTokenResponse>()
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1", any(), any()) } coAnswers { gate.await() }

        val first = launch { fx.coordinator.switchTo(agent("a-1")) }
        runCurrent()
        assertEquals("a-1", fx.coordinator.switchingAgentId.value)

        val second = runCatching { fx.coordinator.switchTo(agent("a-2")) }

        assertTrue(second.exceptionOrNull() is IllegalStateException)
        assertEquals("the refused call must not clear the live mark", "a-1", fx.coordinator.switchingAgentId.value)
        coVerify(exactly = 0) { fx.agentRepository.getMobileTokensForAgent("a-2", any(), any()) }

        gate.complete(TOKENS)
        first.join()
        assertNull(fx.coordinator.switchingAgentId.value)
    }

    @Test
    fun switchAndOpenSessions_onSuccess_raisesThePendingListUntilConsumed() = runTest {
        val fx = newFixture()

        fx.coordinator.pendingSessionsList.test {
            assertFalse(awaitItem())

            fx.coordinator.switchAndOpenSessions(agent("a-1"))
            advanceUntilIdle()

            assertTrue(awaitItem())
            assertNull(fx.coordinator.switchingAgentId.value)
            verify(exactly = 1) { fx.agentEventBus.start() }

            fx.coordinator.consumePendingSessionsList()
            assertFalse(awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun switchAndOpenSessions_onFailure_dropsThePendingList_andReportsTheMessage() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1", any(), any()) } throws
            RuntimeException("token minting failed")

        fx.coordinator.errors.test {
            fx.coordinator.switchAndOpenSessions(agent("a-1"))
            advanceUntilIdle()

            assertEquals("token minting failed", awaitItem())
            assertFalse("a failed switch must not open the next agent list", fx.coordinator.pendingSessionsList.value)
            assertNull(fx.coordinator.switchingAgentId.value)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun switchAndOpenSessions_whenSignedOutDuringTheMint_dropsThePendingList_andStaysSilent() = runTest {
        val fx = newFixture()
        val gate = CompletableDeferred<MobileTokenResponse>()
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1", any(), any()) } coAnswers { gate.await() }

        fx.coordinator.errors.test {
            fx.coordinator.switchAndOpenSessions(agent("a-1"))
            runCurrent()
            assertTrue(fx.coordinator.pendingSessionsList.value)

            every { fx.sessionManager.tryMarkAgentConnected(any()) } returns false
            gate.complete(TOKENS)
            advanceUntilIdle()

            expectNoEvents()
            assertFalse("a sign-out mid-switch must not open the next agent list", fx.coordinator.pendingSessionsList.value)
            assertNull(fx.coordinator.switchingAgentId.value)
            verify(exactly = 0) {
                fx.tokenStore.saveRelayCredentials(any(), any(), any(), any(), any(), any())
            }
            verify(exactly = 0) { fx.connectionStore.captureActiveProfile(any(), any()) }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun switchAndOpenSessions_onAMessagelessFailure_namesTheAgent() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1", any(), any()) } throws RuntimeException()

        fx.coordinator.errors.test {
            fx.coordinator.switchAndOpenSessions(agent("a-1", name = "Laptop"))
            advanceUntilIdle()

            assertEquals("Failed to connect to Laptop", awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun switchAndOpenSessions_isANoOpWhileAnotherSwitchIsInFlight() = runTest {
        val fx = newFixture()
        val gate = CompletableDeferred<MobileTokenResponse>()
        coEvery { fx.agentRepository.getMobileTokensForAgent("a-1", any(), any()) } coAnswers { gate.await() }

        fx.coordinator.switchAndOpenSessions(agent("a-1"))
        runCurrent()
        assertEquals("a-1", fx.coordinator.switchingAgentId.value)

        fx.coordinator.switchAndOpenSessions(agent("a-2"))
        runCurrent()

        assertEquals("a-1", fx.coordinator.switchingAgentId.value)
        coVerify(exactly = 0) { fx.agentRepository.getMobileTokensForAgent("a-2", any(), any()) }

        gate.complete(TOKENS)
        advanceUntilIdle()
        assertNull(fx.coordinator.switchingAgentId.value)
        assertTrue(fx.coordinator.pendingSessionsList.value)
    }
}
