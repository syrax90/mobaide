package com.mobaide.app.testing

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import org.junit.rules.TestWatcher
import org.junit.runner.Description

/**
 * JUnit 4 rule that installs a [TestDispatcher] as the `Dispatchers.Main`
 * replacement for the duration of a single test, so that `viewModelScope`
 * (which is bound to `Dispatchers.Main.immediate`) can run inside `runTest { }`.
 *
 * The default implementation uses a [StandardTestDispatcher], which only
 * advances when the test explicitly yields (`advanceUntilIdle()`, `runCurrent()`,
 * or the `runTest { }` cleanup) — this matches the project's existing
 * `runTest { }` idiom in `data/repository/AutoScanRepositoryTest.kt`.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class MainDispatcherRule(
    private val testDispatcher: TestDispatcher = StandardTestDispatcher(),
) : TestWatcher() {

    override fun starting(description: Description) {
        Dispatchers.setMain(testDispatcher)
    }

    override fun finished(description: Description) {
        Dispatchers.resetMain()
    }
}
