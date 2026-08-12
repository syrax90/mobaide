package com.mobaide.app.ui.terminal

import app.cash.turbine.test
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.Runs
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Guards the "opened into a session that is gone" bounce in
 * [TerminalViewModel] — a terminal can be entered for an id that no longer
 * exists (stale auto-resume id, restored back stack, tapped notification for
 * a session that has since ended), and before this it just sat there
 * connecting while the agent dropped every frame it sent.
 *
 * Two conditions gate the bounce, and both matter:
 *
 * - the snapshot must be agent-authoritative (`observeSessionsSynced`), since
 *   a list left over from a dropped tunnel still names sessions the agent lost
 *   when it restarted;
 * - the session must never have been observed alive on this screen, so a
 *   session that ends while the user is reading it leaves the output on
 *   screen instead of ejecting them.
 *
 * Robolectric so `parseStatus` resolves `org.json.JSONObject` for real (the
 * stub `android.jar` returns defaults under the project's
 * `unitTests.isReturnDefaultValues = true`); SDK 33 matches the rest of the
 * JVM/Robolectric suite.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewModelSessionGoneTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val eventsFlow = MutableSharedFlow<WsEvent>(replay = 1, extraBufferCapacity = 100)
    private val connection = mockk<WebSocketConnection>(relaxed = true) {
        every { events } returns eventsFlow
    }

    private val sessionsFlow = MutableStateFlow<List<SessionResponse>>(emptyList())
    private val syncedFlow = MutableStateFlow(false)

    private val sessionRepository = mockk<SessionRepository> {
        every { createSessionIoConnection("test-session") } returns connection
        every { observeSessionList() } returns sessionsFlow
        every { observeSessionsSynced() } returns syncedFlow
        every { notifySessionEnded(any()) } just Runs
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
        )

    private fun session(id: String, state: String = "running"): SessionResponse =
        SessionResponse(session_id = id, state = state, created_at = "2026-04-11T00:00:00Z")

    /** The `'s'` frame the agent answers an attach with. */
    private suspend fun emitState(status: String) {
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_STATE)
                    .plus("""{"status":"$status","pid":1234}""".toByteArray(Charsets.UTF_8))
                    .toByteString(),
            ),
        )
    }

    @Test
    fun sessionEnded_emits_whenTheSyncedSnapshotDoesNotListTheSession() = runTest {
        val vm = createViewModel()

        vm.sessionEnded.test {
            sessionsFlow.value = listOf(session("someone-else"))
            syncedFlow.value = true
            advanceUntilIdle()

            awaitItem()
            cancelAndIgnoreRemainingEvents()
        }
        // The list screen we bounce to owns the "That session has ended." copy.
        verify(exactly = 1) { sessionRepository.notifySessionEnded("test-session") }
    }

    @Test
    fun sessionEnded_emitsAtMostOnce_acrossFurtherSnapshots() = runTest {
        val vm = createViewModel()

        vm.sessionEnded.test {
            syncedFlow.value = true
            advanceUntilIdle()
            awaitItem()

            sessionsFlow.value = listOf(session("someone-else"))
            advanceUntilIdle()

            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionEnded_doesNotEmit_whenTheListIsNotAgentAuthoritative() = runTest {
        val vm = createViewModel()

        vm.sessionEnded.test {
            // Absent from a stale list is not evidence: this is exactly the
            // cached list an agent restart leaves behind.
            sessionsFlow.value = listOf(session("someone-else"))
            advanceUntilIdle()

            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 0) { sessionRepository.notifySessionEnded(any()) }
    }

    @Test
    fun sessionEnded_doesNotEmit_whenTheSessionEndsUnderTheUser() = runTest {
        val vm = createViewModel()

        vm.sessionEnded.test {
            sessionsFlow.value = listOf(session("test-session"))
            syncedFlow.value = true
            advanceUntilIdle()

            // It died while being read: the output stays on screen.
            sessionsFlow.value = emptyList()
            advanceUntilIdle()

            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 0) { sessionRepository.notifySessionEnded(any()) }
    }

    @Test
    fun sessionEnded_doesNotEmit_whenAStateFrameAlreadyProvedTheSessionAlive() = runTest {
        val vm = createViewModel()

        vm.sessionEnded.test {
            // The agent answering with a live PTY is proof the session exists,
            // whether or not its list snapshot has landed yet.
            emitState("running")
            advanceUntilIdle()
            assertEquals("the state frame must have been parsed", "running", vm.sessionState.value)

            syncedFlow.value = true
            advanceUntilIdle()

            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
        verify(exactly = 0) { sessionRepository.notifySessionEnded(any()) }
    }

    @Test
    fun sessionEnded_emits_whenTheStateFrameSaysTheSessionIsStopped() = runTest {
        val vm = createViewModel()

        vm.sessionEnded.test {
            // `stopped` is what the agent now answers an attach to an unknown
            // id with — it must not count as having seen the session alive.
            emitState("stopped")
            advanceUntilIdle()

            syncedFlow.value = true
            advanceUntilIdle()

            awaitItem()
            cancelAndIgnoreRemainingEvents()
        }
    }
}
