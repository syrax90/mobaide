package com.mobaide.app.data.local

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Which finished turns of Herdr agents the user has already looked at, on this
 * device. A `done` pane whose `turn` is stored here shows as idle.
 *
 * Kept on the device and not in herdr: the only herdr call that marks an agent
 * seen also moves the view of a user at the desktop.
 */
@Singleton
class HerdrSeenStore @Inject constructor(
    @ApplicationContext context: Context,
) {
    private val prefs = context.getSharedPreferences("herdr_seen", Context.MODE_PRIVATE)

    private val _seen = MutableStateFlow(load())
    val seen: StateFlow<Map<String, Long>> = _seen.asStateFlow()

    fun markSeen(key: String, turn: Long) {
        if (_seen.value[key] == turn) return
        val next = (_seen.value + (key to turn)).let { all ->
            // A pane id is never reused on purpose, so old entries only grow.
            if (all.size > MAX_ENTRIES) all.entries.drop(all.size - MAX_ENTRIES).associate { it.toPair() }
            else all
        }
        _seen.value = next
        prefs.edit().clear().apply { next.forEach { (k, v) -> putLong(k, v) } }.apply()
    }

    private fun load(): Map<String, Long> =
        prefs.all.mapNotNull { (key, value) -> (value as? Long)?.let { key to it } }.toMap()

    companion object {
        private const val MAX_ENTRIES = 500

        /** The key of one pane of one server of one agent. */
        fun key(agentId: String, serverId: String, paneId: String): String =
            "$agentId\u0000$serverId\u0000$paneId"
    }
}
