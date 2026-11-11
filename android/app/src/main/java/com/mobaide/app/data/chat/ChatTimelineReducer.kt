package com.mobaide.app.data.chat

import androidx.compose.runtime.Immutable
import kotlinx.serialization.json.JsonObject

/** The client's copy of one chat session: the last applied [epoch] + [seq], state and items. */
@Immutable
data class ChatTimeline(
    val epoch: String,
    val seq: Long,
    val rawState: JsonObject,
    val state: ChatState,
    val items: List<ChatItem>,
    val truncated: Boolean,
)

sealed interface ChatSyncRequest {
    /** `sync` without epoch: the agent answers with a snapshot. */
    data object Snapshot : ChatSyncRequest

    /** `sync{epoch, after_seq}`: the agent answers with the missed events (or a snapshot). */
    data class Resume(val epoch: String, val afterSeq: Long) : ChatSyncRequest
}

/** What one frame did: whether the timeline changed, and a `sync` the caller must send now. */
data class ChatReduction(
    val changed: Boolean,
    val stateChanged: Boolean,
    val sync: ChatSyncRequest?,
) {
    companion object {
        val NONE = ChatReduction(changed = false, stateChanged = false, sync = null)
    }
}

/**
 * Applies snapshot and events frames with the sync rules of the chat protocol.
 * Pure: no I/O, the caller sends whatever [ChatReduction.sync] asks for.
 *
 * The relay fans every frame out to all clients of a session, so frames caused by
 * another client's sync arrive here too: duplicates are skipped by seq, and while
 * one of our own syncs is unanswered further gaps do not send another one.
 */
