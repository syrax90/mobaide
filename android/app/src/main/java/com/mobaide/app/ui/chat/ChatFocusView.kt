package com.mobaide.app.ui.chat

import androidx.compose.runtime.Immutable
import com.mobaide.app.data.chat.ChatItem
import com.mobaide.app.data.chat.SubagentState

/**
 * One row of the chat timeline. In focus view the work between answers folds
 * into a [Steps] row; its items follow it as nested [Item]s while it is open.
 */
@Immutable
sealed interface ChatTimelineRow {
    val key: String

    data class Item(val item: ChatItem, val nested: Boolean) : ChatTimelineRow {
        override val key: String get() = item.id
    }

    data class Steps(
        override val key: String,
        val count: Int,
        /** The run is the newest thing in a running turn. */
        val live: Boolean,
        /** What the newest step does ("Thinking", a tool title); null unless live. */
        val current: String?,
        val open: Boolean,
    ) : ChatTimelineRow

    /** Two or more subagent cards behind one collapsible head. */
    data class Subagents(
        override val key: String,
        val cards: List<ChatItem.Subagent>,
        val open: Boolean,
    ) : ChatTimelineRow
}

fun stepsRowKey(firstItemId: String): String = "steps:$firstItemId"

fun stepsLabel(count: Int): String = if (count == 1) "1 step" else "$count steps"

fun subagentGroupKey(firstCardId: String): String = "subagents:$firstCardId"

/** The key that marks a subagent card open, in the same set as the steps rows and groups. */
fun subagentCardKey(cardId: String): String = "subagent:$cardId"

fun subagentsLabel(count: Int): String = if (count == 1) "1 subagent" else "$count subagents"

/** Sibling subagent cards as shown: a group of the calm ones, or one card. */
sealed interface SubagentEntry {
    data class Card(val card: ChatItem.Subagent) : SubagentEntry
    data class Group(val key: String, val cards: List<ChatItem.Subagent>) : SubagentEntry
}

/** The card, a tool call it waits on, or a card nested in it waits for the user. */
fun subagentNeedsYou(card: ChatItem.Subagent, tree: ChatSubagentTree, waitingToolIds: Set<String>): Boolean =
    card.state == SubagentState.REQUIRES_ACTION ||
        tree.tools[card.subagentId].orEmpty().any { it.id in waitingToolIds } ||
        tree.children[card.subagentId].orEmpty().any { subagentNeedsYou(it, tree, waitingToolIds) }

/** Where a card sits in its group: the ones still working first, then failed, stopped, done. */
private fun subagentRank(card: ChatItem.Subagent): Int = when (card.state) {
    SubagentState.REQUIRES_ACTION -> 0
    SubagentState.RUNNING -> 1
    SubagentState.IDLE -> when (card.stopReason) {
        "error" -> 3
        "cancelled" -> 4
        else -> 5
    }
    else -> 2
}

/**
 * Two or more calm cards form one group, keyed by the first card of all; the
 * cards that need the user follow it on their own, so a request is never
 * hidden. Inside the group the cards still working come first and the
 * finished ones last, each in launch order. With fewer than two calm cards
 * every card shows on its own, in order.
 */
fun subagentEntries(
    cards: List<ChatItem.Subagent>,
    tree: ChatSubagentTree,
    waitingToolIds: Set<String>,
): List<SubagentEntry> {
    val (urgent, calm) = cards.partition { subagentNeedsYou(it, tree, waitingToolIds) }
    if (calm.size < 2) return cards.map { SubagentEntry.Card(it) }
    val ordered = calm.sortedBy(::subagentRank)
    return listOf(SubagentEntry.Group(subagentGroupKey(cards.first().id), ordered)) + urgent.map { SubagentEntry.Card(it) }
}

