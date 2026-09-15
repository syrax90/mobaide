package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.network.dto.VersionInfoResponse
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import java.io.IOException

/**
 * [TerminalViewModel.gitCommitAvailable] — whether the `⋯` menu offers
 * Commit…. One `GET /api/version` probe per subscription, read as an explicit
 * `capabilities.git.commit == true`; an older agent or a failed probe hides
 * the entry rather than offering a route that would 404.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelGitCommitAvailableTest {

    private val testDispatcher = StandardTestDispatcher()

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule(testDispatcher)

    private val connection: WebSocketConnection = mockk(relaxed = true) {
        every { events } returns MutableSharedFlow(replay = 1, extraBufferCapacity = 100)
    }

    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private fun version(capabilities: String) = VersionInfoResponse(
        name = "mobaide-agent",
        server_version = "1.4.0",
        protocol_versions = listOf(7),
        min_client_version = 1,
        capabilities = Json.parseToJsonElement(capabilities).jsonObject,
    )

    private fun newViewModel(probe: suspend () -> VersionInfoResponse): TerminalViewModel {
        val repo = mockk<SessionRepository> {
            every { createSessionIoConnection("test-session") } returns connection
            every { observeSessionList() } returns MutableStateFlow(emptyList<SessionResponse>())
            every { observeSessionsSynced() } returns MutableStateFlow(false)
            coEvery { listSessions() } returns emptyList()
            coEvery { getAgentVersion() } coAnswers { probe() }
        }
        val typesRepo = mockk<CustomAgentTypesRepository> {
            every { observeTypes() } returns MutableStateFlow(emptyList<CustomAgentTypeDto>())
        }
        return TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = repo,
            customAgentTypesRepository = typesRepo,
            templatesRepository = mockk(relaxed = true),
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            appForegroundTracker = AppForegroundTracker(),
            preferencesStore = preferencesStore,
        )
    }

    @Test
    fun gitCommitAvailable_isTrue_forAnExplicitCommitCapability() = runTest(testDispatcher) {
        val vm = newViewModel { version("""{"git":{"changes":true,"stage":true,"commit":true}}""") }
        backgroundScope.launch { vm.gitCommitAvailable.collect { } }
        runCurrent()

        assertTrue(vm.gitCommitAvailable.value)
    }

    @Test
    fun gitCommitAvailable_isFalse_forAnAgentThatOnlyServesTheViewer() = runTest(testDispatcher) {
        val vm = newViewModel { version("""{"git":{"changes":true}}""") }
        backgroundScope.launch { vm.gitCommitAvailable.collect { } }
        runCurrent()

        assertFalse(vm.gitCommitAvailable.value)
    }

    @Test
    fun gitCommitAvailable_isFalse_whenTheProbeFails() = runTest(testDispatcher) {
        val vm = newViewModel { throw IOException("down") }
        backgroundScope.launch { vm.gitCommitAvailable.collect { } }
        runCurrent()

        assertFalse(vm.gitCommitAvailable.value)
    }

    @Test
    fun gitCommitAvailable_isFalse_beforeAnyoneSubscribes() = runTest(testDispatcher) {
        val vm = newViewModel { version("""{"git":{"commit":true}}""") }
        runCurrent()

        assertFalse(vm.gitCommitAvailable.value)
    }
}

/** [TerminalViewModel.gitSyncAvailable] — *Fetch / Pull / Push…* on any of the three job keys. */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelGitSyncAvailableTest {

    private val testDispatcher = StandardTestDispatcher()

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule(testDispatcher)

    private val connection: WebSocketConnection = mockk(relaxed = true) {
        every { events } returns MutableSharedFlow(replay = 1, extraBufferCapacity = 100)
    }

    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private fun version(capabilities: String) = VersionInfoResponse(
        name = "mobaide-agent",
        server_version = "1.4.0",
        protocol_versions = listOf(7),
        min_client_version = 1,
        capabilities = Json.parseToJsonElement(capabilities).jsonObject,
    )

    private fun newViewModel(probe: suspend () -> VersionInfoResponse): TerminalViewModel {
        val repo = mockk<SessionRepository> {
            every { createSessionIoConnection("test-session") } returns connection
            every { observeSessionList() } returns MutableStateFlow(emptyList<SessionResponse>())
            every { observeSessionsSynced() } returns MutableStateFlow(false)
            coEvery { listSessions() } returns emptyList()
            coEvery { getAgentVersion() } coAnswers { probe() }
        }
        val typesRepo = mockk<CustomAgentTypesRepository> {
            every { observeTypes() } returns MutableStateFlow(emptyList<CustomAgentTypeDto>())
        }
        return TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = repo,
            customAgentTypesRepository = typesRepo,
            templatesRepository = mockk(relaxed = true),
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            appForegroundTracker = AppForegroundTracker(),
            preferencesStore = preferencesStore,
        )
    }

    @Test
    fun gitSyncAvailable_isTrue_onAnyJobKey_withoutCommit() = runTest(testDispatcher) {
        val vm = newViewModel { version("""{"git":{"changes":true,"push":true}}""") }
        backgroundScope.launch { vm.gitSyncAvailable.collect { } }
        backgroundScope.launch { vm.gitCommitAvailable.collect { } }
        runCurrent()

        assertTrue(vm.gitSyncAvailable.value)
        assertFalse(vm.gitCommitAvailable.value)
    }

    @Test
    fun gitSyncAvailable_isFalse_forAnAgentThatOnlyStagesAndCommits() = runTest(testDispatcher) {
        val vm = newViewModel { version("""{"git":{"changes":true,"stage":true,"discard":true,"commit":true}}""") }
        backgroundScope.launch { vm.gitSyncAvailable.collect { } }
        backgroundScope.launch { vm.gitCommitAvailable.collect { } }
        runCurrent()

        assertFalse(vm.gitSyncAvailable.value)
        assertTrue(vm.gitCommitAvailable.value)
    }

    @Test
    fun oneProbe_feedsBothFlags() = runTest(testDispatcher) {
        var probes = 0
        val vm = newViewModel { probes += 1; version("""{"git":{"commit":true,"fetch":true}}""") }
        backgroundScope.launch { vm.gitSyncAvailable.collect { } }
        backgroundScope.launch { vm.gitCommitAvailable.collect { } }
        runCurrent()

        assertTrue(vm.gitSyncAvailable.value)
        assertTrue(vm.gitCommitAvailable.value)
        assertEquals(1, probes)
    }
}
