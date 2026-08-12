package com.mobaide.app.notifications

import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.ProcessLifecycleOwner
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * App-scoped record of whether any of this process's activities is on screen.
 *
 * Deliberately not a screen-level `ON_STOP`: a `NavBackStackEntry` also drops
 * to `CREATED` when the user navigates forward or pops, so `ON_STOP` cannot
 * tell "backgrounded" from "left this screen".
 */
@Singleton
class AppForegroundTracker @Inject constructor() : DefaultLifecycleObserver {
    private val _isForeground = MutableStateFlow(true)
    val isForeground: StateFlow<Boolean> = _isForeground.asStateFlow()

    /** Seeds from the current state, so a late start cannot report a stale value. */
    fun start(owner: LifecycleOwner = ProcessLifecycleOwner.get()) {
        _isForeground.value = owner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)
        owner.lifecycle.addObserver(this)
    }

    override fun onStart(owner: LifecycleOwner) {
        _isForeground.value = true
    }

    override fun onStop(owner: LifecycleOwner) {
        _isForeground.value = false
    }
}
