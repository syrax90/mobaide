package com.mobaide.app.data.chat

import com.mobaide.app.testing.ProtocolFixtures
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.add
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.putJsonArray
import okio.Buffer
import okio.ByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android arm of `tests/protocol_fixtures/frames/chat_frames.json`: every
 * golden command rebuilt through [ChatProtocol]'s builders, and every golden
 * agent frame decoded by [ChatProtocol.decode] and applied with
 * [ChatTimelineReducer], as the web and iOS clients do with the same file.
 */
class ChatFramesFixtureTest {

    @Serializable
    private data class CommandCase(val name: String, val command: JsonObject)

    @Serializable
    private data class ServerCase(val name: String, val frame: JsonObject)

    @Serializable
    private data class Fixture(val commands: List<CommandCase>, val server: List<ServerCase>)

    private val fixture: Fixture = ProtocolFixtures.load("frames/chat_frames.json")

    private val epoch = "9f3a1c2b7d4e"

    private val builders: Map<String, (String) -> JsonObject> = mapOf(
        "sync_fresh" to { id -> sync(id, ChatTimelineReducer().onOpen()) },
        "sync_resume" to { id -> sync(id, loaded().onOpen()) },
        "prompt_text" to { id -> ChatProtocol.prompt(id, "Fix the failing test", emptyList()) },
        "prompt_with_image" to { id ->
            ChatProtocol.prompt(id, "What is on this screen?", listOf(ChatImagePayload("image/png", "iVBORw0KGgo=")))
        },
        "cancel" to { id -> ChatProtocol.cancel(id) },
        "set_config_select" to { id -> ChatProtocol.setConfig(id, "model", JsonPrimitive("sonnet")) },
        "set_config_boolean" to { id -> ChatProtocol.setConfig(id, "fast", JsonPrimitive(true)) },
        "respond_permission" to { id -> ChatProtocol.respondPermission(id, "r:9", "allow") },
        "respond_form_accept" to { id ->
            ChatProtocol.respondForm(
                id,
                "r:12",
                "accept",
                buildJsonObject {
                    put("framework", "vue")
                    putJsonArray("extras") { add("lint") }
                    put("strict", false)
                },
            )
        },
        "respond_form_decline" to { id -> ChatProtocol.respondForm(id, "r:12", "decline", null) },
        "dequeue" to { id -> ChatProtocol.dequeue(id, "q:14") },
        "restart" to { id -> ChatProtocol.restart(id) },
        "steer" to { id -> ChatProtocol.steer(id, "q:14") },
    )

    private fun sync(id: String, request: ChatSyncRequest): JsonObject = when (request) {
        ChatSyncRequest.Snapshot -> ChatProtocol.sync(id, null, null)
        is ChatSyncRequest.Resume -> ChatProtocol.sync(id, request.epoch, request.afterSeq)
    }

    private fun agentBytes(frame: JsonObject): ByteString =
        Buffer().writeByte(ChatProtocol.TYPE_FRAME.toInt()).writeUtf8(frame.toString()).readByteString()

    private fun server(name: String): ChatFrame {
        val case = fixture.server.firstOrNull { it.name == name } ?: error("no server case $name")
        return ChatProtocol.decode(agentBytes(case.frame)) ?: error("$name did not decode")
    }

    private fun snapshot(): ChatFrame.Snapshot = server("snapshot_full") as ChatFrame.Snapshot

    private fun batch(): ChatFrame.Events = server("events_batch") as ChatFrame.Events

    private fun loaded(): ChatTimelineReducer = ChatTimelineReducer().apply { apply(snapshot()) }

    @Test
    fun everyGoldenCommand_hasABuilder() {
        assertEquals(builders.keys.sorted(), fixture.commands.map { it.name }.sorted())
    }

    @Test
    fun everyGoldenCommand_isEncodedByTheBuilders() {
        for (case in fixture.commands) {
            val id = (case.command["id"] as JsonPrimitive).content
            val build = builders[case.name] ?: error("no builder for ${case.name}")
            val bytes = ChatProtocol.encode(build(id))

            assertEquals(case.name, ChatProtocol.TYPE_COMMAND, bytes[0])
            assertEquals(case.name, case.command, Json.parseToJsonElement(bytes.substring(1).utf8()))
        }
    }

    @Test
    fun everyServerFrame_decodes() {
        for (case in fixture.server) {
            assertNotNull(case.name, ChatProtocol.decode(agentBytes(case.frame)))
        }
    }

