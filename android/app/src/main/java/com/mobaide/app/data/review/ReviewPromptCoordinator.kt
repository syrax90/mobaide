package com.mobaide.app.data.review

import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.notifications.ForegroundSessionTracker
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

/**
 * An outing runs from the first terminal visit to the next time the session
 * list settles on screen. It is good when the terminals were in healthy use
 * for [ReviewPromptPolicy.MIN_HEALTHY_MS] and every visit ended cleanly; it
 * lives in memory only.
 */
class ReviewPromptCoordinator(
    private val preferencesStore: PreferencesStore,
    private val foregroundSessionTracker: ForegroundSessionTracker,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private data class Outing(val started: Boolean = false, val healthyMs: Long = 0, val clean: Boolean = true)

    private val lock = Any()
    private var outing = Outing()

    @Volatile
    private var sessionListOnScreen = false

    private val _requests = MutableSharedFlow<Unit>(extraBufferCapacity = 1)

    /**
     * One emission per review request, collected by `MainActivity` while it is
     * resumed. An emission nobody collects, or a card dropped by the
     * re-check, starts no cooldown: [markReviewLaunched] does that.
     */
    val requests: SharedFlow<Unit> = _requests.asSharedFlow()

    /**
     * Counts one launch: a fresh `MainActivity`, not a process. The session
     * notification service keeps the process alive after the task is swiped
     * away, so a per-process count would stall at the first launch for weeks.
     */
    suspend fun recordLaunch() {
        preferencesStore.incrementReviewLaunchCount()
    }

    fun terminalVisitStarted() {
        synchronized(lock) { outing = outing.copy(started = true) }
    }

    fun recordTerminalVisit(healthyMs: Long, endedCleanly: Boolean) {
        synchronized(lock) {
            outing = Outing(
                started = true,
                healthyMs = outing.healthyMs + healthyMs,
                clean = outing.clean && endedCleanly,
            )
        }
    }

    /** An error shown to the user spoils the open outing. With none open there is nothing to spoil. */
    fun noteError() {
        synchronized(lock) {
            if (outing.started) outing = outing.copy(clean = false)
        }
    }

    fun sessionListHidden() {
        sessionListOnScreen = false
    }

    /**
     * The session list is still the screen in front, with no terminal or git
     * child screen over it. Read again right before the card launches, since
     * fetching it from Play takes a round trip the user may tap through.
     */
    fun isReviewMoment(): Boolean =
        sessionListOnScreen &&
            foregroundSessionTracker.visibleSessionId.value == null &&
            foregroundSessionTracker.watchHolds.value.isEmpty()

    /**
     * Run while the session list is resumed; cancelling it (the list paused
     * during the settle wait) leaves the outing open.
     */
    suspend fun sessionListShown() {
        sessionListOnScreen = true
        delay(ReviewPromptPolicy.SETTLE_MS)
        // Back inside a session (auto-resume, a quick tap, a git child screen):
        // the outing goes on.
        if (!isReviewMoment()) return
        val finished = synchronized(lock) { outing.also { outing = Outing() } }
        if (!ReviewPromptPolicy.isGoodOuting(finished.healthyMs, finished.clean)) return
        val state = preferencesStore.incrementReviewGoodSessions()
        if (ReviewPromptPolicy.isEligible(state, clock())) _requests.tryEmit(Unit)
    }

    /** The card is about to show: starts the cooldown and the next run of good sessions. */
    suspend fun markReviewLaunched() {
        preferencesStore.markReviewRequested(clock())
    }
}
