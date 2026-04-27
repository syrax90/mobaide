package com.mobaide.app.ui.pairing

import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.PairingCompleteResponse
import com.mobaide.app.data.repository.PairingRepository
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.just
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [PairingViewModel] — covers QR code detection, manual
 * pairing, error handling, and the double-scan guard. The
 * [PairingRepository] is mocked via MockK.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class PairingViewModelTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    // ---- Helpers ------------------------------------------------------------

    private val successResponse = PairingCompleteResponse(
        access_token = "at-1",
        refresh_token = "rt-1",
        agent_id = "agent-abc",
        pairing_id = "pair-123",
    )

    private fun stubRepo(
        completePairing: suspend () -> PairingCompleteResponse = { successResponse },
    ): PairingRepository {
        val repo = mockk<PairingRepository>()
        coEvery {
            repo.completePairing(any(), any(), any(), any())
        } coAnswers { completePairing() }
        coEvery {
            repo.savePairingCredentials(any(), any(), any())
        } just Runs
        return repo
    }

    // ---- QR detection: happy path -------------------------------------------

    @Test
    fun onQrDetected_validClaudePairJson_triggersPairingAndCompletesSuccessfully() = runTest {
        val repo = stubRepo()
        val vm = PairingViewModel(repo)

        val qrJson = """{"type":"claude-pair","token":"tok-1","server":"https://relay.example.com","agent_id":"agent-abc"}"""
        vm.onQrDetected(qrJson)
        advanceUntilIdle()

        val state = vm.uiState.value
        assertTrue(state.pairingComplete)
        assertFalse(state.isProcessing)
        assertNull(state.error)

        coVerify(exactly = 1) {
            repo.completePairing("https://relay.example.com", "tok-1", any(), any())
        }
        coVerify(exactly = 1) {
            repo.savePairingCredentials("https://relay.example.com", successResponse, any())
        }
    }

    // ---- QR detection: invalid inputs ---------------------------------------

    @Test
    fun onQrDetected_invalidJson_isIgnored_andKeepsScanning() = runTest {
        val repo = stubRepo()
        val vm = PairingViewModel(repo)

        vm.onQrDetected("not-json-at-all")
        advanceUntilIdle()

        val state = vm.uiState.value
        assertFalse(state.pairingComplete)
        assertFalse(state.isProcessing)
        assertNull(state.error)
        coVerify(exactly = 0) { repo.completePairing(any(), any(), any(), any()) }
    }

    @Test
    fun onQrDetected_wrongType_isIgnored() = runTest {
        val repo = stubRepo()
        val vm = PairingViewModel(repo)

        val qrJson = """{"type":"wifi-config","token":"t","server":"s","agent_id":"a"}"""
        vm.onQrDetected(qrJson)
        advanceUntilIdle()

        assertFalse(vm.uiState.value.pairingComplete)
        coVerify(exactly = 0) { repo.completePairing(any(), any(), any(), any()) }
    }

    // ---- Double-scan guard --------------------------------------------------

    @Test
    fun onQrDetected_afterSuccessfulPairing_ignoresSubsequentScans() = runTest {
        val repo = stubRepo()
        val vm = PairingViewModel(repo)

        val qrJson = """{"type":"claude-pair","token":"tok-1","server":"https://relay.example.com","agent_id":"agent-abc"}"""
        vm.onQrDetected(qrJson)
        advanceUntilIdle()
        assertTrue(vm.uiState.value.pairingComplete)

        // Second scan should be ignored
        vm.onQrDetected(qrJson)
        advanceUntilIdle()

        coVerify(exactly = 1) { repo.completePairing(any(), any(), any(), any()) }
    }

    // ---- Error handling: ApiException ---------------------------------------

    @Test
    fun onQrDetected_apiException400_showsInvalidPairingCodeError() = runTest {
        val repo = stubRepo(completePairing = { throw ApiException(400, "Token expired") })
        val vm = PairingViewModel(repo)

        val qrJson = """{"type":"claude-pair","token":"tok-1","server":"https://relay.example.com","agent_id":"agent-abc"}"""
        vm.onQrDetected(qrJson)
        advanceUntilIdle()

        val state = vm.uiState.value
        assertFalse(state.pairingComplete)
        assertFalse(state.isProcessing)
        assertEquals("Invalid or expired pairing code", state.error)
    }

    @Test
    fun onQrDetected_apiExceptionOther_showsRawErrorBody() = runTest {
        val repo = stubRepo(completePairing = { throw ApiException(500, "Server error") })
        val vm = PairingViewModel(repo)

        val qrJson = """{"type":"claude-pair","token":"tok-1","server":"https://relay.example.com","agent_id":"agent-abc"}"""
        vm.onQrDetected(qrJson)
        advanceUntilIdle()

        val state = vm.uiState.value
        assertTrue(state.error!!.contains("Server error"))
    }

    @Test
    fun onQrDetected_genericException_showsConnectionFailedError() = runTest {
        val repo = stubRepo(completePairing = { throw RuntimeException("DNS fail") })
        val vm = PairingViewModel(repo)

        val qrJson = """{"type":"claude-pair","token":"tok-1","server":"https://relay.example.com","agent_id":"agent-abc"}"""
        vm.onQrDetected(qrJson)
        advanceUntilIdle()

        val state = vm.uiState.value
        assertTrue(state.error!!.startsWith("Connection failed:"))
    }

    @Test
    fun onQrDetected_afterError_canRetry() = runTest {
        var callCount = 0
        val repo = stubRepo(completePairing = {
            callCount++
            if (callCount == 1) throw RuntimeException("Network error")
            successResponse
        })
        val vm = PairingViewModel(repo)

        val qrJson = """{"type":"claude-pair","token":"tok-1","server":"https://relay.example.com","agent_id":"agent-abc"}"""

        // First attempt fails
        vm.onQrDetected(qrJson)
        advanceUntilIdle()
        assertFalse(vm.uiState.value.pairingComplete)

        // hasProcessed is reset on error, so retry should work
        vm.onQrDetected(qrJson)
        advanceUntilIdle()
        assertTrue(vm.uiState.value.pairingComplete)
    }

    // ---- Manual pairing -----------------------------------------------------

    @Test
    fun pairManually_happyPath_completesSuccessfully() = runTest {
        val repo = stubRepo()
        val vm = PairingViewModel(repo)

        vm.pairManually("https://relay.example.com", "tok-1", "agent-abc")
        advanceUntilIdle()

        assertTrue(vm.uiState.value.pairingComplete)
        coVerify(exactly = 1) {
            repo.completePairing("https://relay.example.com", "tok-1", any(), any())
        }
    }

    @Test
    fun pairManually_initialState_isNotProcessing() = runTest {
        val repo = stubRepo()
        val vm = PairingViewModel(repo)

        assertFalse(vm.uiState.value.isProcessing)
        assertFalse(vm.uiState.value.pairingComplete)
        assertNull(vm.uiState.value.error)
    }

    // ---- clearError ---------------------------------------------------------

    @Test
    fun clearError_setsErrorToNull() = runTest {
        val repo = stubRepo(completePairing = { throw RuntimeException("fail") })
        val vm = PairingViewModel(repo)

        val qrJson = """{"type":"claude-pair","token":"tok-1","server":"https://relay.example.com","agent_id":"agent-abc"}"""
        vm.onQrDetected(qrJson)
        advanceUntilIdle()
        assertTrue(vm.uiState.value.error != null)

        vm.clearError()

        assertNull(vm.uiState.value.error)
    }
}