    @Test
    fun snapshot_decodesItemsAndState() {
        val frame = snapshot()
        assertEquals(epoch, frame.epoch)
        assertEquals(41L, frame.seq)
        assertFalse(frame.truncated)
        assertEquals(
            listOf(
                ChatItem.User::class,
                ChatItem.Thought::class,
                ChatItem.Agent::class,
                ChatItem.Tool::class,
                ChatItem.Tool::class,
                ChatItem.Notice::class,
                ChatItem.Compaction::class,
                ChatItem.TurnEnd::class,
                ChatItem.Error::class,
            ),
            frame.items.map { it::class },
        )
        assertEquals(listOf(ChatAttachment(isImage = true, label = "Image")), (frame.items[0] as ChatItem.User).attachments)
        val edit = frame.items[3] as ChatItem.Tool
        assertEquals("edit", edit.kind)
        assertEquals(listOf(ChatLocation("/home/user/proj/login.py", 12)), edit.locations)
        val diff = edit.content.single() as ChatContentPart.Diff
        assertEquals(1 to 1, diff.added to diff.removed)
        assertEquals(ChatItem.TurnEnd("end:6", "cancelled", "Stopped"), frame.items[7])

        val state = loaded().timeline!!.state
        assertEquals(ChatStatus.RUNNING, state.status)
        assertEquals("Claude Agent", state.agent?.title)
        assertEquals(
            listOf(Triple("mode", false, "default"), Triple("model", false, "opus"), Triple("fast", true, null)),
            state.config.map { Triple(it.id, it.isBoolean, it.selectValue) },
        )
        assertFalse(state.config.last().boolValue)
        assertEquals(listOf("review", "compact"), state.commands.map { it.name })
        assertEquals(ChatUsage(used = 53_000, size = 200_000, cost = ChatCost(0.045, "USD")), state.usage)
        assertEquals(listOf("completed", "in_progress", "pending"), state.plan.map { it.status })
        val permission = state.requests[0] as ChatRequest.Permission
        assertEquals("toolu_02", permission.toolCallId)
        assertEquals(listOf("allow", "always", "reject"), permission.options.map { it.id })
        val form = state.requests[1] as ChatRequest.Form
        assertNull(form.toolCallId)
        assertEquals(
            listOf(
                ChatFieldType.Select,
                ChatFieldType.MultiSelect,
                ChatFieldType.Text,
                ChatFieldType.Boolean,
                ChatFieldType.Integer,
            ),
            form.fields.map { it.type },
        )
        assertEquals(listOf(ChatQueuedMessage("q:14", "Also update the README", 0)), state.queue)
        assertTrue(state.promptImages)
        assertTrue(state.steering)
    }

    @Test
    fun eventsBatch_appliesOnTopOfTheSnapshot() {
        val reducer = loaded()

        val reduction = reducer.apply(batch())

        assertNull(reduction.sync)
        assertTrue(reduction.stateChanged)
        val timeline = reducer.timeline!!
        assertEquals(45L, timeline.seq)
        assertEquals(snapshot().items.size + 2, timeline.items.size)
        assertEquals(ChatItem.Agent("m:8", "Done: the tests pass."), timeline.items.single { it.id == "m:8" })
        assertEquals(ToolStatus.IN_PROGRESS, (timeline.items.last() as ChatItem.Tool).status)
        assertEquals(ChatStatus.READY, timeline.state.status)
        assertNull(timeline.state.turnStartedAt)
        assertEquals(emptyList<ChatRequest>(), timeline.state.requests)
        assertEquals(1, timeline.state.queue.size)

        val ack = reducer.apply(server("events_empty_ack") as ChatFrame.Events)

        assertEquals(ChatReduction.NONE, ack)
        assertSame(timeline, reducer.timeline)
    }

    @Test
    fun syncRules_onTheGoldenFrames() {
        assertEquals(ChatSyncRequest.Resume(epoch, 41), loaded().onOpen())

        val replayed = loaded().apply { apply(batch()) }
        assertEquals(ChatReduction.NONE, replayed.apply(batch()))
        assertEquals(45L, replayed.timeline!!.seq)

        val gapped = loaded()
        val gap = gapped.apply(batch().copy(events = batch().events.drop(1)))
        assertEquals(ChatSyncRequest.Resume(epoch, 41), gap.sync)
        assertEquals(41L, gapped.timeline!!.seq)

        val otherEpoch = loaded()
        assertEquals(ChatSyncRequest.Snapshot, otherEpoch.apply(batch().copy(epoch = "other")).sync)
        assertEquals(snapshot().items, otherEpoch.timeline!!.items)
    }

    @Test
    fun results_decode() {
        assertEquals(
            ChatFrame.Result(
                id = "c-3",
                ok = true,
                data = buildJsonObject {
                    put("queued", true)
                    put("queue_id", "q:14")
                },
                error = null,
            ),
            server("result_ok"),
        )
        assertEquals(
            ChatFrame.Result(id = "c-8", ok = false, data = null, error = "This request was already answered"),
            server("result_error"),
        )
    }

    @Test
    fun ptyReplayFramesForOlderClients_areNotChatFrames() {
        for (type in listOf('b', 'o', 'e')) {
            assertNull(ChatProtocol.decode(Buffer().writeByte(type.code).writeUtf8("A").readByteString()))
        }
    }
}
