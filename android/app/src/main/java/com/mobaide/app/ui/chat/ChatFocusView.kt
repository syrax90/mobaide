package com.mobaide.app.ui.chat

import androidx.compose.runtime.Immutable
import com.mobaide.app.data.chat.ChatItem

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
}

fun stepsRowKey(firstItemId: String): String = "steps:$firstItemId"

fun stepsLabel(count: Int): String = if (count == 1) "1 step" else "$count steps"

/**
 * Focus view: folds thinking, tool calls, and agent messages that more work
 * follows in the same turn; everything else shows in place. A turn ends at the
 * next user message or notice: a turn sent from another app on the PC starts
 * with a notice, and must not fold the answer before it.
 */
fun chatTimelineRows(
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
