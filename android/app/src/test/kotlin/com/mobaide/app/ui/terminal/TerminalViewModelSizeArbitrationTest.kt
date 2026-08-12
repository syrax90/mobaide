package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.TerminalProtocol
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.every
import io.mockk.mockk
import java.util.Collections
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import okio.ByteString
import okio.ByteString.Companion.toByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The proto-v8 latest-active size-arbitration state machine in
 * [TerminalViewModel]: `'w'`-frame handling ([SizeAuthority] transitions,
 * [TerminalViewModel.followerGrid]/[TerminalViewModel.attachedClients]), the
 * follower gate on [TerminalViewModel.resize]/`resendCachedSize`, the
 * v7-agent replay-end fallback, and the typing/foreground takeover paths.
 *
 * Robolectric (sdk 33) is required so the `'s'` state frame's JSON parses
 * and `sessionState` can reach `"running"` — the takeover gate reads it.
 *
 * Outbound frames cross the VM's real single-consumer send pump, so they
 * land on [WebSocketConnection.sendBinary] asynchronously (the reason
 * [TerminalViewModelSendInputTest] verifies with `timeout`). Positive
 * expectations wait with [awaitFrames]; "nothing was sent" expectations are
 * proven with a later *sentinel* frame instead of a sleep — the pump is FIFO,
 * so once the sentinel arrived, an earlier gated frame can never surface.
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class TerminalViewModelSizeArbitrationTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val eventsFlow = MutableSharedFlow<WsEvent>(replay = 1, extraBufferCapacity = 100)
    private val sentFrames: MutableList<ByteString> =
        Collections.synchronizedList(mutableListOf())
    private val connection = mockk<WebSocketConnection>(relaxed = true) {
        every { events } returns eventsFlow
        every { sendBinary(capture(sentFrames)) } returns true
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
            preferencesStore = preferencesStore,
        )

    private suspend fun TestScope.goLiveRunning() {
        eventsFlow.emit(WsEvent.Connected)
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_STATE)
                    .plus("""{"status":"running","pid":1234}""".toByteArray(Charsets.UTF_8))
                    .toByteString(),
            ),
        )
        advanceUntilIdle()
    }

    private suspend fun TestScope.deliverWinsize(rows: Int, cols: Int, clients: Int) {
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(
                    TerminalProtocol.TYPE_WINSIZE,
                    (rows ushr 8).toByte(), rows.toByte(),
                    (cols ushr 8).toByte(), cols.toByte(),
                    (clients ushr 8).toByte(), clients.toByte(),
                ).toByteString(),
            ),
        )
        advanceUntilIdle()
    }

    private suspend fun TestScope.deliverReplaySpan() {
        eventsFlow.emit(WsEvent.Binary(byteArrayOf(TerminalProtocol.TYPE_REPLAY).toByteString()))
        eventsFlow.emit(WsEvent.Binary(byteArrayOf(TerminalProtocol.TYPE_REPLAY_END).toByteString()))
        advanceUntilIdle()
    }

    private suspend fun TestScope.deliverState(status: String) {
        eventsFlow.emit(
            WsEvent.Binary(
                byteArrayOf(TerminalProtocol.TYPE_STATE)
                    .plus("""{"status":"$status"}""".toByteArray(Charsets.UTF_8))
                    .toByteString(),
            ),
        )
        advanceUntilIdle()
    }

    /** Block (real time, bounded) until the async send pump delivered [count] frames. */
    private fun awaitFrames(count: Int, timeoutMs: Long = 2000) {
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (sentFrames.size >= count) return
            Thread.sleep(10)
        }
        fail("send pump delivered ${sentFrames.size} frames, expected $count")
    }

    private fun frames(): List<ByteString> = synchronized(sentFrames) { sentFrames.toList() }

    /** Solo VM with a cached natural grid of 40x120 (one `'r'` frame sent). */
    private suspend fun TestScope.soloWithCachedGrid(vm: TerminalViewModel) {
        goLiveRunning()
        deliverWinsize(24, 80, 1)
        vm.resize(rows = 40, cols = 120)
        advanceTimeBy(101) // the 100 ms resize debounce
        advanceUntilIdle()
        awaitFrames(1)
        assertEquals(listOf(TerminalProtocol.encodeResize(40, 120)), frames())
    }

    /** Follower whose 400 ms foreground claim already elapsed against a send
     *  gate closed by a `stopped` state: the claim is parked, not dropped. */
    private suspend fun TestScope.parkForegroundClaim(vm: TerminalViewModel) {
        soloWithCachedGrid(vm)
        deliverWinsize(60, 150, 2)
        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)
        deliverState("stopped")
        vm.onScreenVisible()
        advanceTimeBy(401)
        advanceUntilIdle()
        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)
    }

    @Test
    fun freshViewModel_recordsThePreWinsizeLayout_andDeliversItOnSoloPromotion() = runTest {
        // A fresh model attaching to a session another client is driving must
        // adopt, not steal: layout passes stay local until a 'w' (or the v7
        // replay-end fallback) resolves the role. But with no foreign grid
        // adopted yet, the report IS this client's natural grid — it must be
        // recorded (still sending nothing), or the promotion would find an
        // empty cache and leave a fresh 24x80 PTY at the spawn size with
        // nothing left to re-fire the grid report. The exact frame list
        // proves the pre-promotion pass sent nothing of its own.
        val vm = createViewModel()
        goLiveRunning()
        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)

        vm.resize(rows = 40, cols = 120)
        advanceTimeBy(200)
        advanceUntilIdle()

        deliverWinsize(24, 80, 1)
        assertEquals(SizeAuthority.Solo, vm.sizeAuthority.value)
        awaitFrames(1)
        assertEquals(listOf(TerminalProtocol.encodeResize(40, 120)), frames())

        // Solo now: later layout passes flow through the normal debounce.
        vm.resize(rows = 50, cols = 132)
        advanceTimeBy(101)
        advanceUntilIdle()
        awaitFrames(2)
        assertEquals(
            listOf(
                TerminalProtocol.encodeResize(40, 120),
                TerminalProtocol.encodeResize(50, 132),
            ),
            frames(),
        )
    }

    @Test
    fun preWinsizeLayoutReport_survivesToTheV7ReplayEndPromotion() = runTest {
        // Same seed, v7 path: the agent never sends 'w', replay-end promotes
        // to Solo, and enterLiveState's re-assert must find the recorded grid.
        val vm = createViewModel()
        goLiveRunning()
        vm.resize(rows = 40, cols = 120)
        advanceTimeBy(200)
        advanceUntilIdle()

        deliverReplaySpan()

        assertEquals(SizeAuthority.Solo, vm.sizeAuthority.value)
        awaitFrames(1)
        assertEquals(listOf(TerminalProtocol.encodeResize(40, 120)), frames())
    }

    @Test
    fun echoOfTheInFlightSend_keepsOwnership_andTheCorrectiveResizeStillFires() = runTest {
        // Mid-drag the cache runs a debounce ahead of the wire, and the agent
        // echoes the frame that actually went out. Matching only the cache
        // would demote the very client that is actively resizing — and cancel
        // its pending corrective frame with the demotion.
        val vm = createViewModel()
        goLiveRunning()
        deliverWinsize(24, 80, 1)
        vm.resize(rows = 40, cols = 118)
        advanceTimeBy(101)
        advanceUntilIdle()
        awaitFrames(1)

        vm.resize(rows = 40, cols = 120) // debounce pending — the wire trails
        deliverWinsize(40, 118, 2) // echo of the in-flight send

        assertEquals(SizeAuthority.Owner, vm.sizeAuthority.value)
        assertNull(vm.followerGrid.value)
        awaitFrames(2)
        assertEquals(
            listOf(
                TerminalProtocol.encodeResize(40, 118),
                TerminalProtocol.encodeResize(40, 120), // the corrective survived
            ),
            frames(),
        )
    }

    @Test
    fun echoOfAPromotionResend_keepsOwnership() = runTest {
        // The other send site: resendCachedSize must record what it put on
        // the wire too, or the echo of a promotion's assert would demote this
        // client as soon as its layout moves on.
        val vm = createViewModel()
        goLiveRunning()
        vm.resize(rows = 40, cols = 120) // pre-'w' seed, nothing sent
        deliverWinsize(24, 80, 1) // promotion assert via resendCachedSize
        awaitFrames(1)

        vm.resize(rows = 50, cols = 132) // cache moves; debounce pending
        deliverWinsize(40, 120, 2) // echo of the resend

        assertEquals(SizeAuthority.Owner, vm.sizeAuthority.value)
        awaitFrames(2)
        assertEquals(
            listOf(
                TerminalProtocol.encodeResize(40, 120),
                TerminalProtocol.encodeResize(50, 132),
            ),
            frames(),
        )
    }

    @Test
    fun soloMismatch_reassertsTheCachedGridImmediately() = runTest {
        // "Other client detached, its grid lingers on the PTY": the solo
        // report differs from the cache, so the natural grid is re-delivered
        // with no debounce.
        val vm = createViewModel()
        soloWithCachedGrid(vm)

        deliverWinsize(24, 80, 1)

        awaitFrames(2)
        assertEquals(
            listOf(
                TerminalProtocol.encodeResize(40, 120),
                TerminalProtocol.encodeResize(40, 120),
            ),
            frames(),
        )
    }

    @Test
    fun soloMatchingReport_staysSilent() = runTest {
        val vm = createViewModel()
        soloWithCachedGrid(vm)

        deliverWinsize(40, 120, 1)

        // Sentinel: a genuine layout change arrives after the matching report;
        // if the report had echoed a redundant frame it would sit between the
        // two on the FIFO pump.
        vm.resize(rows = 50, cols = 132)
        advanceTimeBy(101)
        advanceUntilIdle()
        awaitFrames(2)
        assertEquals(
            listOf(
                TerminalProtocol.encodeResize(40, 120),
                TerminalProtocol.encodeResize(50, 132),
            ),
            frames(),
        )
    }

    @Test
    fun ownEcho_promotesToOwner_withoutSendingAnything() = runTest {
        val vm = createViewModel()
        soloWithCachedGrid(vm)

        deliverWinsize(40, 120, 2)

        assertEquals(SizeAuthority.Owner, vm.sizeAuthority.value)
        assertEquals(2, vm.attachedClients.value)
        assertNull(vm.followerGrid.value)
        assertEquals(listOf(TerminalProtocol.encodeResize(40, 120)), frames())
    }

    @Test
    fun foreignGrid_demotesToFollower_publishesGrid_andFreezesResizes() = runTest {
        val vm = createViewModel()
        soloWithCachedGrid(vm)
        vm.resize(rows = 35, cols = 110) // arms the debounce; demotion must cancel it

        deliverWinsize(60, 150, 3)

        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)
        assertEquals(FollowerGrid(rows = 60, cols = 150), vm.followerGrid.value)
        assertEquals(3, vm.attachedClients.value)

        // Give the canceled debounce (and a fresh gated layout pass) every
        // chance to fire, then send the takeover sentinel: on the FIFO pump
        // any leaked 'r' would surface before it. The takeover re-asserts
        // 35x110 — the debounced *frame* was canceled but the pre-demotion
        // layout pass had already recorded the latest natural grid, and that
        // frozen cache is exactly what a takeover is meant to deliver. The
        // post-demotion resize(10, 10) must leave no trace at all.
        advanceTimeBy(300)
        vm.resize(rows = 10, cols = 10)
        advanceTimeBy(300)
        advanceUntilIdle()
        vm.sendBytes(byteArrayOf(0x0d))
        advanceUntilIdle()

        awaitFrames(3)
        assertEquals(
            listOf(
                TerminalProtocol.encodeResize(40, 120),
                TerminalProtocol.encodeResize(35, 110), // the takeover re-assert
                TerminalProtocol.encodeInput(byteArrayOf(0x0d)),
            ),
            frames(),
        )
    }

    @Test
    fun replayEndWithoutWinsize_fallsBackToSolo() = runTest {
        // A v7 agent sends no 'w'; replay-end is the earliest safe moment to
        // conclude that and restore the pre-v8 sole-authority behavior.
        val vm = createViewModel()
        goLiveRunning()

        deliverReplaySpan()

        assertEquals(SizeAuthority.Solo, vm.sizeAuthority.value)
    }

    @Test
    fun replayEndAfterWinsize_staysFollower() = runTest {
        val vm = createViewModel()
        goLiveRunning()
        deliverWinsize(60, 150, 2)

        deliverReplaySpan()

        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)
    }

    @Test
    fun typingTakeover_sendsTheFrozenGridBeforeTheInputBytes() = runTest {
        val vm = createViewModel()
        soloWithCachedGrid(vm)
        deliverWinsize(60, 150, 2)
        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)

        vm.sendBytes(byteArrayOf(0x0d))
        advanceUntilIdle()

        awaitFrames(3)
        val appended = frames().drop(1)
        assertEquals(
            "takeover resize must precede the keystroke on the wire",
            TerminalProtocol.encodeResize(40, 120),
            appended[0],
        )
        assertEquals(TerminalProtocol.encodeInput(byteArrayOf(0x0d)), appended[1])
        assertEquals(SizeAuthority.Owner, vm.sizeAuthority.value)
        assertNull(vm.followerGrid.value)
    }

    @Test
    fun foregroundTakeover_firesAfterTheDebounce() = runTest {
        val vm = createViewModel()
        soloWithCachedGrid(vm)
        deliverWinsize(60, 150, 2)

        vm.onScreenVisible()
        advanceTimeBy(399)
        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)

        advanceTimeBy(2)
        advanceUntilIdle()

        assertEquals(SizeAuthority.Owner, vm.sizeAuthority.value)
        awaitFrames(2)
        assertEquals(
            listOf(
                TerminalProtocol.encodeResize(40, 120),
                TerminalProtocol.encodeResize(40, 120),
            ),
            frames(),
        )
    }

    @Test
    fun leavingTheScreen_cancelsThePendingForegroundTakeover() = runTest {
        val vm = createViewModel()
        soloWithCachedGrid(vm)
        deliverWinsize(60, 150, 2)

        vm.onScreenVisible()
        advanceTimeBy(200)
        vm.markScreenHidden()
        advanceTimeBy(500)
        advanceUntilIdle()

        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)
    }

    @Test
    fun neverSentMatchingGrid_staysFollower_withNothingOnTheWire() = runTest {
        // The own-echo owner branch is gated on an actual send (lastSent > 0):
        // the fresh-follower seeding records a merely-measured grid, and
        // reading that as "our size echoed back" would promote a fresh client
        // whose natural grid happens to equal the PTY's — it would then steal
        // the size on the next unattended layout pass.
        val vm = createViewModel()
        goLiveRunning()
        vm.resize(rows = 40, cols = 120) // follower with a null grid: cached only
        advanceTimeBy(200)
        advanceUntilIdle()

        deliverWinsize(40, 120, 2)

        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)
        assertEquals(FollowerGrid(rows = 40, cols = 120), vm.followerGrid.value)
        assertEquals(2, vm.attachedClients.value)

        // FIFO sentinel: the typing takeover's pair must be the first frames
        // ever sent — any 'r' leaked by the winsize would precede them.
        vm.sendBytes(byteArrayOf(0x0d))
        advanceUntilIdle()
        awaitFrames(2)
        assertEquals(
            listOf(
                TerminalProtocol.encodeResize(40, 120),
                TerminalProtocol.encodeInput(byteArrayOf(0x0d)),
            ),
            frames(),
        )
    }

    @Test
    fun parkedForegroundClaim_firesExactlyOnce_whenTheGateReopens() = runTest {
        // An elapsed claim that found the gate closed must survive (steady
        // foreground produces no new edge to re-arm it) and fire on the
        // status flip — but only once.
        val vm = createViewModel()
        parkForegroundClaim(vm)

        deliverState("running") // the gate reopens → the parked claim fires
        assertEquals(SizeAuthority.Owner, vm.sizeAuthority.value)

        deliverState("running") // consumed: a later gate sync must not re-fire
        vm.sendBytes(byteArrayOf(0x0d)) // FIFO sentinel; takeoverNow no-ops as Owner
        advanceUntilIdle()
        awaitFrames(3)
        assertEquals(
            listOf(
                TerminalProtocol.encodeResize(40, 120), // the solo seed
                TerminalProtocol.encodeResize(40, 120), // the one takeover assert
                TerminalProtocol.encodeInput(byteArrayOf(0x0d)),
            ),
            frames(),
        )
    }

    @Test
    fun hidingTheScreen_dropsTheParkedClaim_soTheGateReopeningStaysPassive() = runTest {
        val vm = createViewModel()
        parkForegroundClaim(vm)

        vm.markScreenHidden()
        deliverState("running") // reopens with no claim left to fire

        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)
    }

    @Test
    fun soloPromotionWhileParked_consumesTheClaim_beforeTheGateReopens() = runTest {
        // The promotion's authority change clears the parked claim; the
        // foreign grid that follows demotes again, and the reopening gate
        // must find no stale claim — only typing or a fresh foreground edge
        // may take over from here.
        val vm = createViewModel()
        parkForegroundClaim(vm)

        deliverWinsize(24, 80, 1) // solo: clears the claim, re-asserts the grid
        deliverWinsize(60, 150, 2) // foreign again before the reopen
        deliverState("running")

        assertEquals(SizeAuthority.Follower, vm.sizeAuthority.value)
        awaitFrames(2)
        assertEquals(
            listOf(
                TerminalProtocol.encodeResize(40, 120), // the solo seed
                TerminalProtocol.encodeResize(40, 120), // the solo-mismatch re-assert
            ),
            frames(),
        )
    }
}
