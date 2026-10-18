package com.mobaide.app.ui.agents

import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.DeviceIdentityStore
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.DeviceCodeMintResponse
import com.mobaide.app.data.network.dto.DeviceCodeStatusResponse
import com.mobaide.app.data.network.dto.PairingClaimResponse
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.AgentSwitchCoordinator
import com.mobaide.app.data.security.TrustKeyPinning
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for the reversed (device-code) pairing orchestration on
 * [AgentListViewModel].
 *
 * The interesting behaviour is the poll: it is the only place in this
 * ViewModel that drives state from a timer rather than from [AgentEventBus],
 * and it has to survive a dropped request, stop the moment the request
 * settles, and hand off to the shared waiting-for-online tail instead of
 * declaring success on its own.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AgentListViewModelDeviceCodeTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private class Fixture(
        val agentRepository: AgentRepository,
        val connectionStore: ConnectionStore,
        val tokenStore: TokenStore,
        val sessionManager: SessionManager,
        val agentEventBus: AgentEventBus,
        val deviceIdentityStore: DeviceIdentityStore,
        val trustKeyPinning: TrustKeyPinning,
        val agentsFlow: MutableStateFlow<List<AgentSummaryResponse>>,
    )

    private fun newFixture(): Fixture {
        val agentRepository = mockk<AgentRepository>(relaxed = true)
        val connectionStore = mockk<ConnectionStore>(relaxed = true)
        val tokenStore = mockk<TokenStore>(relaxed = true)
        val sessionManager = mockk<SessionManager>(relaxed = true)
        val agentEventBus = mockk<AgentEventBus>(relaxed = true)
        val deviceIdentityStore = mockk<DeviceIdentityStore>(relaxed = true)
        val trustKeyPinning = mockk<TrustKeyPinning>(relaxed = true)

        val agentsFlow = MutableStateFlow<List<AgentSummaryResponse>>(emptyList())
        every { agentRepository.observeAgents() } returns agentsFlow
        every { tokenStore.getAgentId() } returns null
        every { agentEventBus.start() } just Runs
        every { agentEventBus.stop() } just Runs
        coEvery { agentRepository.refreshAgents() } just Runs
        coEvery { agentRepository.mintDeviceCode() } returns MINT
        coEvery { deviceIdentityStore.getOrCreateDeviceId() } returns TEST_DEVICE_ID
        every { deviceIdentityStore.getDeviceName() } returns TEST_DEVICE_NAME

        return Fixture(
            agentRepository, connectionStore, tokenStore,
            sessionManager, agentEventBus, deviceIdentityStore,
            trustKeyPinning, agentsFlow,
        )
    }

    private fun Fixture.buildVm(): AgentListViewModel = AgentListViewModel(
        agentRepository, tokenStore,
        sessionManager, agentEventBus, deviceIdentityStore, trustKeyPinning,
        AgentSwitchCoordinator(
            agentRepository, connectionStore, tokenStore,
            sessionManager, agentEventBus, deviceIdentityStore,
        ),
    ).also {
        // Point the poll's deadline clock at virtual time. `delay` advances
        // only the test scheduler, so a loop comparing the real clock against
        // `expires_at` would never terminate under `advanceUntilIdle` — each
        // iteration schedules another `delay` while the wall clock stands
        // still. Anchoring both to the scheduler makes the deadline reachable.
        it.nowMillis = { mainDispatcherRule.scheduler.currentTime }
    }

    companion object {
        private const val TEST_DEVICE_ID = "dev-uuid-42"
        private const val TEST_DEVICE_NAME = "Test Device"
        private const val REQUEST_ID = "req-1"

        /**
         * Ten minutes past the virtual-clock epoch: far enough that the poll
         * survives every scripted tick, near enough that `advanceUntilIdle`
         * reaches it in a bounded number of iterations instead of spinning.
         */
        private const val POLL_DEADLINE_ISO = "1970-01-01T00:10:00Z"
        private const val POLL_DEADLINE_MS = 600_000L

        private val MINT = DeviceCodeMintResponse(
            request_id = REQUEST_ID,
            code = "WDJB-MJHT",
            expires_at = POLL_DEADLINE_ISO,
            verification_command = "mobaide-agent pair WDJB-MJHT",
        )

        private fun status(
            status: String,
            hostname: String? = "pavel-mbp",
            os: String? = "Darwin",
            isRepair: Boolean = false,
            repairAgentName: String? = null,
            blockedReason: String? = null,
        ) = DeviceCodeStatusResponse(
            status = status,
            expires_at = POLL_DEADLINE_ISO,
            hostname = hostname,
            os = os,
            is_repair = isRepair,
            repair_agent_name = repairAgentName,
            blocked_reason = blockedReason,
        )
    }

    @Test
    fun startDeviceCodePairing_showsTheCommandToRunOnTheAgentHost() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(any()) } returns status("pending")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.startDeviceCodePairing()
        runCurrent()

        val state = vm.uiState.value.deviceCodePairing
        assertTrue(state is DeviceCodePairingState.WaitingForHost)
        state as DeviceCodePairingState.WaitingForHost
        assertEquals("mobaide-agent pair WDJB-MJHT", state.command)
        assertEquals("WDJB-MJHT", state.code)
        assertEquals(REQUEST_ID, state.requestId)
        vm.dismissDeviceCodePairing()
    }

    @Test
    fun startDeviceCodePairing_ignoresASecondTapWhileACodeIsLive() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(any()) } returns status("pending")
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.startDeviceCodePairing()
        runCurrent()
        vm.startDeviceCodePairing()
        runCurrent()

        // Minting supersedes the account's previous code, so a double tap
        // would invalidate the code already on screen.
        coVerify(exactly = 1) { fx.agentRepository.mintDeviceCode() }
        vm.dismissDeviceCodePairing()
    }

    @Test
    fun poll_movesToConfirmingWhenAMachineEntersTheCode() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("redeemed", isRepair = true, repairAgentName = "old-mbp")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()

        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        val state = vm.uiState.value.deviceCodePairing
        assertTrue(state is DeviceCodePairingState.Confirming)
        state as DeviceCodePairingState.Confirming
        assertEquals("pavel-mbp", state.hostname)
        assertEquals("Darwin", state.os)
        assertEquals("old-mbp", state.repairAgentName)
        assertNull(state.blockedReason)
    }

    @Test
    fun poll_namesAnUnnamedMachineRatherThanShowingNothing() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("redeemed", hostname = null, os = null)
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()

        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        val state = vm.uiState.value.deviceCodePairing as DeviceCodePairingState.Confirming
        assertEquals("an unnamed machine", state.hostname)
    }

    @Test
    fun poll_surfacesTheRelaysBlockedVerdictBeforeTheTap() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("redeemed", isRepair = true, blockedReason = "owned_by_other")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()

        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        val state = vm.uiState.value.deviceCodePairing as DeviceCodePairingState.Confirming
        assertEquals(DeviceCodeBlockedReason.OWNED_BY_OTHER, state.blockedReason)
    }

    @Test
    fun poll_treatsAnUnknownBlockedReasonAsNoWarning() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("redeemed", blockedReason = "a_reason_from_a_newer_relay")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()

        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        // Losing the warning beats refusing to render the confirm screen at
        // all; the server still rejects the confirm.
        val state = vm.uiState.value.deviceCodePairing as DeviceCodePairingState.Confirming
        assertNull(state.blockedReason)
    }

    @Test
    fun poll_survivesADroppedRequestAndKeepsWatching() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } throws
            ApiException(503, "gateway blip") andThen status("redeemed")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()

        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()
        // A dropped poll is not a failed pairing — the code is still live
        // server-side and the user can still complete it.
        assertTrue(
            vm.uiState.value.deviceCodePairing is DeviceCodePairingState.WaitingForHost,
        )

        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        assertTrue(
            vm.uiState.value.deviceCodePairing is DeviceCodePairingState.Confirming,
        )
    }

    @Test
    fun poll_reportsACodeThatDiedBeforeAnyMachineUsedIt() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("expired", hostname = null, os = null)
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()

        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        val state = vm.uiState.value.deviceCodePairing
        assertTrue(state is DeviceCodePairingState.Error)
        assertTrue((state as DeviceCodePairingState.Error).message.contains("expired"))
    }

    @Test
    fun poll_keepsPollingThroughConfirmingUntilTheRequestSettles() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("redeemed") andThen status("redeemed") andThen status("expired")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()

        // First "redeemed" poll moves to Confirming...
        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()
        assertTrue(vm.uiState.value.deviceCodePairing is DeviceCodePairingState.Confirming)

        // ...and a second one does not stop the loop: the confirm window is
        // the nonce's, and the user can still be sitting on the screen when
        // it dies.
        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()
        assertTrue(vm.uiState.value.deviceCodePairing is DeviceCodePairingState.Confirming)

        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()
        val state = vm.uiState.value.deviceCodePairing
        assertTrue(state is DeviceCodePairingState.Error)
        assertTrue(
            (state as DeviceCodePairingState.Error).message.contains(
                "before it was confirmed",
            ),
        )

        coVerify(exactly = 3) { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) }
    }

    @Test
    fun confirmDeviceCode_handsOffToTheSharedWaitingForOnlineTail() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("redeemed")
        coEvery {
            fx.agentRepository.confirmDeviceCode(REQUEST_ID, TEST_DEVICE_ID, TEST_DEVICE_NAME, null)
        } returns PairingClaimResponse(
            agent_id = "agent-1",
            agent_friendly_name = "Workstation",
            ws_delivered = true,
        )
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()
        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        vm.confirmDeviceCode()
        runCurrent()

        // A 200 only proves the relay stored credentials; the agent still has
        // to pick them up and connect back, which is the QR flow's problem
        // too — so the two directions share one watcher.
        val qr = vm.uiState.value.qrPairing
        assertTrue(qr is QrPairingState.WaitingForOnline)
        qr as QrPairingState.WaitingForOnline
        assertEquals("agent-1", qr.agentId)
        assertEquals(true, qr.wsDeliveredAtClaim)
        assertEquals(DeviceCodePairingState.Idle, vm.uiState.value.deviceCodePairing)
        vm.dismissQrPairing()
    }

    @Test
    fun confirmDeviceCode_refusesAMachineTheRelayAlreadyRejected() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("redeemed", blockedReason = "owned_by_other")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()
        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        vm.confirmDeviceCode()
        runCurrent()

        // Confirming would spend both the code and the nonce on a call that
        // cannot succeed.
        coVerify(exactly = 0) {
            fx.agentRepository.confirmDeviceCode(any(), any(), any(), any())
        }
        assertTrue(
            vm.uiState.value.deviceCodePairing is DeviceCodePairingState.Confirming,
        )
    }

    @Test
    fun confirmDeviceCode_readsTheAccountPinRefusalOffTheDiscriminator() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("redeemed")
        coEvery {
            fx.agentRepository.confirmDeviceCode(any(), any(), any(), any())
        } throws ApiException(409, """{"detail":{"error":"trust_key_pin_mismatch"}}""")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()
        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        vm.confirmDeviceCode()
        runCurrent()

        // 409 already means "that machine belongs to another account" here,
        // so only the discriminator tells the two refusals apart.
        val state = vm.uiState.value.deviceCodePairing as DeviceCodePairingState.Error
        assertTrue(state.message.contains("show-trust-fp"))
    }

    @Test
    fun confirmDeviceCode_mapsAConsumedRequestToAFreshCodeHint() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("redeemed")
        coEvery {
            fx.agentRepository.confirmDeviceCode(any(), any(), any(), any())
        } throws ApiException(410, """{"detail":"gone"}""")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()
        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        vm.confirmDeviceCode()
        runCurrent()

        val state = vm.uiState.value.deviceCodePairing as DeviceCodePairingState.Error
        assertTrue(state.message.contains("Get a new code"))
    }

    @Test
    fun dismiss_releasesTheAgentWaitingOnTheNonce() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("redeemed")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()
        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        vm.dismissDeviceCodePairing()
        advanceUntilIdle()

        // Cancelling expires the nonce server-side, which is what stops the
        // agent's WebSocket hanging for the rest of the window.
        coVerify(exactly = 1) { fx.agentRepository.cancelDeviceCode(REQUEST_ID) }
        assertEquals(DeviceCodePairingState.Idle, vm.uiState.value.deviceCodePairing)
    }

    @Test
    fun dismiss_stopsThePoll() = runTest {
        val fx = newFixture()
        coEvery { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) } returns
            status("pending")
        val vm = fx.buildVm()
        advanceUntilIdle()
        vm.startDeviceCodePairing()
        runCurrent()
        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS + 1)
        runCurrent()

        vm.dismissDeviceCodePairing()
        advanceTimeBy(DEVICE_CODE_POLL_INTERVAL_MS * 5)
        runCurrent()

        coVerify(exactly = 1) { fx.agentRepository.getDeviceCodeStatus(REQUEST_ID) }
    }
}
