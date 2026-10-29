package com.mobaide.app.data.review

import org.junit.Assert.assertEquals
import org.junit.Test

/** Unit tests for [HealthyTimeMeter] on a hand-driven clock. */
class HealthyTimeMeterTest {

    private var now = 0L
    private val meter = HealthyTimeMeter { now }

    @Test
    fun sumsOnlyTheHealthySpans() {
        meter.update(true)
        now = 10_000
        meter.update(false)
        now = 25_000
        meter.update(true)
        now = 40_000
        meter.update(false)

        assertEquals(25_000L, meter.finish())
    }

    @Test
    fun repeatedHealthyUpdates_doNotRestartTheSpan() {
        meter.update(true)
        now = 5_000
        meter.update(true)
        now = 12_000
        meter.update(false)

        assertEquals(12_000L, meter.finish())
    }

    @Test
    fun finish_closesTheOpenSpan() {
        meter.update(true)
        now = 61_000

        assertEquals(61_000L, meter.finish())
    }

    @Test
    fun finish_startsTheNextVisitFromZero() {
        meter.update(true)
        now = 30_000
        meter.finish()

        now = 50_000
        meter.update(true)
        now = 52_000

        assertEquals(2_000L, meter.finish())
    }
}
