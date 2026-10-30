package com.mobaide.app.data.review

import android.os.SystemClock

/** Sums the spans in which a terminal was in healthy use, on a monotonic clock. */
class HealthyTimeMeter(private val clock: () -> Long = SystemClock::elapsedRealtime) {
    private var since: Long? = null
    private var totalMs = 0L

    fun update(healthy: Boolean) {
        val start = since
        if (healthy && start == null) {
            since = clock()
        } else if (!healthy && start != null) {
            totalMs += clock() - start
            since = null
        }
    }

    /** Closes any open span and returns the total, starting the next visit from zero. */
    fun finish(): Long {
        update(false)
        return totalMs.also { totalMs = 0 }
    }
}
