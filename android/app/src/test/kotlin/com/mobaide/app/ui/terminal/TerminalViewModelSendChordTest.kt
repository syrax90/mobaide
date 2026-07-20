package com.mobaide.app.ui.terminal

import androidx.lifecycle.SavedStateHandle
import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.local.TERMINAL_STATIC_COLUMNS_DEFAULT
import com.mobaide.app.data.local.TerminalDisplayMode
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WsEvent
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
 * keyed by the session kind's wire name; unparseable persisted tokens are
 * dropped silently; and NOTHING touches the chord store before
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
    private val sessionRepository = mockk<SessionRepository> {
        every { createSessionIoConnection("test-session") } returns connection
        every { observeSessionList() } returns MutableStateFlow(emptyList())
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
        val vm = createViewModel()

        vm.sendChord(KeyChord(ctrl = true, key = "a"))
        advanceUntilIdle()

        // "claude" pins SessionKind.wireName as the bucket key (the VM's kind
        // defaults to Claude until the session list resolves).
        coVerify(exactly = 1) { preferencesStore.addRecentShortcutChord("claude", "ctrl+a") }
        assertEquals(
            "a sent chord must reappear in recents without reopening the sheet",
            listOf(KeyChord(ctrl = true, key = "a")),
            vm.shortcutRecents.value,
        )
    }

    @Test
    fun sendChord_persistFailure_stillSendsBytes() = runTest {
        coEvery {
            preferencesStore.addRecentShortcutChord(any(), any())
        } throws RuntimeException("disk full")
        val vm = createViewModel()
        val captured = slot<ByteString>()

        vm.sendChord(KeyChord(ctrl = true, key = "a"))
        advanceUntilIdle()

        verify(timeout = 2000) { connection.sendBinary(capture(captured)) }
        assertArrayEquals(
            "MRU persistence is a nicety — its failure must never lose the keystroke",
            byteArrayOf(0x01),
            payloadOf(captured.captured),
        )
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
        val vm = createViewModel()

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
