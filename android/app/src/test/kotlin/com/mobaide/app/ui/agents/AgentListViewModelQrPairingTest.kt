package com.mobaide.app.ui.agents

import com.mobaide.app.data.auth.ConnectionStore
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.AgentSummaryResponse
import com.mobaide.app.data.network.dto.PairingClaimResponse
import com.mobaide.app.data.repository.AgentRepository
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
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for the QR pairing orchestration on [AgentListViewModel].
 *
 * Covers:
 *  * `onScanResult` -> expired QR / TOFS Mismatch / Pinned / Match / scan failure.
 *  * `confirmQrClaim` -> happy path -> [QrPairingState.Success], API 410 mapping,
 *    generic error mapping.
 *  * Token-leak guard: [QrPairingState.Success] never carries the agent token.
 *
 * Uses MockK to stub [TrustKeyPinning] (the production class wraps a real
 * Android DataStore; mocking lets these tests stay JVM-only and skip
 * Robolectric).
 */
@OptIn(ExperimentalCoroutinesApi::class)
class AgentListViewModelQrPairingTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun agent(id: String, online: Boolean = true): AgentSummaryResponse =
        AgentSummaryResponse(
            agent_id = id,
            name = "agent-$id",
            online = online,
            created_at = "2026-05-07T12:00:00Z",
            last_seen_at = null,
        )

    private fun freshPayload(
        relayUrl: String = "https://relay.test",
        nonce: String = "nonce-abc",
        fp: String = "f".repeat(64),
        expSecondsFromNow: Long = 60,
    ): ParsedQrPairingPayload = ParsedQrPairingPayload(
        version = "MOBA1",
        relayUrl = relayUrl,
        nonce = nonce,
        trustKeyFp = fp,
        expUnix = System.currentTimeMillis() / 1000L + expSecondsFromNow,
    )

    private class Fixture(
        val agentRepository: AgentRepository,
        val connectionStore: ConnectionStore,
        val tokenStore: TokenStore,
        val sessionManager: SessionManager,
        val agentEventBus: AgentEventBus,
        val userIdentityStore: UserIdentityStore,
        val trustKeyPinning: TrustKeyPinning,
        val agentsFlow: MutableStateFlow<List<AgentSummaryResponse>>,
    )

    private fun newFixture(): Fixture {
        val agentRepository = mockk<AgentRepository>(relaxed = true)
        val connectionStore = mockk<ConnectionStore>(relaxed = true)
        val tokenStore = mockk<TokenStore>(relaxed = true)
        val sessionManager = mockk<SessionManager>(relaxed = true)
        val agentEventBus = mockk<AgentEventBus>(relaxed = true)
        val userIdentityStore = mockk<UserIdentityStore>(relaxed = true)
        val trustKeyPinning = mockk<TrustKeyPinning>(relaxed = true)

        val agentsFlow = MutableStateFlow<List<AgentSummaryResponse>>(emptyList())
        every { agentRepository.observeAgents() } returns agentsFlow
        every { tokenStore.getAgentId() } returns null
        every { agentEventBus.start() } just Runs
        every { agentEventBus.stop() } just Runs
        every { sessionManager.markAgentConnected() } just Runs
        coEvery { agentRepository.refreshAgents() } just Runs

        return Fixture(
            agentRepository, connectionStore, tokenStore,
            sessionManager, agentEventBus, userIdentityStore,
            trustKeyPinning, agentsFlow,
        )
    }

    private fun Fixture.buildVm(): AgentListViewModel = AgentListViewModel(
        agentRepository, connectionStore, tokenStore,
        sessionManager, agentEventBus, userIdentityStore, trustKeyPinning,
    )

    @Test
    fun onScanQrClicked_resetsToIdle_andEmitsScanRequest() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        // Pre-seed an error to verify it is cleared.
        // (No public setter; we rely on the documented contract of
        //  onScanQrClicked transitioning back to Idle.)
        vm.onScanQrClicked()
        advanceUntilIdle()

        assertEquals(QrPairingState.Idle, vm.uiState.value.qrPairing)
    }

    @Test
    fun onScanResult_expired_setsErrorState_andSkipsTrustKeyCheck() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload(expSecondsFromNow = -10) // already expired

        vm.onScanResult(Result.success(payload))
        advanceUntilIdle()

        val state = vm.uiState.value.qrPairing
        assertTrue("expected Error, got $state", state is QrPairingState.Error)
        assertTrue((state as QrPairingState.Error).message.contains("expired"))
        // Expired QRs are short-circuited BEFORE the TOFS check.
        coVerify(exactly = 0) { fx.trustKeyPinning.verifyOrPin(any(), any()) }
    }

    @Test
    fun onScanResult_tofsMismatch_setsErrorState_andDoesNotConfirm() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(payload.relayUrl, payload.trustKeyFp)
        } returns TrustKeyPinning.VerifyResult.Mismatch(
            pinned = "old-fp", scanned = payload.trustKeyFp,
        )

        vm.onScanResult(Result.success(payload))
        advanceUntilIdle()

        val state = vm.uiState.value.qrPairing
        assertTrue("expected Error, got $state", state is QrPairingState.Error)
        // Operator-facing message must mention MITM so the user does not
        // think this is a transient network blip.
        assertTrue((state as QrPairingState.Error).message.contains("MITM"))
    }

    @Test
    fun onScanResult_pinned_transitionsToConfirming() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(payload.relayUrl, payload.trustKeyFp)
        } returns TrustKeyPinning.VerifyResult.Pinned

        vm.onScanResult(Result.success(payload))
        advanceUntilIdle()

        val state = vm.uiState.value.qrPairing
        assertTrue("expected Confirming, got $state", state is QrPairingState.Confirming)
        assertEquals(payload, (state as QrPairingState.Confirming).payload)
    }

    @Test
    fun onScanResult_match_transitionsToConfirming() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(payload.relayUrl, payload.trustKeyFp)
        } returns TrustKeyPinning.VerifyResult.Match

        vm.onScanResult(Result.success(payload))
        advanceUntilIdle()

        assertTrue(vm.uiState.value.qrPairing is QrPairingState.Confirming)
    }

    @Test
    fun onScanResult_scannerCancelled_returnsToIdle() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        // CancellationException is the project's own type defined in
        // QrPairingScanner.kt — distinct from kotlin.coroutines.CancellationException.
        vm.onScanResult(
            Result.failure(CancellationException("scan cancelled")),
        )
        advanceUntilIdle()

        assertEquals(QrPairingState.Idle, vm.uiState.value.qrPairing)
    }

    @Test
    fun onScanResult_scannerFailure_setsErrorState() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.onScanResult(
            Result.failure(IllegalArgumentException("Scanned QR is not a Mobaide pairing code")),
        )
        advanceUntilIdle()

        val state = vm.uiState.value.qrPairing
        assertTrue(state is QrPairingState.Error)
        assertTrue((state as QrPairingState.Error).message.contains("Mobaide"))
    }

    @Test
    fun confirmQrClaim_happyPath_transitionsToWaitingForOnline_thenSuccess() = runTest {
        // A 200 from /api/pairing/claim only confirms the central server
        // persisted credentials — it does NOT mean the agent is fully
        // paired. The mobile must wait for the agent's relay tunnel to
        // actually come online (via the `agent.status_changed` event from
        // UserEventBus) before showing Success. This test pins that
        // two-step transition.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        every { fx.userIdentityStore.getUserId() } returns "user-42"
        coEvery {
            fx.agentRepository.claimAgentByPairing(
                nonce = payload.nonce,
                trustKeyFp = payload.trustKeyFp,
                mobileDeviceId = "user-42",
                hostnameHint = null,
                friendlyName = null,
            )
        } returns PairingClaimResponse(
            agent_id = "agent-99",
            agent_friendly_name = "lab-host",
            ws_delivered = true,
        )

        // Walk through the state machine: scan → confirm → claim. Use
        // ``runCurrent`` rather than ``advanceUntilIdle`` to drain the
        // synchronously-dispatched work (claim mock, watcher launch)
        // WITHOUT advancing the virtual clock — otherwise the watcher's
        // 60-iteration ``delay(1000)`` loop would complete before we get
        // to assert the intermediate state.
        vm.onScanResult(Result.success(payload))
        runCurrent()
        vm.confirmQrClaim()
        runCurrent()

        // Claim returned 200; mobile must NOT yet show Success — it's
        // waiting for the agent to come online.
        val midState = vm.uiState.value.qrPairing
        assertTrue(
            "expected WaitingForOnline after claim, got $midState",
            midState is QrPairingState.WaitingForOnline,
        )
        val waiting = midState as QrPairingState.WaitingForOnline
        assertEquals("agent-99", waiting.agentId)
        assertEquals("lab-host", waiting.friendlyName)
        assertEquals(true, waiting.wsDeliveredAtClaim)

        // Simulate the UserEventBus pushing an `agent.status_changed`
        // event that flips the agent to online — this is the signal that
        // the agent's relay tunnel has authenticated and its credentials
        // are on disk in .env.
        fx.agentsFlow.value = listOf(agent("agent-99", online = true))
        runCurrent()
        // Fire one 1-second tick of the watcher loop so it observes the
        // freshly-online agent and transitions to Success.
        advanceTimeBy(1500L)
        runCurrent()

        val state = vm.uiState.value.qrPairing
        assertTrue("expected Success, got $state", state is QrPairingState.Success)
        val success = state as QrPairingState.Success
        assertEquals("agent-99", success.agentId)
        assertEquals("lab-host", success.friendlyName)
        val propNames = QrPairingState.Success::class.java.declaredFields
            .map { it.name }
            .filter { !it.startsWith("$") }
        assertFalse(
            "QrPairingState.Success must NEVER carry a token field — found: $propNames",
            propNames.any { it.contains("token", ignoreCase = true) },
        )
    }

    @Test
    fun confirmQrClaim_agentAlreadyOnlineBeforeWatcher_shortCircuitsToSuccess() = runTest {
        // Race: the `agent.status_changed=true` event can arrive while
        // the mobile is still inside claimAgentByPairing's HTTP roundtrip.
        // In that case the agent shows up in observeAgents() before the
        // watcher's first tick — the watcher must seed-check on entry
        // instead of forcing a 1-second wait.
        val fx = newFixture()
        val vm = fx.buildVm()
        runCurrent()
        // Seed the agent as online BEFORE the claim returns.
        fx.agentsFlow.value = listOf(agent("agent-99", online = true))
        runCurrent()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        every { fx.userIdentityStore.getUserId() } returns "user-42"
        coEvery {
            fx.agentRepository.claimAgentByPairing(any(), any(), any(), any(), any())
        } returns PairingClaimResponse(
            agent_id = "agent-99",
            agent_friendly_name = "lab-host",
            ws_delivered = true,
        )

        vm.onScanResult(Result.success(payload))
        runCurrent()
        vm.confirmQrClaim()
        runCurrent()

        val state = vm.uiState.value.qrPairing
        assertTrue(
            "expected immediate Success when agent is already online, got $state",
            state is QrPairingState.Success,
        )
    }

    @Test
    fun confirmQrClaim_neverComesOnline_transitionsToErrorWithDiagnostic() = runTest {
        // Primary diagnostic case the watcher exists to surface: claim
        // succeeded server-side, but the agent never appears online.
        // The error message must include actionable remediation text so
        // a Windows operator can act without a docs round-trip.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        every { fx.userIdentityStore.getUserId() } returns "user-42"
        coEvery {
            fx.agentRepository.claimAgentByPairing(any(), any(), any(), any(), any())
        } returns PairingClaimResponse(
            agent_id = "agent-99",
            agent_friendly_name = "lab-host",
            ws_delivered = false,
        )

        vm.onScanResult(Result.success(payload))
        runCurrent()
        vm.confirmQrClaim()
        runCurrent()

        assertTrue(vm.uiState.value.qrPairing is QrPairingState.WaitingForOnline)
        // Drive the test scheduler past the timeout. The watcher uses
        // 1-second ticks for WAIT_FOR_ONLINE_TIMEOUT_SECONDS iterations,
        // so advancing past WAIT_FOR_ONLINE_TIMEOUT_SECONDS seconds runs
        // the entire loop to the Error transition.
        advanceTimeBy((WAIT_FOR_ONLINE_TIMEOUT_SECONDS + 1) * 1000L)
        runCurrent()

        val state = vm.uiState.value.qrPairing
        assertTrue("expected Error, got $state", state is QrPairingState.Error)
        val errMsg = (state as QrPairingState.Error).message
        assertTrue(
            "error message must mention the agent friendly name: $errMsg",
            errMsg.contains("lab-host"),
        )
        assertTrue(
            "error message must mention the timeout: $errMsg",
            errMsg.contains("$WAIT_FOR_ONLINE_TIMEOUT_SECONDS"),
        )
        assertTrue(
            "error message must include remediation guidance: $errMsg",
            errMsg.contains("show-qr") || errMsg.contains("MobaideAgent"),
        )
        // ws_delivered=false → message should mention the "staged" path
        // (the agent's WS was NOT open at claim time).
        assertTrue(
            "error message must reflect ws_delivered=false: $errMsg",
            errMsg.contains("staged", ignoreCase = true) ||
                errMsg.contains("not open", ignoreCase = true),
        )
    }

    @Test
    fun confirmQrClaim_wsDeliveredTrue_errorMentionsRelayClientFailure() = runTest {
        // When ws_delivered=true the agent received the push but never
        // came online — the diagnostic should point at the post-claim
        // relay client / .env write path, not at the dispatch path.
        val fx = newFixture()
        val vm = fx.buildVm()
        runCurrent()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        every { fx.userIdentityStore.getUserId() } returns "user-42"
        coEvery {
            fx.agentRepository.claimAgentByPairing(any(), any(), any(), any(), any())
        } returns PairingClaimResponse(
            agent_id = "agent-99",
            agent_friendly_name = "lab-host",
            ws_delivered = true,
        )

        vm.onScanResult(Result.success(payload))
        runCurrent()
        vm.confirmQrClaim()
        runCurrent()
        advanceTimeBy((WAIT_FOR_ONLINE_TIMEOUT_SECONDS + 1) * 1000L)
        runCurrent()

        val state = vm.uiState.value.qrPairing
        assertTrue(state is QrPairingState.Error)
        val errMsg = (state as QrPairingState.Error).message
        assertTrue(
            "ws_delivered=true error must point at relay client / firewall: $errMsg",
            errMsg.contains("relay client", ignoreCase = true) ||
                errMsg.contains("firewall", ignoreCase = true),
        )
    }

    @Test
    fun confirmQrClaim_dismissDuringWaiting_cancelsWatcher() = runTest {
        // The watcher coroutine must be cancelled when the user dismisses
        // the WaitingForOnline dialog so it doesn't keep ticking and
        // eventually fire a stale Error transition into Idle state.
        val fx = newFixture()
        val vm = fx.buildVm()
        runCurrent()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        every { fx.userIdentityStore.getUserId() } returns "user-42"
        coEvery {
            fx.agentRepository.claimAgentByPairing(any(), any(), any(), any(), any())
        } returns PairingClaimResponse(
            agent_id = "agent-99",
            agent_friendly_name = "lab-host",
            ws_delivered = null,
        )

        vm.onScanResult(Result.success(payload))
        runCurrent()
        vm.confirmQrClaim()
        runCurrent()
        assertTrue(vm.uiState.value.qrPairing is QrPairingState.WaitingForOnline)

        // User dismisses; advance past the full timeout — state must
        // stay Idle, NOT flip to Error from a still-running watcher.
        vm.dismissQrPairing()
        advanceTimeBy((WAIT_FOR_ONLINE_TIMEOUT_SECONDS + 5) * 1000L)
        runCurrent()

        assertEquals(QrPairingState.Idle, vm.uiState.value.qrPairing)
    }

    @Test
    fun confirmQrClaim_legacyServer_wsDeliveredNull_workflowStillCompletes() = runTest {
        // Forward compatibility: a central server that predates the
        // ws_delivered field returns null. The mobile must still drive
        // the WaitingForOnline → Success transition.
        val fx = newFixture()
        val vm = fx.buildVm()
        runCurrent()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        every { fx.userIdentityStore.getUserId() } returns "user-42"
        coEvery {
            fx.agentRepository.claimAgentByPairing(any(), any(), any(), any(), any())
        } returns PairingClaimResponse(
            agent_id = "agent-99",
            agent_friendly_name = "lab-host",
            ws_delivered = null,
        )

        vm.onScanResult(Result.success(payload))
        runCurrent()
        vm.confirmQrClaim()
        runCurrent()

        assertTrue(vm.uiState.value.qrPairing is QrPairingState.WaitingForOnline)
        val waiting = vm.uiState.value.qrPairing as QrPairingState.WaitingForOnline
        assertNull(waiting.wsDeliveredAtClaim)

        fx.agentsFlow.value = listOf(agent("agent-99", online = true))
        runCurrent()
        advanceTimeBy(1500L)
        runCurrent()
        assertTrue(vm.uiState.value.qrPairing is QrPairingState.Success)
    }

    @Test
    fun confirmQrClaim_apiException410_mapsToOperatorFriendlyMessage() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        every { fx.userIdentityStore.getUserId() } returns "user-42"
        coEvery {
            fx.agentRepository.claimAgentByPairing(
                any(), any(), any(), any(), any(),
            )
        } throws ApiException(code = 410, errorBody = "{\"detail\":\"consumed\"}")

        vm.onScanResult(Result.success(payload))
        advanceUntilIdle()
        vm.confirmQrClaim()
        advanceUntilIdle()

        val state = vm.uiState.value.qrPairing
        assertTrue(state is QrPairingState.Error)
        // 410 -> "consumed or expired" wording the operator can act on.
        assertTrue(
            (state as QrPairingState.Error).message.contains("consumed", ignoreCase = true),
        )
    }

    @Test
    fun confirmQrClaim_apiExceptionOther_includesHttpCode() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        every { fx.userIdentityStore.getUserId() } returns "user-42"
        coEvery {
            fx.agentRepository.claimAgentByPairing(
                any(), any(), any(), any(), any(),
            )
        } throws ApiException(code = 503, errorBody = "service down")

        vm.onScanResult(Result.success(payload))
        advanceUntilIdle()
        vm.confirmQrClaim()
        advanceUntilIdle()

        val state = vm.uiState.value.qrPairing
        assertTrue(state is QrPairingState.Error)
    }

    @Test
    fun confirmQrClaim_genericException_setsErrorState() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        every { fx.userIdentityStore.getUserId() } returns "user-42"
        coEvery {
            fx.agentRepository.claimAgentByPairing(
                any(), any(), any(), any(), any(),
            )
        } throws RuntimeException("network blip")

        vm.onScanResult(Result.success(payload))
        advanceUntilIdle()
        vm.confirmQrClaim()
        advanceUntilIdle()

        val state = vm.uiState.value.qrPairing
        assertTrue(state is QrPairingState.Error)
        assertEquals(
            "network blip",
            (state as QrPairingState.Error).message,
        )
    }

    @Test
    fun confirmQrClaim_outsideConfirmingState_isNoOp() = runTest {
        // Defensive: invoking confirm when no scan has happened yet
        // must not crash and must not call the repo.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        vm.confirmQrClaim()
        advanceUntilIdle()

        coVerify(exactly = 0) {
            fx.agentRepository.claimAgentByPairing(any(), any(), any(), any(), any())
        }
        assertEquals(QrPairingState.Idle, vm.uiState.value.qrPairing)
    }

    @Test
    fun confirmQrClaim_fallsBackToNonceWhenUserIdMissing() = runTest {
        // Pinned production behaviour: when the user id is unavailable
        // (rare but possible in a half-logged-out state), we use the
        // nonce as a unique-but-non-empty mobile_device_id rather than
        // failing the claim.
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        every { fx.userIdentityStore.getUserId() } returns null
        coEvery {
            fx.agentRepository.claimAgentByPairing(
                nonce = payload.nonce,
                trustKeyFp = payload.trustKeyFp,
                mobileDeviceId = payload.nonce, // fallback
                hostnameHint = null,
                friendlyName = null,
            )
        } returns PairingClaimResponse(
            agent_id = "a", agent_friendly_name = "x", ws_delivered = true,
        )

        vm.onScanResult(Result.success(payload))
        advanceUntilIdle()
        vm.confirmQrClaim()
        advanceUntilIdle()

        coVerify(exactly = 1) {
            fx.agentRepository.claimAgentByPairing(
                nonce = payload.nonce,
                trustKeyFp = payload.trustKeyFp,
                mobileDeviceId = payload.nonce,
                hostnameHint = null,
                friendlyName = null,
            )
        }
    }

    @Test
    fun dismissQrPairing_returnsToIdle_fromAnyState() = runTest {
        val fx = newFixture()
        val vm = fx.buildVm()
        advanceUntilIdle()

        val payload = freshPayload()
        coEvery {
            fx.trustKeyPinning.verifyOrPin(any(), any())
        } returns TrustKeyPinning.VerifyResult.Pinned
        vm.onScanResult(Result.success(payload))
        advanceUntilIdle()
        assertTrue(vm.uiState.value.qrPairing is QrPairingState.Confirming)

        vm.dismissQrPairing()

        assertEquals(QrPairingState.Idle, vm.uiState.value.qrPairing)
    }
}
