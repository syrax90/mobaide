package com.mobaide.app.data.review

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

/**
 * Unit tests for [ReviewPromptPolicy]: the healthy-time threshold, the
 * first-launch and good-session gates, and the cooldown edges, including a
 * stamp left in the future by a clock that moved back.
 */
class ReviewPromptPolicyTest {

    private fun state(
        launchCount: Int = 2,
        goodSessions: Int = 3,
        lastRequestedAtMs: Long? = null,
    ) = ReviewPromptState(launchCount, goodSessions, lastRequestedAtMs)

    @Test
    fun isGoodOuting_needsAFullMinuteOfHealthyTime() {
        assertFalse(ReviewPromptPolicy.isGoodOuting(healthyMs = 59_999, allVisitsClean = true))
        assertTrue(ReviewPromptPolicy.isGoodOuting(healthyMs = 60_000, allVisitsClean = true))
    }

    @Test
    fun isGoodOuting_uncleanVisit_isNeverGood() {
        assertFalse(ReviewPromptPolicy.isGoodOuting(healthyMs = 3_600_000, allVisitsClean = false))
    }

    @Test
    fun isEligible_firstLaunch_neverAsks() {
        assertFalse(ReviewPromptPolicy.isEligible(state(launchCount = 1), T0))
    }

    @Test
    fun isEligible_secondGoodSession_isNotEnough_theThirdIs() {
        assertFalse(ReviewPromptPolicy.isEligible(state(goodSessions = 2), T0))
        assertTrue(ReviewPromptPolicy.isEligible(state(goodSessions = 3), T0))
    }

    @Test
    fun isEligible_cooldown_endsAfterExactly120Days() {
        assertFalse(ReviewPromptPolicy.isEligible(state(lastRequestedAtMs = T0 - 119 * DAY_MS), T0))
        assertTrue(ReviewPromptPolicy.isEligible(state(lastRequestedAtMs = T0 - 120 * DAY_MS), T0))
    }

    @Test
    fun isEligible_stampInTheFuture_isIgnored() {
        assertTrue(ReviewPromptPolicy.isEligible(state(lastRequestedAtMs = T0 + DAY_MS), T0))
    }

    private companion object {
        val T0: Long = Instant.parse("2026-10-29T12:00:00Z").toEpochMilli()
        const val DAY_MS: Long = 24 * 60 * 60 * 1000
    }
}
