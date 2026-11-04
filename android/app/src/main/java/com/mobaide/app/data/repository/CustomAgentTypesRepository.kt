package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.auth.requireAccessToken
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.SessionModeOrderDto
import com.mobaide.app.data.network.dto.UpdateCustomAgentTypeRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Repository for per-user custom AI agent types via the central server.
 * Caches the (non-deleted) type list in-memory and exposes it as a
 * [StateFlow].
 *
 * Cache mutations come from two paths: explicit REST calls made by the UI
 * (`createType`, `updateType`, ...) and inbound `custom_type.*` events
 * delivered over `/ws/user-events` and forwarded by `UserEventBus`.
 *
 * Every REST path carries an account-generation guard: the user id (not the
 * token — it survives token refresh mid-flight) is captured before the call
 * and the cache write is skipped if it changed by the time the response
 * lands, so a request resolving after logout/account switch cannot
 * repopulate a cache the previous account no longer owns. The server
 * response is still returned to the caller either way.
 *
 * It also holds the account's order of the mode picker's cards (built-in
 * modes and custom types, by mode id), from `GET /api/session-mode-order` and
 * the `session_mode_order.updated` push. [observeModeOrder] is the order to
 * show: a save in flight shows its own order until the server confirms it.
 */
class CustomAgentTypesRepository(
    private val centralApi: CentralApi,
    private val userIdentityStore: UserIdentityStore,
) {
    private val _types = MutableStateFlow<List<CustomAgentTypeDto>>(emptyList())

    // Saves outlive the screen that started them, so leaving the wizard right
    // after a drop still stores the new order.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val orderLock = Any()
    private val saveMutex = Mutex()
    private var serverModeOrder = SessionModeOrderDto()
    private var pendingModeOrder: List<String>? = null
    private val _modeOrder = MutableStateFlow<List<String>>(emptyList())

    fun observeTypes(): StateFlow<List<CustomAgentTypeDto>> = _types.asStateFlow()

    fun observeModeOrder(): StateFlow<List<String>> = _modeOrder.asStateFlow()

    suspend fun refreshModeOrder() {
        val owner = userIdentityStore.getUserId()
        val token = userIdentityStore.requireAccessToken()
        val order = centralApi.getSessionModeOrder(token)
        if (userIdentityStore.getUserId() == owner) {
            applyModeOrder(order)
        }
    }

    /**
     * Save [order] and show it at once. Saves run one at a time, in call
     * order, so a later drop is never overtaken by an earlier one. On failure
     * the order falls back to the last one the server confirmed and the
     * exception reaches the caller.
     */
    suspend fun saveModeOrder(order: List<String>) {
        val owner = userIdentityStore.getUserId()
        synchronized(orderLock) {
            pendingModeOrder = order
            publishModeOrderLocked()
        }
        scope.async {
            try {
                val saved = saveMutex.withLock {
                    // A save queued before a sign-out must not run with the next
                    // account's token and write this account's order into it.
                    if (userIdentityStore.getUserId() != owner) return@withLock null
                    centralApi.putSessionModeOrder(userIdentityStore.requireAccessToken(), order)
                }
                if (saved != null && userIdentityStore.getUserId() == owner) applyModeOrder(saved)
            } finally {
                synchronized(orderLock) {
                    if (pendingModeOrder == order) {
                        pendingModeOrder = null
                        publishModeOrderLocked()
                    }
                }
            }
        }.await()
    }

    /** Apply a server snapshot; one older than the cached version is dropped. */
    fun applyModeOrder(order: SessionModeOrderDto) {
        synchronized(orderLock) {
            if (order.version < serverModeOrder.version) return
            serverModeOrder = order
            if (pendingModeOrder == order.order) pendingModeOrder = null
            publishModeOrderLocked()
        }
    }

    private fun publishModeOrderLocked() {
        _modeOrder.value = pendingModeOrder ?: serverModeOrder.order
    }

    suspend fun refreshTypes() {
        val owner = userIdentityStore.getUserId()
        val token = userIdentityStore.requireAccessToken()
        val list = centralApi.listCustomAgentTypes(token)
        if (userIdentityStore.getUserId() == owner) {
            _types.value = list
        }
    }

    suspend fun createType(name: String, command: String): CustomAgentTypeDto {
        val owner = userIdentityStore.getUserId()
        val token = userIdentityStore.requireAccessToken()
        val created = centralApi.createCustomAgentType(token, name, command)
        if (userIdentityStore.getUserId() == owner) {
            applyUpserted(created)
        }
        return created
    }

    suspend fun updateType(
        typeId: String,
        name: String,
        command: String,
    ): CustomAgentTypeDto {
        val owner = userIdentityStore.getUserId()
        val token = userIdentityStore.requireAccessToken()
        val updated = centralApi.updateCustomAgentType(
            token,
            typeId,
            UpdateCustomAgentTypeRequest(name = name, command = command),
        )
        if (userIdentityStore.getUserId() == owner) {
            applyUpserted(updated)
        }
        return updated
    }

    suspend fun deleteType(typeId: String) {
        val owner = userIdentityStore.getUserId()
        val token = userIdentityStore.requireAccessToken()
        centralApi.deleteCustomAgentType(token, typeId)
        if (userIdentityStore.getUserId() == owner) {
            applyDeleted(typeId)
        }
    }

    /** Insert or replace a type in the cache by `id`. */
    fun applyUpserted(type: CustomAgentTypeDto) {
        _types.update { current ->
            val idx = current.indexOfFirst { it.id == type.id }
            if (idx == -1) current + type
            else current.toMutableList().also { it[idx] = type }
        }
    }

    fun applyDeleted(typeId: String) {
        _types.update { current ->
            current.filterNot { it.id == typeId }
        }
    }

    fun clear() {
        _types.value = emptyList()
        synchronized(orderLock) {
            serverModeOrder = SessionModeOrderDto()
            pendingModeOrder = null
            publishModeOrderLocked()
        }
    }
}
