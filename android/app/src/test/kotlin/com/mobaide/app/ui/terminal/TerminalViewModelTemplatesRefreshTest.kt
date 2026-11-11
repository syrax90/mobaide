package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import app.cash.turbine.test
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for [TerminalViewModel.refreshTemplates] — the deterministic
 * fetch fired when the session templates picker opens.
 *
 * The load-bearing contract: the picker must never depend on the best-effort
 * cache warm-ups (WS-connect refresh, a prior visit to Settings → Templates).
 * Opening it (1) triggers exactly one repository fetch, (2) exposes an
 * in-flight flag so the sheet can show a spinner instead of the misleading
 * "no templates yet" empty state, (3) surfaces failures as a user message
 * rather than silently, (4) never clears already-cached templates, and
 * (5) retries on the next open after a failure — the guard never latches.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelTemplatesRefreshTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val eventsFlow = MutableSharedFlow<com.mobaide.app.data.network.WsEvent>(
        replay = 1,
        extraBufferCapacity = 100,
    )
    private val connection = mockk<WebSocketConnection>(relaxed = true) {
        every { events } returns eventsFlow
    }
    private val sessionRepository = mockk<SessionRepository> {
        every { createSessionIoConnection("test-session") } returns connection
        every { observeSessionList() } returns MutableStateFlow(emptyList())
        every { observeSessionsSynced() } returns MutableStateFlow(false)
    }
    private val templatesRepository = mockk<TemplatesRepository>(relaxed = true)
    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
    }

    private fun createViewModel(): TerminalViewModel =
        TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = sessionRepository,
            customAgentTypesRepository = mockk { every { observeTypes() } returns MutableStateFlow(emptyList()) },
            templatesRepository = templatesRepository,
            shortcutsRepository = mockk(),
            foregroundSessionTracker = ForegroundSessionTracker(),
            appForegroundTracker = AppForegroundTracker(),
            preferencesStore = preferencesStore,
            reviewPromptCoordinator = mockk(relaxed = true),
        )

    @Test
    fun refreshTemplates_togglesLoadingFlag() = runTest {
        val gate = CompletableDeferred<Unit>()
        coEvery { templatesRepository.refreshTemplates() } coAnswers { gate.await() }
        val vm = createViewModel()

        vm.refreshTemplates()
        runCurrent()
        assertTrue(
            "loading must be true while the fetch is in flight so the sheet can show a spinner",
            vm.templatesLoading.value,
        )

        gate.complete(Unit)
        advanceUntilIdle()
        assertFalse(
            "loading must clear once the fetch completes",
            vm.templatesLoading.value,
        )
    }

    @Test
    fun refreshTemplates_failure_emitsUserMessage_andClearsLoading() = runTest {
        coEvery { templatesRepository.refreshTemplates() } throws RuntimeException("boom")
        val vm = createViewModel()

        vm.userMessages.test {
            vm.refreshTemplates()
            advanceUntilIdle()
            val message = awaitItem()
            assertTrue(
                "a failed fetch must surface a snackbar message, never fail silently; got: \"$message\"",
                message.startsWith("Failed to load templates") && message.contains("boom"),
            )
            cancelAndIgnoreRemainingEvents()
        }
        assertFalse(
            "loading must clear even when the fetch fails",
            vm.templatesLoading.value,
        )
    }

    @Test
    fun refreshTemplates_whileInFlight_doesNotStartSecondFetch() = runTest {
        val gate = CompletableDeferred<Unit>()
        coEvery { templatesRepository.refreshTemplates() } coAnswers { gate.await() }
        val vm = createViewModel()

        vm.refreshTemplates()
        vm.refreshTemplates()
        gate.complete(Unit)
        advanceUntilIdle()

        coVerify(exactly = 1) { templatesRepository.refreshTemplates() }
    }

    @Test
    fun refreshTemplates_afterFailedFetch_nextCallFetchesAgain() = runTest {
        var first = true
        coEvery { templatesRepository.refreshTemplates() } coAnswers {
            if (first) {
                first = false
                throw RuntimeException("boom")
            }
        }
        val vm = createViewModel()

        vm.refreshTemplates()
        advanceUntilIdle()
        vm.refreshTemplates()
        advanceUntilIdle()

        coVerify(exactly = 2) { templatesRepository.refreshTemplates() }
    }
}
