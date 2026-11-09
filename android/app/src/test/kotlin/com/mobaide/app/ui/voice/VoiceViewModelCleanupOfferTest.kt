package com.mobaide.app.ui.voice

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.TokenStore
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.VoiceCapability
import com.mobaide.app.data.network.dto.VoiceStatusResponse
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.VoiceRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test

/** The setup sheet offers cleanup in a session whose kind the agent lists in `cleanup_kinds`. */
@OptIn(ExperimentalCoroutinesApi::class)
class VoiceViewModelCleanupOfferTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private fun capability(cleanup: Boolean, kinds: List<String>) = VoiceCapability(
        sampleRate = 16000,
        maxSeconds = 120,
        maxChunkBytes = 65536,
        cleanup = cleanup,
        cleanupKinds = kinds,
    )

    private val allKinds = capability(cleanup = true, kinds = listOf("claude", "codex", "antigravity"))

    private fun viewModel(capability: VoiceCapability): VoiceViewModel {
        val voiceRepository = mockk<VoiceRepository> {
            coEvery { capability() } returns capability
            coEvery { refresh() } returns mockk()
            every { status } returns MutableStateFlow<VoiceStatusResponse?>(null)
        }
        val agentRepository = mockk<AgentRepository> { every { observeAgents() } returns MutableStateFlow(emptyList()) }
        val tokenStore = mockk<TokenStore> {
            every { getAgentId() } returns "agent-1"
            every { getAgentUrl() } returns null
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
            agentRepository = agentRepository,
            tokenStore = tokenStore,
            preferencesStore = preferencesStore,
            sessionManager = sessionManager,
            appForegroundTracker = AppForegroundTracker(),
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "s1")),
        )
    }

    private fun TestScope.offered(capability: VoiceCapability, kind: SessionKind?): Pair<Boolean, Boolean> {
        val vm = viewModel(capability)
        vm.setSessionKind(kind)
        backgroundScope.launch { vm.uiState.collect {} }
        advanceUntilIdle()
        return vm.uiState.value.let { it.cleanupSupported to it.cleanupOffered }
    }

    @Test
    fun everyListedKind_isOffered() = runTest {
        for (kind in listOf(SessionKind.Claude, SessionKind.Codex, SessionKind.Antigravity)) {
            assertEquals(kind.toString(), true to true, offered(allKinds, kind))
        }
    }

    @Test
    fun aKindTheAgentDoesNotList_isNotOffered() = runTest {
        assertEquals(true to false, offered(allKinds, SessionKind.Shell))
        assertEquals(true to false, offered(allKinds, SessionKind.Custom("custom_ab12cd")))
        assertEquals(true to false, offered(capability(cleanup = true, kinds = listOf("claude")), SessionKind.Codex))
    }

    @Test
    fun withoutASession_orWithoutCleanup_nothingIsOffered() = runTest {
        assertEquals(true to false, offered(allKinds, null))
        assertEquals(false to false, offered(capability(cleanup = false, kinds = emptyList()), SessionKind.Claude))
    }
}
