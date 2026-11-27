package com.mobaide.app.ui.voice

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.dto.CloudVoiceStatus
import com.mobaide.app.data.network.dto.VoiceCapability
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.CloudVoiceRepository
import com.mobaide.app.data.repository.VoiceRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.IOException

/**
 * An agent that did not answer about its own voice is asked again by itself,
 * so "Couldn't reach …" clears without reopening the screen.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class VoiceViewModelUnreachableRetryTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val voice = VoiceCapability(
        sampleRate = 16000,
        maxSeconds = 120,
        maxChunkBytes = 65536,
        cleanup = false,
        cleanupKinds = emptyList(),
    )

    /** The agent's first [probeFailures] capability probes and [statusFailures] status reads get no answer. */
    private class Agent(val probeFailures: Int = 0, val statusFailures: Int = 0) {
        var probes = 0
        var statusReads = 0
    }

    private fun viewModel(agent: Agent): VoiceViewModel {
        val voiceRepository = mockk<VoiceRepository> {
            coEvery { capability() } coAnswers {
                agent.probes += 1
                if (agent.probes <= agent.probeFailures) throw IOException("offline")
                voice
            }
            coEvery { refresh() } coAnswers {
                agent.statusReads += 1
                if (agent.statusReads <= agent.statusFailures) throw IOException("offline")
                mockk<VoiceStatusResponse>()
            }
            every { status } returns MutableStateFlow<VoiceStatusResponse?>(null)
        }
        val cloudVoiceRepository = mockk<CloudVoiceRepository> {
            every { status } returns MutableStateFlow<CloudVoiceStatus?>(null)
            every { checking } returns flowOf(false)
            coEvery { refresh(any()) } just Runs
        }
        val agentRepository = mockk<AgentRepository> { every { observeAgents() } returns MutableStateFlow(emptyList()) }
        val tokenStore = mockk<TokenStore> {
            every { getAgentId() } returns "agent-1"
        }
        val preferencesStore = mockk<PreferencesStore> {
            every { voiceMicHidden(any()) } returns flowOf(false)
            every { voiceCleanupNoticeSeen } returns flowOf(true)
        }
        val sessionManager = mockk<SessionManager> {
            every { authState } returns MutableStateFlow(SessionManager.AuthState.FullyConnected)
        }
        return VoiceViewModel(
            context = mockk<Context>(relaxed = true),
            voiceRepository = voiceRepository,
            cloudVoiceRepository = cloudVoiceRepository,
            agentRepository = agentRepository,
            tokenStore = tokenStore,
            preferencesStore = preferencesStore,
            sessionManager = sessionManager,
            appForegroundTracker = AppForegroundTracker(),
            savedStateHandle = SavedStateHandle(),
        )
    }

    @Test
    fun aProbeTheAgentDidNotAnswer_isRepeatedWhileAVoiceSurfaceShows_untilItAnswers() = runTest {
        val agent = Agent(probeFailures = 2)
        val vm = viewModel(agent)
        runCurrent()

        // No voice surface shows the state yet: nobody waits for the answer.
        advanceTimeBy(LONG_MS)
        runCurrent()
        assertEquals(1, agent.probes)

        backgroundScope.launch { vm.uiState.collect {} }
        runCurrent()
        assertTrue(vm.uiState.value.capabilityLoadFailed)

        advanceTimeBy(RETRY_MS)
        runCurrent()
        assertEquals(2, agent.probes)
        assertTrue(vm.uiState.value.capabilityLoadFailed)

        advanceTimeBy(RETRY_MS)
        runCurrent()
        assertFalse(vm.uiState.value.capabilityLoadFailed)
        assertEquals(voice, vm.uiState.value.capability)

        // Answered: the agent is left alone.
        advanceTimeBy(LONG_MS)
        runCurrent()
        assertEquals(3, agent.probes)
    }

    @Test
    fun aStatusReadTheAgentDidNotAnswer_isRepeatedUntilItAnswers() = runTest {
        val agent = Agent(statusFailures = 1)
        val vm = viewModel(agent)
        backgroundScope.launch { vm.uiState.collect {} }
        runCurrent()
        assertFalse(vm.uiState.value.capabilityLoadFailed)
        assertTrue(vm.uiState.value.statusLoadFailed)

        advanceTimeBy(RETRY_MS)
        runCurrent()
        assertFalse(vm.uiState.value.statusLoadFailed)

        advanceTimeBy(LONG_MS)
        runCurrent()
        assertEquals(2, agent.statusReads)
    }

    private companion object {
        /** The view model's retry interval while the agent is unreachable. */
        const val RETRY_MS = 5_000L
        const val LONG_MS = 60_000L
    }
}
