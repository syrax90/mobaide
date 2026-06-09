package com.mobaide.app.notifications

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * App-scoped record of which session's terminal screen is currently visible.
 *
 * Set by `TerminalScreen` while it is on screen and read by
 * [SessionEventService] so an attention notification is suppressed only for the
 * session the user is already looking at — alerts for *other* sessions still
 * fire even when the app is open.
 */
@Singleton
class ForegroundSessionTracker @Inject constructor() {
    private val _visibleSessionId = MutableStateFlow<String?>(null)
    val visibleSessionId: StateFlow<String?> = _visibleSessionId.asStateFlow()

    fun setVisible(sessionId: String) {
        _visibleSessionId.value = sessionId
    }

    fun clearVisible(sessionId: String) {
        // Only clear if it still points at this session: guards the race where
        // a newly opened terminal sets a different id before this one disposes.
        _visibleSessionId.compareAndSet(sessionId, null)
    }
}
