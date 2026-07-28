package com.mobaide.app.data.events

import com.mobaide.app.data.auth.SessionManager
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.WebSocketConnection
import com.mobaide.app.data.network.WebSocketManager
import com.mobaide.app.data.network.WsEvent
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.repository.AgentRepository
import com.mobaide.app.data.repository.CustomAgentTypesRepository
import com.mobaide.app.data.repository.ShortcutsRepository
import com.mobaide.app.data.repository.TemplatesRepository
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.just
import io.mockk.mockk
import io.mockk.verify
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import okio.ByteString
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Unit tests for [UserEventBus] — the user-scoped sister of [AgentEventBus].
 *
 * Focuses on behavior unique to the user-events path:
 *   - `agent.status_changed` envelopes are forwarded to
 *     [AgentRepository.updateAgentOnline] with the correct args.
 *   - `template.created` / `template.updated` envelopes are forwarded to
 *     [TemplatesRepository.applyUpserted] with the parsed [TemplateDto].
 *   - `template.deleted` envelopes are forwarded to
 *     [TemplatesRepository.applyDeleted] with just the id.
 *   - `shortcut.created` / `shortcut.updated` / `shortcut.deleted` envelopes
 *     are forwarded to [ShortcutsRepository] the same way.
 *   - Malformed envelopes / unknown types / wrong-typed payloads are
 *     silently dropped and do not crash the collector.
 *   - `WsEvent.Connected` triggers `refreshAgents()`, `refreshTemplates()`,
 *     and `refreshShortcuts()` (reconnect resync).
 *   - `WsEvent.Binary` is ignored (channel is text-only).
 *
 * The connection state machine and auth-lifecycle wiring are already
 * covered structurally by [AgentEventBusTest]; they are not re-tested here.
 *
 * The bus's event collector runs on `Dispatchers.Default`, so verification
 * is performed with MockK's `verify(timeout = ...)` — the same pattern
 * used by the project's existing `TerminalViewModelResizeTest` — which
 * polls on real wall time rather than the `runTest { }` virtual clock.
 * A 2000 ms budget is effectively instant on CI and keeps the tests
 * deterministic on slow machines.
 */
class UserEventBusTest {

    // ---- Fixture ------------------------------------------------------------

    // New collaborators are appended LAST: tests positionally destructure
    // prefixes of this fixture, so inserting in the middle would silently
    // shift every existing destructuring.
    private data class Fixture(
        val bus: UserEventBus,
        val connEvents: MutableSharedFlow<WsEvent>,
        val conn: WebSocketConnection,
        val wsm: WebSocketManager,
        val agentRepository: AgentRepository,
        val templatesRepository: TemplatesRepository,
        val shortcutsRepository: ShortcutsRepository,
        val customAgentTypesRepository: CustomAgentTypesRepository,
        val authStateFlow: MutableStateFlow<SessionManager.AuthState>,
    )

