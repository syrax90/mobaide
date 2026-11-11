package com.mobaide.app.notifications

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [SessionEventService.shouldNotify] — the pure suppression
 * rule (CLA-64). No Android framework, no coroutines: it is a static function
 * on the companion object, so it is exercised directly.
 *
 * Rule: notify UNLESS the app is in the foreground AND the user is already
 * viewing the very session the event is for. The product-confirmed edge case
 * — an alert for a *different* session while the app is foreground — must
 * still notify.
 */
class SessionEventServiceTest {

    // ---- The four-row truth table -------------------------------------------

    @Test
    fun foreground_sameSessionVisible_isSuppressed() {
        assertFalse(
            SessionEventService.shouldNotify(
                appForeground = true,
                visibleSessionId = "s1",
                eventSessionId = "s1",
            ),
        )
    }

    @Test
    fun foreground_differentSessionVisible_stillNotifies() {
        // The explicitly product-confirmed case: app open on session s2, an
        // alert arrives for s1 — the in-app UI is not showing s1, so notify.
        assertTrue(
            SessionEventService.shouldNotify(
                appForeground = true,
                visibleSessionId = "s2",
                eventSessionId = "s1",
            ),
        )
    }

    @Test
    fun background_sameSessionId_notifies() {
        // Backgrounded → always notify, even if the last-visible session id
        // happens to match the event.
        assertTrue(
            SessionEventService.shouldNotify(
                appForeground = false,
                visibleSessionId = "s1",
                eventSessionId = "s1",
            ),
        )
    }

    // ---- Null visible-session handling --------------------------------------

    @Test
    fun foreground_noVisibleSession_notifies() {
        // App is foreground but not on any terminal screen (e.g. the session
        // list). Nothing to suppress against → notify.
        assertTrue(
            SessionEventService.shouldNotify(
                appForeground = true,
                visibleSessionId = null,
                eventSessionId = "s1",
            ),
        )
    }
}