/** "2 running · 5 done · 1 failed": the states of a group's cards, busiest first. */
fun subagentGroupSummary(cards: List<ChatItem.Subagent>): String {
    var needs = 0
    var running = 0
    var done = 0
    var failed = 0
    var stopped = 0
    for (card in cards) {
        when (card.state) {
            SubagentState.REQUIRES_ACTION -> needs += 1
            SubagentState.RUNNING -> running += 1
            SubagentState.IDLE -> when (card.stopReason) {
                "cancelled" -> stopped += 1
                "error" -> failed += 1
                else -> done += 1
            }
        }
    }
    return listOfNotNull(
        "$needs need you".takeIf { needs > 0 },
        "$running running".takeIf { running > 0 },
        "$done done".takeIf { done > 0 },
        "$failed failed".takeIf { failed > 0 },
        "$stopped stopped".takeIf { stopped > 0 },
    ).joinToString(" · ")
}

private val RESULT_LINE_MARKS = Regex("""^\s*(#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)""")
private val RESULT_INLINE_MARKS = Regex("""\*\*|__|`""")
private val RESULT_RULE = Regex("""^(-{3,}|\*{3,}|_{3,})$""")

/** The first line of a result as plain text, for a collapsed card; null when it is blank. */
fun subagentResultPreview(result: String?): String? = result?.lineSequence()
    ?.map { it.replace(RESULT_LINE_MARKS, "").replace(RESULT_INLINE_MARKS, "").trim() }
    ?.firstOrNull { it.isNotEmpty() && !RESULT_RULE.matches(it) }

/**
 * Which items a subagent card shows instead of the main flow: the tool calls
 * that name a present card, and the cards whose parent card is present.
 * [tools] and [children] are keyed by subagent id, in timeline order.
 */
@Immutable
class ChatSubagentTree(
    val hidden: Set<String>,
    val tools: Map<String, List<ChatItem.Tool>>,
    val children: Map<String, List<ChatItem.Subagent>>,
) {
    companion object {
        val EMPTY = ChatSubagentTree(emptySet(), emptyMap(), emptyMap())
    }
}

fun chatSubagentTree(items: List<ChatItem>): ChatSubagentTree {
    val cards = HashMap<String, ChatItem.Subagent>()
    items.forEach { if (it is ChatItem.Subagent) cards.putIfAbsent(it.subagentId, it) }
    if (cards.isEmpty()) return ChatSubagentTree.EMPTY
    val hidden = HashSet<String>()
    val tools = HashMap<String, MutableList<ChatItem.Tool>>()
    val children = HashMap<String, MutableList<ChatItem.Subagent>>()
    for (item in items) {
        when (item) {
            is ChatItem.Tool -> {
                val owner = item.subagentId?.takeIf { it in cards } ?: continue
                hidden += item.id
                tools.getOrPut(owner) { ArrayList() } += item
            }
            is ChatItem.Subagent -> {
                if (!nestsInCard(item, cards)) continue
                hidden += item.id
                children.getOrPut(item.parentSubagentId!!) { ArrayList() } += item
            }
            else -> Unit
        }
    }
    return ChatSubagentTree(hidden, tools, children)
}

/** A parent chain that loops back would hide every card in it, so such a card stays in the main flow. */
private fun nestsInCard(card: ChatItem.Subagent, cards: Map<String, ChatItem.Subagent>): Boolean {
    val seen = hashSetOf(card.subagentId)
    var current = card
    while (true) {
        val parent = current.parentSubagentId?.let { cards[it] } ?: return current !== card
        if (!seen.add(parent.subagentId)) return false
        current = parent
    }
}

/**
 * Focus view: folds thinking, tool calls, and agent messages that more work
 * follows in the same turn; everything else shows in place. A turn ends at the
 * next user message or notice: a turn sent from another app on the PC starts
 * with a notice, and must not fold the answer before it. Items a subagent card
 * shows are left out first; a card itself never folds, but counts as work
 * after an agent message, like a tool call. Cards with only thinking and tool
 * calls between them form a run; a run of two or more shows at its first
 * card's place through [subagentEntries].
 */