    private fun newBus(initiallyLoggedIn: Boolean = true): Fixture {
        val connEvents = MutableSharedFlow<WsEvent>(replay = 0, extraBufferCapacity = 64)
        val conn = mockk<WebSocketConnection>(relaxed = true)
        every { conn.events } returns connEvents

        val wsm = mockk<WebSocketManager>()
        every { wsm.createUserConnection("ws/user-events") } returns conn

        val sessionManager = mockk<SessionManager>(relaxed = true)
        val initial: SessionManager.AuthState =
            if (initiallyLoggedIn) SessionManager.AuthState.FullyConnected
            else SessionManager.AuthState.Unauthenticated
        val authStateFlow = MutableStateFlow<SessionManager.AuthState>(initial)
        every { sessionManager.authState } returns authStateFlow

        val userIdentityStore = mockk<UserIdentityStore>()
        every { userIdentityStore.isUserLoggedIn() } returns initiallyLoggedIn

        val agentRepository = mockk<AgentRepository>()
        every { agentRepository.updateAgentOnline(any(), any()) } just Runs
        coEvery { agentRepository.refreshAgents() } just Runs

        val templatesRepository = mockk<TemplatesRepository>()
        every { templatesRepository.applyUpserted(any()) } just Runs
        every { templatesRepository.applyDeleted(any()) } just Runs
        coEvery { templatesRepository.refreshTemplates() } just Runs

        val shortcutsRepository = mockk<ShortcutsRepository>()
        every { shortcutsRepository.applyUpserted(any()) } just Runs
        every { shortcutsRepository.applyDeleted(any()) } just Runs
        every { shortcutsRepository.purgeKind(any()) } just Runs
        every { shortcutsRepository.clear() } just Runs
        coEvery { shortcutsRepository.refreshShortcuts() } just Runs
        every { templatesRepository.clear() } just Runs

        val customAgentTypesRepository = mockk<CustomAgentTypesRepository>()
        every { customAgentTypesRepository.applyUpserted(any()) } just Runs
        every { customAgentTypesRepository.applyDeleted(any()) } just Runs
        every { customAgentTypesRepository.clear() } just Runs
        coEvery { customAgentTypesRepository.refreshTypes() } just Runs

        val bus = UserEventBus(
            wsm, sessionManager, userIdentityStore,
            agentRepository, templatesRepository, shortcutsRepository,
            customAgentTypesRepository,
        )
        return Fixture(
            bus, connEvents, conn, wsm,
            agentRepository, templatesRepository, shortcutsRepository,
            customAgentTypesRepository, authStateFlow,
        )
    }

    /** Emit a WS event from a real coroutine context.
     *
     *  The bus's collector runs on `Dispatchers.Default`; if we tried to
     *  `emit()` from the `runTest { }` virtual-time scope the SharedFlow's
     *  suspension would never be dispatched to the real dispatcher. Using
     *  `runBlocking` here hops onto the real scheduler so the collector
     *  actually sees the emission. */
    private fun emit(events: MutableSharedFlow<WsEvent>, ev: WsEvent) {
        runBlocking { events.emit(ev) }
    }

    /** Wait until the bus has actually subscribed to the events flow.
     *
     *  `MutableSharedFlow(replay = 0)` silently drops emissions that happen
     *  before any subscriber has registered. `bus.start()` launches the
     *  subscribing coroutine on `Dispatchers.Default` asynchronously, so
     *  `emit()` immediately after `start()` races against the subscription.
     *  Polling `subscriptionCount` on wall time removes that race
     *  deterministically. */
    private fun awaitSubscribed(events: MutableSharedFlow<WsEvent>) = runBlocking {
        val deadline = System.currentTimeMillis() + 2000
        while (events.subscriptionCount.value == 0 &&
            System.currentTimeMillis() < deadline
        ) {
            kotlinx.coroutines.delay(10)
        }
        check(events.subscriptionCount.value > 0) {
            "UserEventBus did not subscribe within 2s"
        }
    }

    // ---- parse(): agent.status_changed -> updateAgentOnline -----------------

