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
 *
 * Every REST path carries an account-generation guard: the user id (not the
 * token — it survives token refresh mid-flight) is captured before the call
 * and the cache write is skipped if it changed by the time the response
 * lands, so a request resolving after logout/account switch cannot
 * repopulate a cache the previous account no longer owns. The server
 * response is still returned to the caller either way.
 */
class ShortcutsRepository(
    private val centralApi: CentralApi,
    private val userIdentityStore: UserIdentityStore,
) {
    private val _shortcuts = MutableStateFlow<List<ShortcutDto>>(emptyList())

    fun observeShortcuts(): StateFlow<List<ShortcutDto>> = _shortcuts.asStateFlow()

    suspend fun refreshShortcuts() {
        val owner = userIdentityStore.getUserId()
        val token = userIdentityStore.requireAccessToken()
        val list = centralApi.listShortcuts(token)
        if (userIdentityStore.getUserId() == owner) {
            _shortcuts.value = list
        }
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
        val owner = userIdentityStore.getUserId()
        val token = userIdentityStore.requireAccessToken()
        val created = centralApi.createShortcut(token, kind, chordToken, name)
        if (userIdentityStore.getUserId() == owner) {
            applyUpserted(created)
        }
        return created
    }

    /** Rename a shortcut; an empty [name] clears it back to the chord label. */
    suspend fun renameShortcut(shortcutId: String, name: String): ShortcutDto {
        val owner = userIdentityStore.getUserId()
        val token = userIdentityStore.requireAccessToken()
        val updated = centralApi.updateShortcut(
            token,
            shortcutId,
            UpdateShortcutRequest(name = name),
        )
        if (userIdentityStore.getUserId() == owner) {
            applyUpserted(updated)
        }
        return updated
    }

    suspend fun deleteShortcut(shortcutId: String) {
        val owner = userIdentityStore.getUserId()
        val token = userIdentityStore.requireAccessToken()
        centralApi.deleteShortcut(token, shortcutId)
        if (userIdentityStore.getUserId() == owner) {
            applyDeleted(shortcutId)
        }
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

    /**
     * Purge every cached shortcut of [kind] — the client half of the
     * server-side cascade on `custom_type.deleted`, which soft-deletes the
     * type's shortcuts without emitting per-shortcut `shortcut.deleted`
     * events.
     */
    fun purgeKind(kind: String) {
        _shortcuts.update { current ->
            current.filterNot { it.kind == kind }
        }
    }

    fun clear() {
        _shortcuts.value = emptyList()
    }
}
