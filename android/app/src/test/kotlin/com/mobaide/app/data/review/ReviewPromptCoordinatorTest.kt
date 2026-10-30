package com.mobaide.app.data.review

import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.notifications.ForegroundSessionTracker
import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * The preferences store is a field-backed mock so the persisted counters can be
 * asserted directly.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ReviewPromptCoordinatorTest {

    private class Fixture {
        var launchCount = 2
        var goodSessions = 0
        var lastRequestedAt: Long? = null
        var now: Long = T0
        val tracker = ForegroundSessionTracker()
        val prefs = mockk<PreferencesStore>()
        val coordinator: ReviewPromptCoordinator

        init {
            coEvery { prefs.incrementReviewLaunchCount() } answers {
                launchCount += 1
                Unit
            }
            coEvery { prefs.incrementReviewGoodSessions() } answers {
                goodSessions += 1
                ReviewPromptState(launchCount, goodSessions, lastRequestedAt)
            }
            coEvery { prefs.markReviewRequested(any()) } answers {
                lastRequestedAt = firstArg()
                goodSessions = 0
                Unit
            }
            coordinator = ReviewPromptCoordinator(prefs, tracker) { now }
        }

        fun goodVisit() = coordinator.recordTerminalVisit(healthyMs = 60_000, endedCleanly = true)
    }

    /** A collector like `MainActivity`'s while it is resumed; returns what it received. */
    private fun TestScope.subscribe(f: Fixture): List<Long> {
        val delivered = mutableListOf<Long>()
        backgroundScope.launch { f.coordinator.requests.collect { delivered += it } }
        runCurrent()
        return delivered
    }

    /** One more good outing settles on the list and asks; returns the list visit the request carried. */
    private suspend fun TestScope.settleARequest(f: Fixture, delivered: List<Long>): Long {
        val before = delivered.size
        f.goodVisit()
        f.coordinator.sessionListShown()
        runCurrent()
        assertEquals(before + 1, delivered.size)
        return delivered.last()
    }

    @Test
    fun goodOutings_count_andTheThirdRequestsAReview() = runTest {
        val f = Fixture()
        val delivered = subscribe(f)

        repeat(2) { i ->
            f.goodVisit()
            f.coordinator.sessionListShown()
            assertEquals(i + 1, f.goodSessions)
        }
        assertTrue(delivered.isEmpty())

        f.goodVisit()
        f.coordinator.sessionListShown()
        runCurrent()

        assertEquals(1, delivered.size)
        assertNull(f.lastRequestedAt)
        assertEquals(3, f.goodSessions)
    }

    @Test
    fun visitsInOneOuting_sumTheirHealthyTime() = runTest {
        val f = Fixture()

        f.coordinator.recordTerminalVisit(healthyMs = 30_000, endedCleanly = true)
        f.coordinator.recordTerminalVisit(healthyMs = 30_000, endedCleanly = true)
        f.coordinator.sessionListShown()

        assertEquals(1, f.goodSessions)
    }

    @Test
    fun uncleanVisit_spoilsTheWholeOuting_andTheNextOutingStartsClean() = runTest {
        val f = Fixture()

        f.coordinator.recordTerminalVisit(healthyMs = 120_000, endedCleanly = true)
        f.coordinator.recordTerminalVisit(healthyMs = 0, endedCleanly = false)
        f.coordinator.sessionListShown()
        assertEquals(0, f.goodSessions)

        f.goodVisit()
        f.coordinator.sessionListShown()
        assertEquals(1, f.goodSessions)
    }

    @Test
    fun noteError_spoilsTheOpenOuting() = runTest {
        val f = Fixture()

        f.goodVisit()
        f.coordinator.noteError()
        f.coordinator.sessionListShown()

        assertEquals(0, f.goodSessions)
    }

    @Test
    fun noteError_duringTheFirstOpenVisit_spoilsTheOuting() = runTest {
        val f = Fixture()

        f.coordinator.terminalVisitStarted()
        f.coordinator.noteError()
        f.goodVisit()
        f.coordinator.sessionListShown()

        assertEquals(0, f.goodSessions)
    }

    @Test
    fun noteError_withNoOutingOpen_spoilsNothing() = runTest {
        val f = Fixture()

        f.coordinator.noteError()
        f.goodVisit()
        f.coordinator.sessionListShown()

        assertEquals(1, f.goodSessions)
    }

    @Test
    fun noteError_afterTheOutingClosed_spoilsNothing() = runTest {
        val f = Fixture()
        f.coordinator.terminalVisitStarted()
        f.goodVisit()
        f.coordinator.sessionListShown()

        f.coordinator.noteError()
        f.goodVisit()
        f.coordinator.sessionListShown()

        assertEquals(2, f.goodSessions)
    }

    @Test
    fun settle_terminalVisibleAgain_abandons_andTheOutingGoesOn() = runTest {
        val f = Fixture()
        f.goodVisit()

        val shown = launch { f.coordinator.sessionListShown() }
        advanceTimeBy(500)
        f.tracker.setVisible("s1")
        shown.join()
        assertEquals(0, f.goodSessions)

        f.tracker.clearVisible("s1")
        f.coordinator.sessionListShown()
        assertEquals(1, f.goodSessions)
    }

    @Test
    fun settle_gitChildHoldingAWatch_abandons_andTheOutingGoesOn() = runTest {
        val f = Fixture()
        f.goodVisit()

        f.tracker.holdWatch("s1")
        f.coordinator.sessionListShown()
        assertEquals(0, f.goodSessions)

        f.tracker.releaseWatch("s1")
        f.coordinator.sessionListShown()
        assertEquals(1, f.goodSessions)
    }

    @Test
    fun settle_cancelled_leavesTheOutingOpen() = runTest {
        val f = Fixture()
        f.goodVisit()

        val shown = launch { f.coordinator.sessionListShown() }
        advanceTimeBy(500)
        shown.cancel()
        runCurrent()
        assertEquals(0, f.goodSessions)

        f.coordinator.sessionListShown()
        assertEquals(1, f.goodSessions)
    }

    @Test
    fun settle_listHiddenDuringTheWait_abandons_andTheOutingGoesOn() = runTest {
        val f = Fixture()
        f.goodVisit()

        val shown = launch { f.coordinator.sessionListShown() }
        advanceTimeBy(500)
        f.coordinator.sessionListHidden()
        shown.join()
        assertEquals(0, f.goodSessions)

        f.coordinator.sessionListShown()
        assertEquals(1, f.goodSessions)
    }

    @Test
    fun settle_listHiddenAndShownAgainDuringTheWait_onlyTheNewListVisitAsks() = runTest {
        val f = Fixture()
        f.goodSessions = 2
        val delivered = subscribe(f)
        f.goodVisit()

        val first = launch { f.coordinator.sessionListShown() }
        advanceTimeBy(500)
        f.coordinator.sessionListHidden()
        val second = launch { f.coordinator.sessionListShown() }
        first.join()
        assertEquals(2, f.goodSessions)
        assertTrue(delivered.isEmpty())

        second.join()
        runCurrent()
        assertEquals(3, f.goodSessions)
        assertEquals(1, delivered.size)
        assertTrue(f.coordinator.isReviewMoment(delivered.single()))
    }

    @Test
    fun isReviewMoment_forTheListVisitThatAsked_onTheSettledList_isTrue() = runTest {
        val f = Fixture()
        f.goodSessions = 2
        val delivered = subscribe(f)

        val asked = settleARequest(f, delivered)

        assertTrue(f.coordinator.isReviewMoment(asked))
    }

    @Test
    fun isReviewMoment_afterTheListIsHidden_isFalse() = runTest {
        val f = Fixture()
        f.goodSessions = 2
        val asked = settleARequest(f, subscribe(f))

        f.coordinator.sessionListHidden()

        assertFalse(f.coordinator.isReviewMoment(asked))
    }

    @Test
    fun isReviewMoment_withATerminalOrGitChildOverTheList_isFalse() = runTest {
        val f = Fixture()
        f.goodSessions = 2
        val asked = settleARequest(f, subscribe(f))

        f.tracker.setVisible("s1")
        assertFalse(f.coordinator.isReviewMoment(asked))

        f.tracker.clearVisible("s1")
        f.tracker.holdWatch("s1")
        assertFalse(f.coordinator.isReviewMoment(asked))
    }

    @Test
    fun isReviewMoment_forAnEarlierListVisit_isFalse_onceTheListSettlesAgain() = runTest {
        val f = Fixture()
        f.goodSessions = 2
        val delivered = subscribe(f)
        val asked = settleARequest(f, delivered)

        f.coordinator.sessionListHidden()
        val current = settleARequest(f, delivered)

        assertNotEquals(asked, current)
        assertFalse(f.coordinator.isReviewMoment(asked))
        assertTrue(f.coordinator.isReviewMoment(current))
    }

    @Test
    fun requestNobodyCollects_startsNoCooldown_andTheNextGoodOutingAsksAgain() = runTest {
        val f = Fixture()
        f.goodSessions = 2

        f.goodVisit()
        f.coordinator.sessionListShown()

        assertNull(f.lastRequestedAt)
        assertEquals(3, f.goodSessions)

        val delivered = subscribe(f)
        f.goodVisit()
        f.coordinator.sessionListShown()
        runCurrent()

        assertEquals(1, delivered.size)
        assertNull(f.lastRequestedAt)
        assertEquals(4, f.goodSessions)
    }

    @Test
    fun requestNotLaunched_keepsAsking_onEachGoodOuting() = runTest {
        val f = Fixture()
        f.goodSessions = 2
        val delivered = subscribe(f)

        repeat(2) {
            f.goodVisit()
            f.coordinator.sessionListShown()
            runCurrent()
        }

        assertEquals(2, delivered.size)
        assertNull(f.lastRequestedAt)
    }

    @Test
    fun markReviewLaunched_stampsTheInjectedClock_andTheCooldownBlocksTheNextRequest() = runTest {
        val f = Fixture()
        f.goodSessions = 2
        val delivered = subscribe(f)
        f.goodVisit()
        f.coordinator.sessionListShown()
        runCurrent()
        assertEquals(1, delivered.size)

        f.now = T0 + 5_000
        f.coordinator.markReviewLaunched()
        assertEquals(T0 + 5_000, f.lastRequestedAt)
        assertEquals(0, f.goodSessions)

        f.now = T0 + 5_000 + 119 * DAY_MS
        repeat(3) {
            f.goodVisit()
            f.coordinator.sessionListShown()
            runCurrent()
        }
        assertEquals(1, delivered.size)

        f.now = T0 + 5_000 + 120 * DAY_MS
        f.goodVisit()
        f.coordinator.sessionListShown()
        runCurrent()
        assertEquals(2, delivered.size)
    }

    @Test
    fun firstLaunch_neverRequests() = runTest {
        val f = Fixture()
        f.launchCount = 1
        f.goodSessions = 2
        val delivered = subscribe(f)

        f.goodVisit()
        f.coordinator.sessionListShown()
        runCurrent()

        assertTrue(delivered.isEmpty())
        assertNull(f.lastRequestedAt)
        assertEquals(3, f.goodSessions)
    }

    @Test
    fun cooldown_isMeasuredOnTheInjectedClock() = runTest {
        val f = Fixture()
        f.goodSessions = 2
        f.lastRequestedAt = T0 - 119 * DAY_MS
        val delivered = subscribe(f)

        f.goodVisit()
        f.coordinator.sessionListShown()
        runCurrent()
        assertTrue(delivered.isEmpty())

        f.now = T0 + DAY_MS
        f.goodVisit()
        f.coordinator.sessionListShown()
        runCurrent()

        assertEquals(1, delivered.size)
    }

    @Test
    fun recordLaunch_eachCallCountsOneLaunch() = runTest {
        val f = Fixture()
        f.launchCount = 0

        f.coordinator.recordLaunch()
        f.coordinator.recordLaunch()
        f.coordinator.recordLaunch()

        assertEquals(3, f.launchCount)
    }

    private companion object {
        val T0: Long = Instant.parse("2026-10-29T12:00:00Z").toEpochMilli()
        const val DAY_MS: Long = 24 * 60 * 60 * 1000
    }
}