class ChatTimelineReducer(
    initial: ChatTimeline? = null,
    private val maxItems: Int = MAX_ITEMS,
) {
    var timeline: ChatTimeline? = initial
        private set

    private var pendingSync: ChatSyncRequest? = null
    private var framesWhilePending = 0
    private val items = ArrayList<ChatItem>()
    private val index = HashMap<String, Int>()
    private var truncated = initial?.truncated ?: false

    init {
        initial?.items?.forEach(::upsert)
    }

    /** The sync to send right after every socket open. */
    fun onOpen(): ChatSyncRequest {
        val current = timeline
        val request = if (current == null) ChatSyncRequest.Snapshot else ChatSyncRequest.Resume(current.epoch, current.seq)
        markPending(request)
        return request
    }

    fun onClose() {
        pendingSync = null
    }

    fun apply(frame: ChatFrame.Snapshot): ChatReduction {
        items.clear()
        index.clear()
        frame.items.forEach(::upsert)
        truncated = frame.truncated
        enforceCap()
        timeline = ChatTimeline(
            epoch = frame.epoch,
            seq = frame.seq,
            rawState = frame.state,
            state = ChatJson.state(frame.state),
            items = items.toList(),
            truncated = truncated,
        )
        pendingSync = null
        return ChatReduction(changed = true, stateChanged = true, sync = null)
    }

    fun apply(frame: ChatFrame.Events): ChatReduction {
        val current = timeline
            ?: return ChatReduction(changed = false, stateChanged = false, sync = pendingSync?.let { resendIfStale() })
        val pending = pendingSync
        if (pending == ChatSyncRequest.Snapshot) {
            return ChatReduction(changed = false, stateChanged = false, sync = resendIfStale())
        }
        if (frame.epoch != current.epoch) {
            markPending(ChatSyncRequest.Snapshot)
            return ChatReduction(changed = false, stateChanged = false, sync = ChatSyncRequest.Snapshot)
        }

        var seq = current.seq
        var rawState = current.rawState
        var stateTouched = false
        var itemsTouched = false
        var gap = false
        for (event in frame.events) {
            if (event.seq <= seq) continue
            if (event.seq != seq + 1) {
                gap = true
                break
            }
            seq = event.seq
            when (event) {
                is ChatEvent.Upsert -> event.item?.let {
                    upsert(it)
                    itemsTouched = true
                }
                is ChatEvent.Append -> {
                    val at = index[event.id]
                    val appended = at?.let { items[it].appendText(event.text) }
                    if (at != null && appended != null) {
                        items[at] = appended
                        itemsTouched = true
                    }
                }
                is ChatEvent.Remove -> if (remove(event.id)) itemsTouched = true
                is ChatEvent.StatePatch -> {
                    rawState = JsonObject(rawState + event.patch)
                    stateTouched = true
                }
                is ChatEvent.Unknown -> Unit
            }
        }
        // A frame whose own seq is past what its events reach (an empty ack
        // after missed frames, for example) also means events were missed.
        if (!gap && frame.seq > seq) gap = true
        if (itemsTouched) enforceCap()
        if (seq != current.seq) {
            timeline = current.copy(
                seq = seq,
                rawState = rawState,
                state = if (stateTouched) ChatJson.state(rawState) else current.state,
                items = if (itemsTouched) items.toList() else current.items,
                truncated = truncated,
            )
        }

        val sync = when {
            !gap -> {
                if (pending is ChatSyncRequest.Resume) pendingSync = null
                null
            }
            pending == null -> ChatSyncRequest.Resume(current.epoch, seq).also(::markPending)
            else -> resendIfStale()
        }
        return ChatReduction(changed = itemsTouched || stateTouched, stateChanged = stateTouched, sync = sync)
    }

    private fun markPending(request: ChatSyncRequest) {
        pendingSync = request
        framesWhilePending = 0
    }

    /** A sync answer lost on the way (a full frame buffer drops frames) must not wedge us. */
    private fun resendIfStale(): ChatSyncRequest? {
        framesWhilePending += 1
        if (framesWhilePending < STALE_SYNC_FRAMES) return null
        val current = timeline
        val request = if (pendingSync is ChatSyncRequest.Resume && current != null) {
            ChatSyncRequest.Resume(current.epoch, current.seq)
        } else {
            ChatSyncRequest.Snapshot
        }
        markPending(request)
        return request
    }

    private fun upsert(item: ChatItem) {
        val at = index[item.id]
        if (at != null) {
            items[at] = item
        } else {
            index[item.id] = items.size
            items.add(item)
        }
    }

    /** Rare (a setup notice going away), so the index is simply rebuilt. */
    private fun remove(id: String): Boolean {
        val at = index[id] ?: return false
        items.removeAt(at)
        reindex()
        return true
    }

    private fun reindex() {
        index.clear()
        items.forEachIndexed { i, item -> index[item.id] = i }
    }

    /**
     * Finished steps go first, oldest first, then items from the front; the newest
     * item and running tools stay (a dropped tool's next update would re-add it).
     * [truncated] marks only dropped messages.
     */
    private fun enforceCap() {
        var excess = items.size - maxItems
        if (excess <= 0) return
        val last = items.lastIndex
        val drop = BooleanArray(items.size)
        for (i in 0 until last) {
            if (excess == 0) break
            if (items[i].isFinishedStep) {
                drop[i] = true
                excess -= 1
            }
        }
        for (i in 0 until last) {
            if (excess == 0) break
            if (drop[i]) continue
            drop[i] = true
            excess -= 1
            if (!items[i].isStep) truncated = true
        }
        val kept = items.filterIndexed { i, _ -> !drop[i] }
        items.clear()
        items.addAll(kept)
        reindex()
    }

    private val ChatItem.isStep: Boolean get() = this is ChatItem.Tool || this is ChatItem.Thought

    private val ChatItem.isFinishedStep: Boolean
        get() = this is ChatItem.Thought ||
            (this is ChatItem.Tool && status != ToolStatus.PENDING && status != ToolStatus.IN_PROGRESS)

    companion object {
        const val MAX_ITEMS = 1500
        private const val STALE_SYNC_FRAMES = 12
    }
}