fun chatTimelineRows(
    items: List<ChatItem>,
    focus: Boolean,
    running: Boolean,
    waitingToolIds: Set<String>,
    openSteps: Set<String>,
    subagents: ChatSubagentTree = chatSubagentTree(items),
): List<ChatTimelineRow> {
    val shown = if (subagents.hidden.isEmpty()) items else items.filter { it.id !in subagents.hidden }
    val runs = subagentRuns(shown)
    val kept = if (runs.isEmpty()) shown else {
        val merged = runs.values.flatMapTo(HashSet()) { run -> run.drop(1).map { it.id } }
        shown.filter { it.id !in merged }
    }
    val rows = foldTimeline(kept, focus, running, waitingToolIds, openSteps)
    if (runs.isEmpty()) return rows
    return rows.flatMap { row ->
        val run = (row as? ChatTimelineRow.Item)?.let { runs[it.item.id] } ?: return@flatMap listOf(row)
        subagentEntries(run, subagents, waitingToolIds).map { entry ->
            when (entry) {
                is SubagentEntry.Card -> ChatTimelineRow.Item(entry.card, row.nested)
                is SubagentEntry.Group -> ChatTimelineRow.Subagents(entry.key, entry.cards, entry.key in openSteps)
            }
        }
    }
}

/** Runs of two or more cards with only thinking and tool calls between them, by the first card's id. */
private fun subagentRuns(items: List<ChatItem>): Map<String, List<ChatItem.Subagent>> {
    val runs = HashMap<String, List<ChatItem.Subagent>>()
    var run = ArrayList<ChatItem.Subagent>()
    fun close() {
        if (run.size >= 2) runs[run.first().id] = run
        run = ArrayList()
    }
    for (item in items) {
        when (item) {
            is ChatItem.Subagent -> run += item
            is ChatItem.Thought, is ChatItem.Tool -> Unit
            else -> close()
        }
    }
    close()
    return runs
}

private fun foldTimeline(
    items: List<ChatItem>,
    focus: Boolean,
    running: Boolean,
    waitingToolIds: Set<String>,
    openSteps: Set<String>,
): List<ChatTimelineRow> {
    if (!focus) return items.map { ChatTimelineRow.Item(it, nested = false) }
    val folded = BooleanArray(items.size)
    var workAfter = false
    for (index in items.indices.reversed()) {
        when (val item = items[index]) {
            is ChatItem.User, is ChatItem.Notice -> workAfter = false
            is ChatItem.Agent -> folded[index] = workAfter
            is ChatItem.Thought -> {
                folded[index] = true
                workAfter = true
            }
            is ChatItem.Tool -> {
                folded[index] = item.id !in waitingToolIds
                workAfter = true
            }
            is ChatItem.Subagent -> workAfter = true
            else -> Unit
        }
    }
    val rows = ArrayList<ChatTimelineRow>(items.size)
    var index = 0
    while (index < items.size) {
        if (!folded[index]) {
            rows += ChatTimelineRow.Item(items[index], nested = false)
            index += 1
            continue
        }
        val start = index
        while (index < items.size && folded[index]) index += 1
        val group = items.subList(start, index)
        val key = stepsRowKey(group.first().id)
        val live = running && index == items.size
        val open = key in openSteps
        rows += ChatTimelineRow.Steps(
            key = key,
            count = group.size,
            live = live,
            current = if (live) stepLabel(group.last()) else null,
            open = open,
        )
        if (open) group.forEach { rows += ChatTimelineRow.Item(it, nested = true) }
    }
    return rows
}

private fun stepLabel(item: ChatItem): String = when (item) {
    is ChatItem.Tool -> item.title.ifEmpty { "Tool call" }
    is ChatItem.Thought -> "Thinking"
    is ChatItem.Agent -> "Writing"
    else -> "Working"
}
