package com.mobaide.app.data.chat

import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [ChatTimelineReducer] — snapshot / events application and the
 * sync rules: duplicates by seq, one sync in flight, a resend when its answer is
 * lost, another epoch, and the client item cap, which drops old steps (tool
 * calls, thoughts) before messages.
 */
class ChatTimelineReducerTest {

    private val epoch = "e1"

    private fun agent(id: String, text: String = "") = ChatItem.Agent(id, text)

    private fun user(id: String, text: String = "") = ChatItem.User(id, text, emptyList())

    private fun thought(id: String, text: String = "") = ChatItem.Thought(id, text)

    private fun tool(id: String, status: String = "pending") = ChatItem.Tool(
        id = id,
        title = "Run tests",
        kind = "execute",
        status = status,
        subtitle = null,
        locations = emptyList(),
        content = emptyList(),
        input = null,
    )

    private fun state(vararg pairs: Pair<String, Any>): JsonObject = JsonObject(
        pairs.associate { (key, value) ->
            key to when (value) {
                is Boolean -> JsonPrimitive(value)
                is Number -> JsonPrimitive(value)
                else -> JsonPrimitive(value.toString())
            }
        },
    )

    private fun snapshot(seq: Long, items: List<ChatItem>, epoch: String = this.epoch) =
        ChatFrame.Snapshot(epoch = epoch, seq = seq, truncated = false, state = state("status" to "ready"), items = items)

    private fun events(vararg events: ChatEvent, epoch: String = this.epoch) =
        ChatFrame.Events(epoch = epoch, seq = events.lastOrNull()?.seq ?: 0, events = events.toList())

    private fun loaded(seq: Long, items: List<ChatItem>, maxItems: Int = ChatTimelineReducer.MAX_ITEMS) =
        ChatTimelineReducer(maxItems = maxItems).apply {
            onOpen()
            apply(snapshot(seq, items))
        }

    private fun ChatTimelineReducer.textOf(id: String): String? =
        when (val item = timeline?.items?.firstOrNull { it.id == id }) {
            is ChatItem.Agent -> item.text
            else -> null
        }

    @Test
    fun onOpen_withoutATimeline_asksForASnapshot() {
        assertEquals(ChatSyncRequest.Snapshot, ChatTimelineReducer().onOpen())
    }

    @Test
    fun onOpen_withACachedTimeline_resumesFromIt() {
        val cached = ChatTimeline(
            epoch = epoch,
            seq = 7,
            rawState = JsonObject(emptyMap()),
            state = ChatState(),
            items = listOf(agent("m:1", "a")),
            truncated = false,
        )
        val reducer = ChatTimelineReducer(cached)

        assertEquals(ChatSyncRequest.Resume(epoch, 7), reducer.onOpen())
        reducer.apply(events(ChatEvent.Append(8, "m:1", "b")))
        assertEquals("ab", reducer.textOf("m:1"))
    }

    @Test
    fun snapshot_replacesItemsAndState() {
        val reducer = loaded(3, listOf(agent("m:1", "old")))

        val reduction = reducer.apply(snapshot(9, listOf(agent("m:2", "new")), epoch = "e2"))

        val timeline = reducer.timeline!!
        assertTrue(reduction.changed && reduction.stateChanged)
        assertEquals("e2", timeline.epoch)
        assertEquals(9L, timeline.seq)
        assertEquals(listOf("m:2"), timeline.items.map { it.id })
        assertEquals("ready", timeline.state.status)
    }

    @Test
    fun events_upsertAppendAndPatchState() {
        val reducer = loaded(2, listOf(agent("m:1", "Hel")))

        val reduction = reducer.apply(
            events(
                ChatEvent.Append(3, "m:1", "lo"),
                ChatEvent.Upsert(4, tool("call-1")),
                ChatEvent.Upsert(5, tool("call-1", status = "completed")),
                ChatEvent.StatePatch(6, state("status" to "running", "cancelling" to true)),
            ),
        )

        val timeline = reducer.timeline!!
        assertNull(reduction.sync)
        assertTrue(reduction.changed && reduction.stateChanged)
        assertEquals(6L, timeline.seq)
        assertEquals("Hello", reducer.textOf("m:1"))
        assertEquals(listOf("m:1", "call-1"), timeline.items.map { it.id })
        assertEquals("completed", (timeline.items[1] as ChatItem.Tool).status)
        assertEquals("running", timeline.state.status)
        assertTrue(timeline.state.cancelling)
    }

