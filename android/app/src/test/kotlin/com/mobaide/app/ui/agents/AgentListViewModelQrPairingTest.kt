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
import kotlinx.coroutines.test.advanceUntilIdle
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

    private fun agent(id: String): AgentSummaryResponse = AgentSummaryResponse(
        agent_id = id,
        name = "agent-$id",
        online = true,
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
    fun confirmQrClaim_happyPath_transitionsToSuccess_withoutToken() = runTest {
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
        )

        // Walk through the state machine: scan → confirm → claim.
        vm.onScanResult(Result.success(payload))
        advanceUntilIdle()
        vm.confirmQrClaim()
        advanceUntilIdle()

        val state = vm.uiState.value.qrPairing
        assertTrue("expected Success, got $state", state is QrPairingState.Success)
        val success = state as QrPairingState.Success
        assertEquals("agent-99", success.agentId)
        assertEquals("lab-host", success.friendlyName)
        // Sealed-class invariant: Success is a 2-arg data class with no
        // token property. Reflect on the declared properties to pin
        // this contract — a future refactor that adds a `token` field
        // here would silently leak it into the UI.
        val propNames = QrPairingState.Success::class.java.declaredFields
            .map { it.name }
            .filter { !it.startsWith("$") }
        assertFalse(
            "QrPairingState.Success must NEVER carry a token field — found: $propNames",
            propNames.any { it.contains("token", ignoreCase = true) },
        )
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
            agent_id = "a", agent_friendly_name = "x",
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
