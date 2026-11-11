package com.mobaide.app.testing

import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout

/**
 * Blocks until [count] collectors have subscribed. Repositories start their
 * event-bus collectors on a real `Dispatchers.Default` thread, so a `replay = 0`
 * emit that wins that race is dropped and the test times out waiting for it.
 */
fun MutableSharedFlow<*>.awaitSubscribers(count: Int = 1) {
    runBlocking { withTimeout(5_000) { subscriptionCount.first { it >= count } } }
}
