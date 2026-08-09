package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.SessionKind
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.data.repository.TemplatesRepository
import com.mobaide.app.notifications.ForegroundSessionTracker
import com.mobaide.app.testing.MainDispatcherRule
import io.mockk.coEvery
import io.mockk.coJustRun
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import io.mockk.verify
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.runTest
import okio.ByteString
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

/**
 * Unit tests for the [TerminalViewModel] custom-shortcut path:
 * [TerminalViewModel.sendChord] and [TerminalViewModel.refreshShortcutRecents].
 *
 * Load-bearing contracts: a chord travels the raw 'i'-frame path with its
 * encoded bytes; the send is unconditional while MRU persistence is
 * fire-and-forget (a store failure must never lose the keystroke); recents are
 * keyed by the session kind's wire name — MRU writes are skipped while the
 * kind is unresolved (null since proto-v7 — never recorded under a guessed
 * Claude bucket) and a picker-open refresh waits for resolution instead of
 * no-opping; unparseable persisted tokens are dropped silently; and
 * NOTHING touches the chord store before
 * [TerminalViewModel.refreshShortcutRecents]/[TerminalViewModel.sendChord] fire
 * — every existing ViewModel test stubs [PreferencesStore] strictly with only
 * the display-mode flows, so any eager access would break the whole suite.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TerminalViewModelSendChordTest {

    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    private val eventsFlow = MutableSharedFlow<WsEvent>(replay = 1, extraBufferCapacity = 100)
    private val connection = mockk<WebSocketConnection>(relaxed = true) {
        every { events } returns eventsFlow
    }

    /** Empty by default — the kind stays unresolved (null). Bucket-pinning
     *  tests resolve it by seeding a session BEFORE building the VM. */
    private val sessionListFlow = MutableStateFlow<List<SessionResponse>>(emptyList())

    private val sessionRepository = mockk<SessionRepository> {
        every { createSessionIoConnection("test-session") } returns connection
        every { observeSessionList() } returns sessionListFlow
        every { observeSessionsSynced() } returns MutableStateFlow(false)
    }

    private fun resolveKind(kind: SessionKind = SessionKind.Claude) {
        sessionListFlow.value = listOf(
            SessionResponse(
                session_id = "test-session",
                state = "running",
                created_at = "2026-01-01T00:00:00Z",
                kind = kind,
            ),
        )
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

    /** Strip the frame-type prefix byte to get the raw PTY payload. */
    private fun payloadOf(frame: ByteString): ByteArray =
        frame.toByteArray().let { it.copyOfRange(1, it.size) }

    private fun frameTypeOf(frame: ByteString): Byte = frame.toByteArray()[0]

    @Test
    fun sendChord_sendsEncodedBytes_asRawInputFrame() = runTest {
        coJustRun { preferencesStore.addRecentShortcutChord(any(), any()) }
        coEvery { preferencesStore.getRecentShortcutChords(any()) } returns emptyList()
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendChord(KeyChord(ctrl = true, key = "a"))
        advanceUntilIdle()

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertEquals(
            "chords must use the raw 'i' (input) frame, never the submit path",
            'i'.code.toByte(),
            frameTypeOf(captured.captured),
        )
        assertArrayEquals(byteArrayOf(0x01), payloadOf(captured.captured))
    }

    @Test
    fun sendChord_persistsTokenUnderWireNameBucket_andReloadsRecents() = runTest {
        coJustRun { preferencesStore.addRecentShortcutChord(any(), any()) }
        coEvery { preferencesStore.getRecentShortcutChords("claude") } returns listOf("ctrl+a")
        resolveKind(SessionKind.Claude)
        val vm = createViewModel()
        advanceUntilIdle() // let sessionKind resolve from the seeded list

        vm.sendChord(KeyChord(ctrl = true, key = "a"))
        advanceUntilIdle()

        // "claude" pins SessionKind.wireName as the bucket key, taken from the
        // RESOLVED kind — an unresolved kind skips MRU entirely (see below).
        coVerify(exactly = 1) { preferencesStore.addRecentShortcutChord("claude", "ctrl+a") }
        assertEquals(
            "a sent chord must reappear in recents without reopening the sheet",
            listOf(KeyChord(ctrl = true, key = "a")),
            vm.shortcutRecents.value,
        )
    }

    @Test
    fun sendChord_customKind_persistsUnderRawWireKindBucket() = runTest {
        // Custom-type sessions bucket under their id-based wire string —
        // rename-stable, never the display name.
        coJustRun { preferencesStore.addRecentShortcutChord(any(), any()) }
        coEvery {
            preferencesStore.getRecentShortcutChords("custom_ab12cd34ef56")
        } returns emptyList()
        resolveKind(SessionKind.Custom("custom_ab12cd34ef56"))
        val vm = createViewModel()
        advanceUntilIdle()

        vm.sendChord(KeyChord(ctrl = true, key = "a"))
        advanceUntilIdle()

        coVerify(exactly = 1) {
            preferencesStore.addRecentShortcutChord("custom_ab12cd34ef56", "ctrl+a")
        }
    }

    @Test
    fun sendChord_persistFailure_stillSendsBytes() = runTest {
        coEvery {
            preferencesStore.addRecentShortcutChord(any(), any())
        } throws RuntimeException("disk full")
        resolveKind(SessionKind.Claude)
        val vm = createViewModel()
        advanceUntilIdle() // resolve the kind so the persist path actually runs
        val captured = slot<ByteString>()

        vm.sendChord(KeyChord(ctrl = true, key = "a"))
        advanceUntilIdle()

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "MRU persistence is a nicety — its failure must never lose the keystroke",
            byteArrayOf(0x01),
            payloadOf(captured.captured),
        )
        coVerify(exactly = 1) { preferencesStore.addRecentShortcutChord("claude", "ctrl+a") }
    }

    @Test
    fun sendChord_kindUnresolved_sendsBytes_withoutTouchingMruBucket() = runTest {
        // No chord-store stubs on purpose: while the kind is null the strict
        // mock proves NOTHING records under a guessed bucket, yet the
        // keystroke still travels the raw path.
        val vm = createViewModel()
        advanceUntilIdle()
        val captured = slot<ByteString>()

        vm.sendChord(KeyChord(ctrl = true, key = "a"))
        advanceUntilIdle()

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(byteArrayOf(0x01), payloadOf(captured.captured))
        coVerify(exactly = 0) { preferencesStore.addRecentShortcutChord(any(), any()) }
        coVerify(exactly = 0) { preferencesStore.getRecentShortcutChords(any()) }
    }

    @Test
    fun refreshShortcutRecents_kindUnresolved_readsNothingWhilePending() = runTest {
        // The load waits for kind resolution rather than no-opping — while it
        // is still pending the strict mock pins the zero-access contract (no
        // bucket guess, no read).
        val vm = createViewModel()
        advanceUntilIdle()

        vm.refreshShortcutRecents()
        advanceUntilIdle()

        assertTrue(vm.shortcutRecents.value.isEmpty())
        coVerify(exactly = 0) { preferencesStore.getRecentShortcutChords(any()) }
    }

    @Test
    fun refreshShortcutRecents_pendingLoadResumes_whenKindResolves() = runTest {
        // Picker opened before the first sessions snapshot: the load suspends,
        // then self-heals into the RESOLVED kind's bucket — here a custom
        // type, pinning that the awaited wireName is used, never a guess.
        coEvery {
            preferencesStore.getRecentShortcutChords("custom_ab12cd34ef56")
        } returns listOf("ctrl+a")
        val vm = createViewModel()
        advanceUntilIdle()

        vm.refreshShortcutRecents()
        advanceUntilIdle()
        coVerify(exactly = 0) { preferencesStore.getRecentShortcutChords(any()) }

        resolveKind(SessionKind.Custom("custom_ab12cd34ef56"))
        advanceUntilIdle()

        assertEquals(
            "a pre-resolution picker open must self-heal once the kind lands",
            listOf(KeyChord(ctrl = true, key = "a")),
            vm.shortcutRecents.value,
        )
        coVerify(exactly = 1) {
            preferencesStore.getRecentShortcutChords("custom_ab12cd34ef56")
        }
    }

    @Test
    fun refreshShortcutRecents_reopenWhilePending_loadsExactlyOnce() = runTest {
        // Cancel-and-replace: reopening the picker while the first open is
        // still awaiting kind resolution must not stack a second collector —
        // resolution then triggers exactly one bucket read.
        coEvery { preferencesStore.getRecentShortcutChords("claude") } returns listOf("ctrl+a")
        val vm = createViewModel()
        advanceUntilIdle()

        vm.refreshShortcutRecents()
        advanceUntilIdle() // first load reaches its suspension point
        vm.refreshShortcutRecents()
        advanceUntilIdle()

        resolveKind(SessionKind.Claude)
        advanceUntilIdle()

        assertEquals(listOf(KeyChord(ctrl = true, key = "a")), vm.shortcutRecents.value)
        coVerify(exactly = 1) { preferencesStore.getRecentShortcutChords("claude") }
    }

    @Test
    fun sendChord_unencodableChord_sendsNothing_andPersistsNothing() = runTest {
        val vm = createViewModel()

        vm.sendChord(KeyChord(ctrl = true, alt = true, shift = true, key = "a"))
        advanceUntilIdle()

        verify(exactly = 0) { connection.sendBinary(any()) }
        coVerify(exactly = 0) { preferencesStore.addRecentShortcutChord(any(), any()) }
    }

    @Test
    fun refreshShortcutRecents_dropsUnparseableTokens() = runTest {
        coEvery { preferencesStore.getRecentShortcutChords("claude") } returns listOf(
            "ctrl+a",
            "meta+x",
            "ctrl+alt+shift+a",
            "bogus",
            "shift+f5",
        )
        resolveKind(SessionKind.Claude)
        val vm = createViewModel()
        advanceUntilIdle()

        vm.refreshShortcutRecents()
        advanceUntilIdle()

        assertEquals(
            "stale or forward-incompatible tokens must be dropped silently",
            listOf(KeyChord(ctrl = true, key = "a"), KeyChord(shift = true, key = "f5")),
            vm.shortcutRecents.value,
        )
    }

    @Test
    fun shortcutRecents_emptyAndUntouched_beforeFirstRefresh() = runTest {
        // No coEvery stubs for the chord methods on purpose: the strict mock
        // makes any eager store access blow up, and the coVerify pins it to
        // zero — the lazy-load constraint every existing VM test relies on.
        val vm = createViewModel()
        advanceUntilIdle()

        assertTrue(vm.shortcutRecents.value.isEmpty())
        coVerify(exactly = 0) { preferencesStore.getRecentShortcutChords(any()) }
    }
}
