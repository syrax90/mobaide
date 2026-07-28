package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.auth.requireAccessToken
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.UpdateCustomAgentTypeRequest
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

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
 */
class CustomAgentTypesRepository(
    private val centralApi: CentralApi,
    private val userIdentityStore: UserIdentityStore,
) {
    private val _types = MutableStateFlow<List<CustomAgentTypeDto>>(emptyList())

    fun observeTypes(): StateFlow<List<CustomAgentTypeDto>> = _types.asStateFlow()

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
    }
}