    @Test
    fun message_agentStatusChangedOnline_callsUpdateAgentOnline_withTrue() = runTest {
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """
                {
                  "type": "agent.status_changed",
                  "payload": {"agent_id":"a-42","online":true}
                }
                """.trimIndent(),
            ),
        )

        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-42", true) }

        bus.stop()
    }

    @Test
    fun message_agentStatusChangedOffline_callsUpdateAgentOnline_withFalse() = runTest {
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-9","online":false}}""",
            ),
        )

        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-9", false) }

        bus.stop()
    }

    @Test
    fun message_multipleAgentStatusChanges_areAllForwardedInOrder() = runTest {
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-1","online":true}}""",
            ),
        )
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-2","online":false}}""",
            ),
        )
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-1","online":false}}""",
            ),
        )

        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-1", true) }
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-2", false) }
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-1", false) }

        bus.stop()
    }

    // ---- parse(): malformed payloads are dropped silently -------------------

    @Test
    fun message_malformedEnvelopeJson_isSilentlySkipped_noRepoCall() = runTest {
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Message("this is not json at all {{{"))

        // A subsequent well-formed envelope must still be processed —
        // proves the bus didn't crash or stop consuming. Using the
        // positive verification as the synchronization point (waits for
        // the collector to actually process the follow-up message)
        // guarantees that by the time we assert the negative path above,
        // the malformed message has been fully handled.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-1","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("a-1", true) }

        // The malformed message must NOT have produced any other call.
        verify(exactly = 0) { repo.updateAgentOnline(neq("a-1"), any()) }

        bus.stop()
    }

    @Test
    fun message_envelopeMissingPayloadField_isSilentlySkipped() = runTest {
        // Envelope parses (has `type`) but `payload` is missing. The outer
        // decodeFromString call must throw MissingFieldException and the
        // catch block must swallow it.
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Message("""{"type":"agent.status_changed"}"""))

        // Synchronize on a valid follow-up call, then assert nothing else.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }

        bus.stop()
    }

    @Test
    fun message_agentStatusChangedPayloadMissingAgentId_isSilentlySkipped() = runTest {
        // Valid envelope, matching type, but inner payload lacks `agent_id`.
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"online":true}}""",
            ),
        )

        // Synchronize on a valid follow-up.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }

        bus.stop()
    }

    @Test
    fun message_agentStatusChangedPayloadWrongTypes_isSilentlySkipped() = runTest {
        // `online` as a string, not a bool — deserialization must fail
        // and the bus must remain alive for the next message.
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"a-1","online":"yes"}}""",
            ),
        )

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }

        bus.stop()
    }

    // ---- parse(): unknown event types are ignored ---------------------------

    @Test
    fun message_unknownEventType_isIgnored_noRepoCall() = runTest {
        // Forward-compatibility: any type other than agent.status_changed
        // must fall through the `else -> Unit` branch.
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"some.future.event","payload":{"whatever":1}}""",
            ),
        )
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"sessions.changed","payload":{"sessions":[]}}""",
            ),
        )

        // Synchronize on a valid message, then assert nothing else fired.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }

        bus.stop()
    }

    @Test
    fun message_sessionAttentionKind_isIgnored_noRepoCall() = runTest {
        // `session.attention` is a user-event kind for the iOS APNs
        // pipeline. Android's attention path stays on the agent events
        // channel, so this kind must keep falling through `else -> Unit` —
        // pinned explicitly (not just any unknown type) because the server
        // now emits it to every connected user socket.
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"session.attention","payload":{"session_id":"s1","reason":"turn_finished","title":"Fix bug","agent_id":"a1"}}""",
            ),
        )

        // Synchronize on a valid message, then assert nothing else fired.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }

        bus.stop()
    }

    // ---- Non-Message WsEvents: Connected refreshes, Binary is ignored -------

    @Test
    fun connected_event_triggersRefreshAgents() = runTest {
        // On every WS reconnect, the bus must call refreshAgents() to
        // reconcile any transitions that happened while disconnected.
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Connected)

        coVerify(timeout = 2000, exactly = 1) { repo.refreshAgents() }

        bus.stop()
    }

    @Test
    fun binary_event_isIgnored_noRepoCall() = runTest {
        // The user-events channel is text-only. Binary frames must not
        // reach the parser, and must not throw.
        val (bus, connEvents, _, _, repo, _) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Binary(ByteString.of(1, 2, 3, 4)))

        // Synchronize on a valid follow-up Message — by the time that
        // arrives the Binary frame has been fully observed by the
        // collector (SharedFlow preserves emission order to a subscriber).
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { repo.updateAgentOnline("sync", true) }

        // No other updateAgentOnline calls — the Binary frame was silent.
        verify(exactly = 1) { repo.updateAgentOnline(any(), any()) }
        // refreshAgents must NOT have been called — Binary is neither
        // Connected nor Message.
        coVerify(exactly = 0) { repo.refreshAgents() }

        bus.stop()
    }

    @Test
    fun connected_event_alsoTriggersRefreshTemplates() = runTest {
        // On WS reconnect the bus must reconcile templates as well as
        // agents — otherwise template.* events that fired while offline
        // would be lost.
        val (bus, connEvents, _, _, _, templates) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Connected)

        coVerify(timeout = 2000, exactly = 1) { templates.refreshTemplates() }

        bus.stop()
    }

    // ---- parse(): template.* envelopes -> TemplatesRepository --------------

    private fun templateJson(
        id: String = "t-1",
        name: String = "tpl",
        body: String = "body",
        favorite: Boolean = false,
        version: Int = 1,
    ): String =
        """
        {
          "template_id": "$id",
          "name": "$name",
          "body": "$body",
          "is_favorite": $favorite,
          "last_used_at": null,
          "created_at": "2026-04-30T12:00:00Z",
          "updated_at": "2026-04-30T12:00:00Z",
          "version": $version
        }
        """.trimIndent()

    @Test
    fun message_templateCreated_callsApplyUpserted_withParsedDto() = runTest {
        val (bus, connEvents, _, _, _, templates) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"template.created","payload":${templateJson(id = "t-42", name = "explain")}}""",
            ),
        )

        // Using the equals-based matcher rather than a CapturingSlot keeps
        // the verify path consistent with the existing
        // `message_agentStatusChangedOnline_callsUpdateAgentOnline_withTrue`
        // pattern in this file. TemplateDto is a data class so structural
        // equality on the parsed payload is deterministic.
        verify(timeout = 2000, exactly = 1) {
            templates.applyUpserted(
                match {
                    it.template_id == "t-42" &&
                        it.name == "explain" &&
                        it.body == "body"
                },
            )
        }
        verify(exactly = 1) { templates.applyUpserted(any()) }

        bus.stop()
    }

    @Test
    fun message_templateUpdated_callsApplyUpserted_withParsedDto() = runTest {
        val (bus, connEvents, _, _, _, templates) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"template.updated","payload":${templateJson(id = "t-9", favorite = true, version = 7)}}""",
            ),
        )

        verify(timeout = 2000, exactly = 1) {
            templates.applyUpserted(
                match {
                    it.template_id == "t-9" &&
                        it.is_favorite &&
                        it.version == 7
                },
            )
        }

        bus.stop()
    }

    @Test
    fun message_templateDeleted_callsApplyDeleted_withTemplateId() = runTest {
        val (bus, connEvents, _, _, _, templates) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """
                {
                  "type": "template.deleted",
                  "payload": {
                    "template_id": "t-doomed",
                    "version": 5,
                    "updated_at": "2026-04-30T13:00:00Z"
                  }
                }
                """.trimIndent(),
            ),
        )

        verify(timeout = 2000, exactly = 1) { templates.applyDeleted("t-doomed") }
        // applyUpserted must NOT fire for the delete envelope.
        verify(exactly = 0) { templates.applyUpserted(any()) }

        bus.stop()
    }

    @Test
    fun message_templateCreatedMalformedPayload_isSilentlySkipped() = runTest {
        // Wrong-typed inner payload (`is_favorite` as a string) — the inner
        // decode must fail and the bus must remain alive for the next
        // message. Mirrors the agent.status_changed wrong-type test above.
        val (bus, connEvents, _, _, agentRepo, templates) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """
                {
                  "type": "template.created",
                  "payload": {
                    "template_id": "t-1",
                    "name": "x",
                    "body": "y",
                    "is_favorite": "not-a-bool",
                    "created_at": "2026-04-30T12:00:00Z",
                    "updated_at": "2026-04-30T12:00:00Z",
                    "version": 1
                  }
                }
                """.trimIndent(),
            ),
        )

        // Synchronize on a valid follow-up agent.status_changed message —
        // by the time it's processed the malformed template payload has
        // also been observed by the collector. Then assert no template
        // method was called.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { agentRepo.updateAgentOnline("sync", true) }
        verify(exactly = 0) { templates.applyUpserted(any()) }
        verify(exactly = 0) { templates.applyDeleted(any()) }

        bus.stop()
    }

    @Test
    fun message_templateDeletedMissingId_isSilentlySkipped() = runTest {
        // template.deleted envelope without the required `template_id` field —
        // payload-level decode must throw and the bus must swallow it.
        val (bus, connEvents, _, _, agentRepo, templates) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"template.deleted","payload":{"version":1,"updated_at":"x"}}""",
            ),
        )

        // Sync on a valid follow-up.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { agentRepo.updateAgentOnline("sync", true) }
        verify(exactly = 0) { templates.applyDeleted(any()) }

        bus.stop()
    }

    private fun shortcutJson(
        id: String = "s-1",
        kind: String = "claude",
        chordToken: String = "ctrl+x",
        name: String? = null,
        position: Int = 0,
        version: Int = 1,
    ): String =
        """
        {
          "shortcut_id": "$id",
          "kind": "$kind",
          "chord_token": "$chordToken",
          "name": ${if (name != null) "\"$name\"" else "null"},
          "position": $position,
          "created_at": "2026-07-20T12:00:00Z",
          "updated_at": "2026-07-20T12:00:00Z",
          "version": $version
        }
        """.trimIndent()

    @Test
    fun connected_event_alsoTriggersRefreshShortcuts() = runTest {
        // On WS reconnect the bus must reconcile saved shortcuts as well —
        // otherwise shortcut.* events that fired while offline would be lost.
        val (bus, connEvents, _, _, _, _, shortcuts) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Connected)

        coVerify(timeout = 2000, exactly = 1) { shortcuts.refreshShortcuts() }

        bus.stop()
    }

    @Test
    fun message_shortcutCreated_callsApplyUpserted_withParsedDto() = runTest {
        val (bus, connEvents, _, _, _, _, shortcuts) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"shortcut.created","payload":${shortcutJson(id = "s-42", kind = "codex", chordToken = "ctrl+k", name = "Klear")}}""",
            ),
        )

        verify(timeout = 2000, exactly = 1) {
            shortcuts.applyUpserted(
                match {
                    it.shortcut_id == "s-42" &&
                        it.kind == "codex" &&
                        it.chord_token == "ctrl+k" &&
                        it.name == "Klear"
                },
            )
        }
        verify(exactly = 1) { shortcuts.applyUpserted(any()) }

        bus.stop()
    }

    @Test
    fun message_shortcutUpdated_callsApplyUpserted_withParsedDto() = runTest {
        val (bus, connEvents, _, _, _, _, shortcuts) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"shortcut.updated","payload":${shortcutJson(id = "s-9", name = null, version = 7)}}""",
            ),
        )

        verify(timeout = 2000, exactly = 1) {
            shortcuts.applyUpserted(
                match {
                    it.shortcut_id == "s-9" &&
                        it.name == null &&
                        it.version == 7
                },
            )
        }

        bus.stop()
    }

    @Test
    fun message_shortcutDeleted_callsApplyDeleted_withShortcutId() = runTest {
        val (bus, connEvents, _, _, _, _, shortcuts) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """
                {
                  "type": "shortcut.deleted",
                  "payload": {
                    "shortcut_id": "s-doomed",
                    "version": 5,
                    "updated_at": "2026-07-20T13:00:00Z"
                  }
                }
                """.trimIndent(),
            ),
        )

        verify(timeout = 2000, exactly = 1) { shortcuts.applyDeleted("s-doomed") }
        // applyUpserted must NOT fire for the delete envelope.
        verify(exactly = 0) { shortcuts.applyUpserted(any()) }

        bus.stop()
    }

    @Test
    fun message_shortcutCreatedMalformedPayload_isSilentlySkipped() = runTest {
        // Wrong-typed inner payload (`position` as a string) — the inner
        // decode must fail and the bus must remain alive for the next
        // message. Mirrors the template.created wrong-type test above.
        val (bus, connEvents, _, _, agentRepo, _, shortcuts) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """
                {
                  "type": "shortcut.created",
                  "payload": {
                    "shortcut_id": "s-1",
                    "kind": "claude",
                    "chord_token": "ctrl+x",
                    "name": null,
                    "position": "not-an-int",
                    "created_at": "2026-07-20T12:00:00Z",
                    "updated_at": "2026-07-20T12:00:00Z",
                    "version": 1
                  }
                }
                """.trimIndent(),
            ),
        )

        // Synchronize on a valid follow-up, then assert nothing fired.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { agentRepo.updateAgentOnline("sync", true) }
        verify(exactly = 0) { shortcuts.applyUpserted(any()) }
        verify(exactly = 0) { shortcuts.applyDeleted(any()) }

        bus.stop()
    }

    @Test
    fun message_shortcutDeletedMissingId_isSilentlySkipped() = runTest {
        // shortcut.deleted envelope without the required `shortcut_id` field —
        // payload-level decode must throw and the bus must swallow it.
        val (bus, connEvents, _, _, agentRepo, _, shortcuts) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"shortcut.deleted","payload":{"version":1,"updated_at":"x"}}""",
            ),
        )

        // Sync on a valid follow-up.
        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) { agentRepo.updateAgentOnline("sync", true) }
        verify(exactly = 0) { shortcuts.applyDeleted(any()) }

        bus.stop()
    }

    // ---- parse(): custom_type.* envelopes -> CustomAgentTypesRepository -----

    private fun customTypeJson(
        id: String = "ab12cd34ef56",
        name: String = "Aider",
        command: String = "aider --model sonnet",
    ): String =
        """
        {
          "id": "$id",
          "name": "$name",
          "command": "$command",
          "created_at": "2026-07-28T12:00:00Z",
          "updated_at": "2026-07-28T12:00:00Z"
        }
        """.trimIndent()

    @Test
    fun connected_event_alsoTriggersRefreshTypes() = runTest {
        // On WS reconnect the bus must reconcile custom agent types as well —
        // otherwise custom_type.* events that fired while offline would be lost.
        val (bus, connEvents, _, _, _, _, _, customTypes) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(connEvents, WsEvent.Connected)

        coVerify(timeout = 2000, exactly = 1) { customTypes.refreshTypes() }

        bus.stop()
    }

    @Test
    fun message_customTypeCreated_callsApplyUpserted_withParsedDto() = runTest {
        val (bus, connEvents, _, _, _, _, _, customTypes) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"custom_type.created","payload":${customTypeJson(id = "ab12cd34ef56", name = "Aider")}}""",
            ),
        )

        verify(timeout = 2000, exactly = 1) {
            customTypes.applyUpserted(
                match {
                    it.id == "ab12cd34ef56" &&
                        it.name == "Aider" &&
                        it.command == "aider --model sonnet"
                },
            )
        }
        verify(exactly = 1) { customTypes.applyUpserted(any()) }

        bus.stop()
    }

    @Test
    fun message_customTypeUpdated_callsApplyUpserted_withParsedDto() = runTest {
        val (bus, connEvents, _, _, _, _, _, customTypes) = newBus()
        bus.start()
        awaitSubscribed(connEvents)

        emit(
            connEvents,
            WsEvent.Message(
                """{"type":"custom_type.updated","payload":${customTypeJson(id = "ab12cd34ef56", name = "Renamed")}}""",
            ),
        )

        verify(timeout = 2000, exactly = 1) {
            customTypes.applyUpserted(
                match { it.id == "ab12cd34ef56" && it.name == "Renamed" },
            )
        }

        bus.stop()
    }

    @Test
    fun message_customTypeDeleted_appliesDeleted_andPurgesShortcutKind() = runTest {
        // The server cascades the type's saved shortcuts in the same
        // transaction WITHOUT per-shortcut shortcut.deleted events — the bus
        // must mirror that locally via purgeKind on the full wire kind, and
        // must NOT route through the per-shortcut applyDeleted path.
        val fx = newBus()
        fx.bus.start()
        awaitSubscribed(fx.connEvents)

        emit(
            fx.connEvents,
            WsEvent.Message(
                """{"type":"custom_type.deleted","payload":{"id":"ab12cd34ef56"}}""",
            ),
        )

        verify(timeout = 2000, exactly = 1) {
            fx.customAgentTypesRepository.applyDeleted("ab12cd34ef56")
        }
        verify(timeout = 2000, exactly = 1) {
            fx.shortcutsRepository.purgeKind("custom_ab12cd34ef56")
        }
        verify(exactly = 0) { fx.shortcutsRepository.applyDeleted(any()) }

        fx.bus.stop()
    }

    @Test
    fun message_customTypeCreatedMalformedPayload_isSilentlySkipped() = runTest {
        // Payload missing the required `command` field — the inner decode must
        // fail and the bus must remain alive for the next message.
        val fx = newBus()
        fx.bus.start()
        awaitSubscribed(fx.connEvents)

        emit(
            fx.connEvents,
            WsEvent.Message(
                """{"type":"custom_type.created","payload":{"id":"t-1","name":"x"}}""",
            ),
        )

        // Synchronize on a valid follow-up, then assert nothing fired.
        emit(
            fx.connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) {
            fx.agentRepository.updateAgentOnline("sync", true)
        }
        verify(exactly = 0) { fx.customAgentTypesRepository.applyUpserted(any()) }
        verify(exactly = 0) { fx.customAgentTypesRepository.applyDeleted(any()) }

        fx.bus.stop()
    }

    @Test
    fun message_customTypeDeletedMissingId_isSilentlySkipped() = runTest {
        // custom_type.deleted without the required `id` field — the payload
        // decode must throw, be swallowed, and neither the type delete nor
        // the shortcut cascade may run on a guessed id.
        val fx = newBus()
        fx.bus.start()
        awaitSubscribed(fx.connEvents)

        emit(
            fx.connEvents,
            WsEvent.Message("""{"type":"custom_type.deleted","payload":{}}"""),
        )

        emit(
            fx.connEvents,
            WsEvent.Message(
                """{"type":"agent.status_changed","payload":{"agent_id":"sync","online":true}}""",
            ),
        )
        verify(timeout = 2000, exactly = 1) {
            fx.agentRepository.updateAgentOnline("sync", true)
        }
        verify(exactly = 0) { fx.customAgentTypesRepository.applyDeleted(any()) }
        verify(exactly = 0) { fx.shortcutsRepository.purgeKind(any()) }

        fx.bus.stop()
    }

    // ---- Auth lifecycle: logout clears the account-scoped caches ------------

    @Test
    fun unauthenticated_transition_clearsAccountScopedCaches() = runTest {
        // Every logout/account-switch path funnels through
        // AuthState.Unauthenticated — the single seam where the previous
        // account's templates/shortcuts/custom-type caches must be dropped.
        val fx = newBus(initiallyLoggedIn = true)

        // The bus's auth collector applies drop(1) to skip the initial state.
        // Flipping the flow before that collector subscribes would make
        // Unauthenticated itself the dropped first value — wait for the
        // subscription on wall time, mirroring awaitSubscribed.
        runBlocking {
            val deadline = System.currentTimeMillis() + 2000
            while (fx.authStateFlow.subscriptionCount.value == 0 &&
                System.currentTimeMillis() < deadline
            ) {
                kotlinx.coroutines.delay(10)
            }
            check(fx.authStateFlow.subscriptionCount.value > 0) {
                "UserEventBus did not subscribe to authState within 2s"
            }
        }

        fx.authStateFlow.value = SessionManager.AuthState.Unauthenticated

        verify(timeout = 2000, exactly = 1) { fx.templatesRepository.clear() }
        verify(timeout = 2000, exactly = 1) { fx.shortcutsRepository.clear() }
        verify(timeout = 2000, exactly = 1) { fx.customAgentTypesRepository.clear() }
    }
}
