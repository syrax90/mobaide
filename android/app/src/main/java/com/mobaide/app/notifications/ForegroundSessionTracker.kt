package com.mobaide.app.notifications

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import javax.inject.Inject
import javax.inject.Singleton

/**
 * App-scoped record of which session's terminal screen is currently visible,
 * and which sessions a child screen is keeping watched.
 *
 * [visibleSessionId] is set by `TerminalScreen` while it is on screen and read
 * by [SessionEventService] so an attention notification is suppressed only for
 * the session the user is already looking at — alerts for *other* sessions
 * still fire even when the app is open.
 *
 * [watchHolds] is the "covered by a child screen" signal for the terminal's
 * unwatched-suspend governor. A session-scoped child (the git Changes and Diff
 * screens) disposes the terminal composable when it is pushed, which alone
 * reads as "nobody is watching" and would eventually close the PTY socket —
 * an expensive mistake, since every reopen replays the whole ring. The child
 * therefore holds a ref-counted watch for exactly as long as it is composed, so
 * every exit path (pop, a deep link pushing another terminal on top, process
 * death) releases it without knowing why. Visibility is deliberately not
 * transferred: the terminal is *not* on screen, so its own attention alerts
 * should still fire.
 */
@Singleton
class ForegroundSessionTracker @Inject constructor() {
    private val _visibleSessionId = MutableStateFlow<String?>(null)
    val visibleSessionId: StateFlow<String?> = _visibleSessionId.asStateFlow()

    private val _watchHolds = MutableStateFlow<Map<String, Int>>(emptyMap())
    val watchHolds: StateFlow<Map<String, Int>> = _watchHolds.asStateFlow()

    fun setVisible(sessionId: String) {
        _visibleSessionId.value = sessionId
    }

    fun clearVisible(sessionId: String) {
        // Only clear if it still points at this session: guards the race where
        // a newly opened terminal sets a different id before this one disposes.
        _visibleSessionId.compareAndSet(sessionId, null)
    }

    fun holdWatch(sessionId: String) {
        _watchHolds.update { holds -> holds + (sessionId to (holds[sessionId] ?: 0) + 1) }
    }

    fun releaseWatch(sessionId: String) {
        _watchHolds.update { holds ->
            val remaining = (holds[sessionId] ?: 0) - 1
            if (remaining <= 0) holds - sessionId else holds + (sessionId to remaining)
        }
    }

    fun isHeld(sessionId: String): Boolean = (_watchHolds.value[sessionId] ?: 0) > 0
}