    @Test
    fun events_duplicatesAreSkipped() {
        val reducer = loaded(2, listOf(agent("m:1", "a")))
        val frame = events(ChatEvent.Append(3, "m:1", "b"))

        reducer.apply(frame)
        val again = reducer.apply(frame)

        assertFalse(again.changed)
        assertNull(again.sync)
        assertEquals("ab", reducer.textOf("m:1"))
    }

    @Test
    fun events_appendToAToolAndUnknownItemsOnlyAdvanceTheSeq() {
        val reducer = loaded(1, listOf(tool("call-1")))

        val reduction = reducer.apply(
            events(
                ChatEvent.Append(2, "call-1", "x"),
                ChatEvent.Append(3, "missing", "x"),
                ChatEvent.Upsert(4, null),
                ChatEvent.Unknown(5),
            ),
        )

        assertFalse(reduction.changed)
        assertEquals(5L, reducer.timeline!!.seq)
        assertEquals(listOf(tool("call-1")), reducer.timeline!!.items)
    }

    @Test
    fun remove_dropsTheItemAndKeepsLaterEventsOfTheFrameOnTheRightItems() {
        val reducer = loaded(1, listOf(agent("n:1", "Setting up"), agent("m:2", "a")))

        val reduction = reducer.apply(
            events(
                ChatEvent.Upsert(2, agent("n:3", "brief")),
                ChatEvent.Remove(3, "n:1"),
                ChatEvent.Remove(4, "n:3"),
                ChatEvent.Append(5, "m:2", "b"),
                ChatEvent.Upsert(6, agent("m:4", "c")),
                ChatEvent.Append(7, "m:4", "d"),
            ),
        )

        assertTrue(reduction.changed)
        assertNull(reduction.sync)
        assertEquals(7L, reducer.timeline!!.seq)
        assertEquals(listOf(agent("m:2", "ab"), agent("m:4", "cd")), reducer.timeline!!.items)
    }

    @Test
    fun remove_ofAnUnknownItemOnlyAdvancesTheSeq() {
        val reducer = loaded(1, listOf(agent("m:1", "a")))

        val reduction = reducer.apply(events(ChatEvent.Remove(2, "missing")))

        assertFalse(reduction.changed)
        assertEquals(2L, reducer.timeline!!.seq)
        assertEquals(listOf(agent("m:1", "a")), reducer.timeline!!.items)
    }

    @Test
    fun gap_appliesWhatCameBeforeAndAsksForTheRestOnce() {
        val reducer = loaded(1, listOf(agent("m:1", "a")))

        val gap = reducer.apply(events(ChatEvent.Append(2, "m:1", "b"), ChatEvent.Append(4, "m:1", "d")))
        assertEquals(ChatSyncRequest.Resume(epoch, 2), gap.sync)
        assertEquals("ab", reducer.textOf("m:1"))

        val second = reducer.apply(events(ChatEvent.Append(5, "m:1", "e")))
        assertNull(second.sync)
        assertEquals(2L, reducer.timeline!!.seq)
    }

    @Test
    fun gap_resendsTheSyncWhenItsAnswerNeverCame() {
        val reducer = loaded(1, emptyList())
        assertEquals(ChatSyncRequest.Resume(epoch, 1), reducer.apply(events(ChatEvent.Unknown(3))).sync)

        val unanswered = (1..11).map { reducer.apply(events(ChatEvent.Unknown(3L + it))).sync }
        assertTrue(unanswered.all { it == null })

        assertEquals(ChatSyncRequest.Resume(epoch, 1), reducer.apply(events(ChatEvent.Unknown(20))).sync)
    }

    @Test
    fun answeredResume_letsTheNextGapAskAgain() {
        val reducer = loaded(1, emptyList())
        reducer.apply(events(ChatEvent.Unknown(3)))

        reducer.apply(events(ChatEvent.Unknown(2), ChatEvent.Unknown(3)))
        val next = reducer.apply(events(ChatEvent.Unknown(5)))

        assertEquals(ChatSyncRequest.Resume(epoch, 3), next.sync)
    }

