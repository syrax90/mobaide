package com.mobaide.app.data.chat

import com.mobaide.app.data.auth.SessionManager
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * The last timeline of the few chat sessions opened most recently, kept in memory
 * so going back to one renders at once and its socket only syncs the missed events.
 * Emptied whenever the app leaves the connected agent.
 */
@Singleton
class ChatSessionCache @Inject constructor(sessionManager: SessionManager) {
    private val entries = object : LinkedHashMap<String, ChatTimeline>(MAX_SESSIONS, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, ChatTimeline>?): Boolean =
            size > MAX_SESSIONS
    }

    init {
        CoroutineScope(SupervisorJob() + Dispatchers.Default).launch {
            sessionManager.authState.collect { state ->
                if (state != SessionManager.AuthState.FullyConnected) clear()
            }
        }
    }

    @Synchronized
    fun get(sessionId: String): ChatTimeline? = entries[sessionId]

    @Synchronized
    fun put(sessionId: String, timeline: ChatTimeline) {
        entries[sessionId] = timeline
    }

    @Synchronized
    fun remove(sessionId: String) {
        entries.remove(sessionId)
    }

    @Synchronized
    fun clear() {
        entries.clear()
    }

    private companion object {
        const val MAX_SESSIONS = 6
    }
}
