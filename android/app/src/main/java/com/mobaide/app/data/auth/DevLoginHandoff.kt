package com.mobaide.app.data.auth

import android.content.Intent
import com.mobaide.app.BuildConfig
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Debug builds only: `tests/e2e/manual_stack.py` launches the app with these
 * two extras to sign in to its throwaway stack (iOS reads the same pair from
 * launch arguments). Held in memory until the login screen consumes it, so it
 * works whether that screen exists before or after the launch intent arrives.
 * A manual sign-out discards it, so signing out stays signed out even when the
 * app started signed in and the login screen first appears only then.
 */
object DevLoginHandoff {
    const val EXTRA_EMAIL = "com.mobaide.app.extra.DEV_LOGIN_EMAIL"
    const val EXTRA_PASSWORD = "com.mobaide.app.extra.DEV_LOGIN_PASSWORD"

    data class Credentials(val email: String, val password: String)

    private val _pending = MutableStateFlow<Credentials?>(null)
    val pending: StateFlow<Credentials?> = _pending.asStateFlow()

    fun offer(intent: Intent?) {
        if (!BuildConfig.DEBUG || intent == null) return
        val email = intent.getStringExtra(EXTRA_EMAIL)?.takeIf { it.isNotBlank() }
        val password = intent.getStringExtra(EXTRA_PASSWORD)?.takeIf { it.isNotEmpty() }
        intent.removeExtra(EXTRA_EMAIL)
        intent.removeExtra(EXTRA_PASSWORD)
        if (email != null && password != null) _pending.value = Credentials(email, password)
    }

    fun discard() {
        _pending.value = null
    }

    /** Hands the credentials to exactly one caller. */
    fun take(expected: Credentials): Boolean = _pending.compareAndSet(expected, null)
}