    @Test
    fun otherEpoch_asksForASnapshotAndWaitsForIt() {
        val reducer = loaded(4, listOf(agent("m:1")))

        val first = reducer.apply(events(ChatEvent.Unknown(1), epoch = "e2"))
        val second = reducer.apply(events(ChatEvent.Unknown(5)))

        assertEquals(ChatSyncRequest.Snapshot, first.sync)
        assertNull(second.sync)
        assertFalse(second.changed)
        assertEquals(4L, reducer.timeline!!.seq)
    }

    @Test
    fun onOpen_afterAGap_resumesFromTheAppliedSeq() {
        val reducer = loaded(1, emptyList())
        reducer.apply(events(ChatEvent.Unknown(2), ChatEvent.Unknown(9)))
        reducer.onClose()

        assertEquals(ChatSyncRequest.Resume(epoch, 2), reducer.onOpen())
    }

    @Test
    fun cap_dropsTheOldestItemsAndKeepsTheIndexRight() {
        val reducer = loaded(1, listOf(agent("m:1", "1"), agent("m:2", "2"), agent("m:3", "3")), maxItems = 3)

        reducer.apply(events(ChatEvent.Upsert(2, agent("m:4", "4")), ChatEvent.Append(3, "m:3", "!")))
        reducer.apply(events(ChatEvent.Append(4, "m:4", "?")))

        val timeline = reducer.timeline!!
        assertEquals(listOf("m:2", "m:3", "m:4"), timeline.items.map { it.id })
        assertEquals("3!", reducer.textOf("m:3"))
        assertEquals("4?", reducer.textOf("m:4"))
        assertTrue(timeline.truncated)
    }

    @Test
    fun cap_dropsFinishedStepsAfterTheLatestUserMessageBeforeOlderMessages() {
        val reducer = loaded(
            1,
            listOf(user("u:0"), agent("m:1"), user("u:2"), tool("call-3", "completed"), thought("h:4"), agent("m:5")),
            maxItems = 4,
        )

        reducer.apply(events(ChatEvent.Append(2, "m:5", "!")))

        val timeline = reducer.timeline!!
        assertEquals(listOf("u:0", "m:1", "u:2", "m:5"), timeline.items.map { it.id })
        assertEquals("!", reducer.textOf("m:5"))
        assertFalse(timeline.truncated)
    }

    @Test
    fun cap_keepsARunningToolInPlaceWhileOlderMessagesGo() {
        val reducer = loaded(1, listOf(user("u:0"), agent("m:1"), tool("call-2", "in_progress")), maxItems = 3)

        reducer.apply(events(ChatEvent.Upsert(2, agent("m:3", "a"))))
        reducer.apply(events(ChatEvent.Upsert(3, tool("call-2", "completed"))))

        val timeline = reducer.timeline!!
        assertEquals(listOf("m:1", "call-2", "m:3"), timeline.items.map { it.id })
        assertEquals("completed", (timeline.items[1] as ChatItem.Tool).status)
        assertTrue(timeline.truncated)
    }

    @Test
    fun cap_dropsFromTheFrontOnceTheFinishedStepsAreGone() {
        val reducer = loaded(
            1,
            listOf(tool("call-0", "pending"), user("u:1"), thought("h:2"), agent("m:3")),
            maxItems = 1,
        )

        val timeline = reducer.timeline!!
        assertEquals(listOf("m:3"), timeline.items.map { it.id })
        assertTrue(timeline.truncated)
    }

    @Test
    fun cap_droppingOnlyStepsFromTheFrontLeavesTruncatedOff() {
        val reducer = loaded(1, listOf(tool("call-0", "in_progress"), thought("h:1"), agent("m:2")), maxItems = 1)

        val timeline = reducer.timeline!!
        assertEquals(listOf("m:2"), timeline.items.map { it.id })
        assertFalse(timeline.truncated)
    }

    @Test
    fun cap_keepsTheNewestItemEvenWhenItIsAFinishedStep() {
        val reducer = loaded(1, listOf(agent("m:0"), thought("h:1")), maxItems = 1)

        val timeline = reducer.timeline!!
        assertEquals(listOf("h:1"), timeline.items.map { it.id })
        assertTrue(timeline.truncated)
    }
}
