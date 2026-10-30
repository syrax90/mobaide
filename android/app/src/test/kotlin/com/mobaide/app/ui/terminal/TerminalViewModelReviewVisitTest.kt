package com.mobaide.app.ui.terminal

import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.review.ReviewPromptCoordinator
import com.mobaide.app.data.review.ReviewPromptState
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
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.shadows.ShadowSystemClock
import java.time.Duration

/**
 * A real [ReviewPromptCoordinator] turns the report into the observable
 * outcome, a counted good session. Robolectric so the meter's
 * `SystemClock.elapsedRealtime` is the shadow clock the test advances, and so
 * `parseStatus` resolves `org.json.JSONObject` for real.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewModelReviewVisitTest {

    private val testDispatcher = StandardTestDispatcher()

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule(testDispatcher)

    /** `createUnsafe` skips the main-thread assertion a unit test cannot satisfy. */
    private class FakeLifecycleOwner : LifecycleOwner {
        val registry: LifecycleRegistry = LifecycleRegistry.createUnsafe(this)
        override val lifecycle: Lifecycle get() = registry
    }

    private val owner = FakeLifecycleOwner()
    private val appForegroundTracker = AppForegroundTracker()
    private val foregroundSessionTracker = ForegroundSessionTracker()

    private val eventsFlow: MutableSharedFlow<WsEvent> =
        MutableSharedFlow(replay = 1, extraBufferCapacity = 100)

    private val connection: WebSocketConnection = mockk(relaxed = true) {
        every { events } returns eventsFlow
    }

    private val repository: SessionRepository = mockk {
        every { createSessionIoConnection("test-session") } returns connection
        every { observeSessionList() } returns MutableStateFlow(emptyList())
        every { observeSessionsSynced() } returns MutableStateFlow(false)
    }

    private var goodSessions = 0

    /** First launch, so a counted outing never goes on to request a review. */
    private val preferencesStore = mockk<PreferencesStore> {
        every { terminalDisplayMode } returns flowOf(TerminalDisplayMode.Full)
        every { terminalStaticColumns } returns flowOf(TERMINAL_STATIC_COLUMNS_DEFAULT)
        coEvery { incrementReviewGoodSessions() } answers {
            goodSessions += 1
            ReviewPromptState(launchCount = 1, goodSessions = goodSessions, lastRequestedAtMs = null)
        }
    }

    private val reviewPromptCoordinator =
        ReviewPromptCoordinator(preferencesStore, foregroundSessionTracker)

    private fun stateFrame(status: String): WsEvent =
        WsEvent.Binary(
            byteArrayOf(TerminalProtocol.TYPE_STATE)
                .plus("""{"status":"$status","pid":1234}""".toByteArray(Charsets.UTF_8))
                .toByteString(),
        )

    /** On screen, foregrounded, `Live` and `running`: the state in which healthy time runs. */
    private fun TestScope.healthyTerminal(): TerminalViewModel {
        owner.registry.currentState = Lifecycle.State.STARTED
        appForegroundTracker.start(owner)
        val vm = TerminalViewModel(
            savedStateHandle = SavedStateHandle(mapOf("sessionId" to "test-session")),
            sessionRepository = repository,
            customAgentTypesRepository = mockk {
                every { observeTypes() } returns MutableStateFlow(emptyList())
            },
            templatesRepository = mockk(relaxed = true),
            shortcutsRepository = mockk(),
            foregroundSessionTracker = foregroundSessionTracker,
            appForegroundTracker = appForegroundTracker,
            preferencesStore = preferencesStore,
            reviewPromptCoordinator = reviewPromptCoordinator,
        )
        vm.markScreenVisible()
        eventsFlow.tryEmit(WsEvent.Connected)
        eventsFlow.tryEmit(stateFrame("running"))
        runCurrent()
        assertEquals(ConnectionState.Live, vm.connectionState.value)
        assertEquals("running", vm.sessionState.value)
        return vm
    }

    private fun elapse(seconds: Long) {
        ShadowSystemClock.advanceBy(Duration.ofSeconds(seconds))
    }

    @Test
    fun liveAndRunningAtTheHide_afterAMinute_countsAGoodOuting() = runTest(testDispatcher) {
        val vm = healthyTerminal()
        elapse(61)

        vm.markScreenHidden()
        reviewPromptCoordinator.sessionListShown()

        assertEquals(1, goodSessions)
    }

    @Test
    fun visitThatEndsReconnecting_isNotClean() = runTest(testDispatcher) {
        val vm = healthyTerminal()
        elapse(61)
        eventsFlow.tryEmit(WsEvent.Disconnected)
        runCurrent()

        vm.markScreenHidden()
        reviewPromptCoordinator.sessionListShown()

        assertEquals(0, goodSessions)
    }

    @Test
    fun userStoppedTheSession_endsCleanly() = runTest(testDispatcher) {
        val vm = healthyTerminal()
        elapse(61)
        vm.markEndedByUser()
        eventsFlow.tryEmit(stateFrame("stopped"))
        eventsFlow.tryEmit(WsEvent.Disconnected)
        runCurrent()

        vm.markScreenHidden()
        reviewPromptCoordinator.sessionListShown()

        assertEquals(1, goodSessions)
    }

    @Test
    fun userStop_doesNotCarryIntoTheNextVisit() = runTest(testDispatcher) {
        val vm = healthyTerminal()
        vm.markEndedByUser()
        vm.markScreenHidden()
        reviewPromptCoordinator.sessionListShown()

        vm.markScreenVisible()
        runCurrent()
        elapse(61)
        eventsFlow.tryEmit(WsEvent.Disconnected)
        runCurrent()
        vm.markScreenHidden()
        reviewPromptCoordinator.sessionListShown()

        assertEquals(0, goodSessions)
    }

    @Test
    fun errorShownDuringTheFirstVisit_spoilsTheOuting() = runTest(testDispatcher) {
        val vm = healthyTerminal()
        elapse(61)
        vm.noteReviewError()

        vm.markScreenHidden()
        reviewPromptCoordinator.sessionListShown()

        assertEquals(0, goodSessions)
    }

    @Test
    fun timeInTheBackground_isNotHealthy() = runTest(testDispatcher) {
        val vm = healthyTerminal()
        owner.registry.currentState = Lifecycle.State.CREATED
        runCurrent()
        elapse(61)
        owner.registry.currentState = Lifecycle.State.STARTED
        runCurrent()
        assertEquals(ConnectionState.Live, vm.connectionState.value)

        vm.markScreenHidden()
        reviewPromptCoordinator.sessionListShown()

        assertEquals(0, goodSessions)
    }
}
