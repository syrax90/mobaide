package com.mobaide.app.ui.chat

import com.mobaide.app.data.chat.ChatItem
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * [chatTimelineRows] in focus view (web `focusView.test.ts`, iOS
 * `ChatFocusViewTests`): the work between a question and its answer folds into
 * one steps row per run; questions, answers, waiting tool calls and status
 * lines stay in place.
 */
class ChatFocusViewTest {

    private fun user(id: String) = ChatItem.User(id, "Fix the login bug", emptyList())

    private fun agent(id: String) = ChatItem.Agent(id, "Done")

    private fun thought(id: String) = ChatItem.Thought(id, "Reading the code")

    private fun tool(id: String, title: String = "Run tests") = ChatItem.Tool(
        id = id,
        title = title,
        kind = "execute",
        status = "in_progress",
        subtitle = null,
        locations = emptyList(),
        content = emptyList(),
        input = null,
    )

    private fun notice(id: String) = ChatItem.Notice(id, "info", "Loaded", "")

    private fun compaction(id: String) = ChatItem.Compaction(id, "completed", "")

    private fun error(id: String) = ChatItem.Error(id, "The agent exited")

    private fun turnEnd(id: String) = ChatItem.TurnEnd(id, "end_turn", "")

    private fun rows(
        items: List<ChatItem>,
        focus: Boolean = true,
        running: Boolean = false,
        waiting: Set<String> = emptySet(),
        open: Set<String> = emptySet(),
    ) = chatTimelineRows(items, focus, running, waiting, open)

    /** Item rows by id (nested ones indented), steps rows as `<key> ×<count>`. */
    private fun outline(rows: List<ChatTimelineRow>): List<String> = rows.map { row ->
        when (row) {
            is ChatTimelineRow.Steps -> "${row.key} ×${row.count}"
            is ChatTimelineRow.Item -> if (row.nested) "  ${row.item.id}" else row.item.id
        }
    }

    private fun steps(rows: List<ChatTimelineRow>) = rows.filterIsInstance<ChatTimelineRow.Steps>()

    @Test
    fun focusOff_showsEveryItemInPlace() {
        val items = listOf(user("u:1"), thought("t:2"), tool("tool-3"), agent("m:4"))

        assertEquals(
            items.map { ChatTimelineRow.Item(it, nested = false) },
            rows(items, focus = false, running = true),
        )
    }

    @Test
    fun theWorkOfATurn_foldsIntoOneStepsRow_betweenTheQuestionAndTheAnswer() {
        val result = rows(listOf(user("u:1"), thought("t:2"), agent("m:3"), tool("tool-4"), agent("m:5"), turnEnd("end:6")))

        assertEquals(listOf("u:1", "steps:t:2 ×3", "m:5", "end:6"), outline(result))
    }

    @Test
    fun theAnswerOfATurn_staysInPlace_whenTheNextTurnDoesMoreWork() {
        val result = rows(listOf(user("u:1"), tool("tool-2"), agent("m:3"), user("u:4"), tool("tool-5"), agent("m:6")))

        assertEquals(listOf("u:1", "steps:tool-2 ×1", "m:3", "u:4", "steps:tool-5 ×1", "m:6"), outline(result))
    }

    @Test
    fun aToolWaitingForApproval_staysInPlace_andSplitsTheRun() {
        val result = rows(listOf(user("u:1"), thought("t:2"), tool("tool-3"), tool("tool-4")), waiting = setOf("tool-3"))

        assertEquals(listOf("u:1", "steps:t:2 ×1", "tool-3", "steps:tool-4 ×1"), outline(result))
    }

    @Test
    fun noticesCompactionAndErrors_stayInPlaceBetweenRuns() {
        val result = rows(
            listOf(user("u:1"), thought("t:2"), notice("n:3"), tool("tool-4"), compaction("cmp-5"), error("err:6")),
        )

        assertEquals(listOf("u:1", "steps:t:2 ×1", "n:3", "steps:tool-4 ×1", "cmp-5", "err:6"), outline(result))
    }

    @Test
    fun aNoticeEndsATurnLikeAUserMessage_soTheAnswerBeforeItStaysInPlace() {
        val fromAnotherApp = rows(listOf(user("u:1"), agent("m:2"), notice("n:3"), tool("call_1"), agent("m:4")))
        assertEquals(listOf("u:1", "m:2", "n:3", "steps:call_1 ×1", "m:4"), outline(fromAnotherApp))

        val midRun = rows(listOf(user("u:1"), thought("t:2"), agent("m:3"), notice("n:4"), tool("tool-5"), agent("m:6")))
        assertEquals(listOf("u:1", "steps:t:2 ×1", "m:3", "n:4", "steps:tool-5 ×1", "m:6"), outline(midRun))
    }

    @Test
    fun theRunThatEndsARunningTurn_namesItsNewestStep() {
        val items = listOf(user("u:1"), thought("t:2"), agent("m:3"), tool("tool-4"), thought("t:5"), tool("tool-6", "pytest -q"))

        assertEquals(
            ChatTimelineRow.Steps(key = stepsRowKey("t:2"), count = 5, live = true, current = "pytest -q", open = false),
            steps(rows(items, running = true)).single(),
        )
        assertEquals("Thinking", steps(rows(items.take(5), running = true)).single().current)
        assertEquals("Tool call", steps(rows(listOf(user("u:1"), tool("tool-2", "")), running = true)).single().current)
    }

    @Test
    fun noRunIsLive_whenTheTurnIsNotRunning_orTheRunIsNotTheNewestThing() {
        val working = listOf(user("u:1"), thought("t:2"), tool("tool-3"))

        val idle = steps(rows(working)).single()
        assertFalse(idle.live)
        assertNull(idle.current)

        val answered = steps(rows(working + agent("m:4"), running = true)).single()
        assertFalse(answered.live)
        assertNull(answered.current)

        val twoRuns = steps(rows(listOf(user("u:1"), thought("t:2"), notice("n:3"), tool("tool-4")), running = true))
        assertEquals(listOf(false, true), twoRuns.map { it.live })
    }

    @Test
    fun anOpenStepsRow_isFollowedByItsStepsNested() {
        val items = listOf(
            user("u:1"), thought("t:2"), agent("m:3"), tool("tool-4"), agent("m:5"),
            user("u:6"), tool("tool-7"), agent("m:8"),
        )

        val result = rows(items, open = setOf(stepsRowKey("t:2")))

        assertEquals(
            listOf("u:1", "steps:t:2 ×3", "  t:2", "  m:3", "  tool-4", "m:5", "u:6", "steps:tool-7 ×1", "m:8"),
            outline(result),
        )
        assertEquals(listOf(true, false), steps(result).map { it.open })
    }

    @Test
    fun stepsLabel_countsOneAndMany() {
        assertEquals("1 step", stepsLabel(1))
        assertEquals("4 steps", stepsLabel(4))
    }
}
