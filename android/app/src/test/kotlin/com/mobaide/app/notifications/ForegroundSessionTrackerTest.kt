package com.mobaide.app.notifications

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * Unit tests for [ForegroundSessionTracker] (CLA-64). The tracker is a plain
 * `@Singleton` holding a `StateFlow<String?>`; its `.value` is read
 * synchronously, so no coroutine machinery is needed.
 *
 * The subtle contract is [ForegroundSessionTracker.clearVisible]: it must only
 * clear when the stored id still matches, so the race where a newly opened
 * terminal sets a different id before the old one disposes does not wipe the
 * new visible session.
 */
class ForegroundSessionTrackerTest {

    @Test
    fun startsWithNoVisibleSession() {
        val tracker = ForegroundSessionTracker()
        assertNull(tracker.visibleSessionId.value)
    }

    @Test
    fun setVisible_storesTheSessionId() {
        val tracker = ForegroundSessionTracker()
        tracker.setVisible("s1")
        assertEquals("s1", tracker.visibleSessionId.value)
    }

    @Test
    fun setVisible_overwritesPreviousSession() {
        val tracker = ForegroundSessionTracker()
        tracker.setVisible("s1")
        tracker.setVisible("s2")
        assertEquals("s2", tracker.visibleSessionId.value)
    }

    @Test
    fun clearVisible_matchingId_clearsToNull() {
        val tracker = ForegroundSessionTracker()
        tracker.setVisible("s1")
        tracker.clearVisible("s1")
        assertNull(tracker.visibleSessionId.value)
    }

    @Test
    fun clearVisible_nonMatchingId_isIgnored() {
        // The dispose-after-open race: terminal A disposes (clearVisible("A"))
        // after terminal B has already become visible (setVisible("B")). The
        // stale clear must NOT wipe B.
        val tracker = ForegroundSessionTracker()
        tracker.setVisible("B")
        tracker.clearVisible("A")
        assertEquals("B", tracker.visibleSessionId.value)
    }

    @Test
    fun clearVisible_whenAlreadyNull_staysNull() {
        val tracker = ForegroundSessionTracker()
        tracker.clearVisible("s1")
        assertNull(tracker.visibleSessionId.value)
    }
}
