package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.auth.requireAccessToken
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.ShortcutDto
import com.mobaide.app.data.network.dto.UpdateShortcutRequest
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Repository for per-user saved keyboard shortcuts via the central server.
 * Caches the (non-deleted) shortcut list in-memory and exposes it as a
 * [StateFlow].
 *
 * Cache mutations come from two paths: explicit REST calls made by the UI
 * (`createShortcut`, `renameShortcut`, ...) and inbound `shortcut.*` events
 * delivered over `/ws/user-events` and forwarded by `UserEventBus`.
 */
class ShortcutsRepository(
    private val centralApi: CentralApi,
    private val userIdentityStore: UserIdentityStore,
) {
    private val _shortcuts = MutableStateFlow<List<ShortcutDto>>(emptyList())

    fun observeShortcuts(): StateFlow<List<ShortcutDto>> = _shortcuts.asStateFlow()

    suspend fun refreshShortcuts() {
        val token = userIdentityStore.requireAccessToken()
        _shortcuts.value = centralApi.listShortcuts(token)
    }

    /**
     * Save a chord for a session kind. The server upserts: re-saving an
     * existing (kind, chordToken) pair updates its name instead of creating
     * a duplicate button, so the returned row's id may belong to a
     * previously saved shortcut.
     */
    suspend fun createShortcut(
        kind: String,
        chordToken: String,
        name: String?,
    ): ShortcutDto {
        val token = userIdentityStore.requireAccessToken()
        val created = centralApi.createShortcut(token, kind, chordToken, name)
        applyUpserted(created)
        return created
    }

    /** Rename a shortcut; an empty [name] clears it back to the chord label. */
    suspend fun renameShortcut(shortcutId: String, name: String): ShortcutDto {
        val token = userIdentityStore.requireAccessToken()
        val updated = centralApi.updateShortcut(
            token,
            shortcutId,
            UpdateShortcutRequest(name = name),
        )
        applyUpserted(updated)
        return updated
    }

    suspend fun deleteShortcut(shortcutId: String) {
        val token = userIdentityStore.requireAccessToken()
        centralApi.deleteShortcut(token, shortcutId)
        applyDeleted(shortcutId)
    }

    /** Insert or replace a shortcut in the cache by `shortcut_id`. */
    fun applyUpserted(shortcut: ShortcutDto) {
        _shortcuts.update { current ->
            val idx = current.indexOfFirst { it.shortcut_id == shortcut.shortcut_id }
            if (idx == -1) current + shortcut
            else current.toMutableList().also { it[idx] = shortcut }
        }
    }

    fun applyDeleted(shortcutId: String) {
        _shortcuts.update { current ->
            current.filterNot { it.shortcut_id == shortcutId }
        }
    }

    fun clear() {
        _shortcuts.value = emptyList()
    }
}
