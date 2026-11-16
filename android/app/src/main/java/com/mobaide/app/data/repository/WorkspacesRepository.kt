package com.mobaide.app.data.repository

import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.WorkspaceInfo
import com.mobaide.app.data.network.dto.WorkspaceOrderRequest
import com.mobaide.app.data.network.dto.WorkspaceUpdate
import com.mobaide.app.data.network.dto.WorkspaceWrite
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonPrimitive

/** Typed 409 surface so the UI can distinguish rename/collision/in-use from
 *  unrelated exceptions. `detail` is the human-readable server message. */
class WorkspaceConflictException(val detail: String) : Exception(detail)

class WorkspacesRepository(
    private val restApi: RestApi,
    private val eventBus: AgentEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val _workspaces = MutableStateFlow<List<WorkspaceInfo>>(emptyList())

    // Lenient parser for `{"detail": "..."}` error bodies — used only to
    // extract the human-readable message. Never used for success decoding.
    private val errJson = Json { ignoreUnknownKeys = true; isLenient = true }

    init {
        scope.launch {
            eventBus.stream<AgentEvent.WorkspacesChanged>().collect { ev ->
                _workspaces.value = ev.workspaces
            }
        }
    }

    /** One-shot fetch (still used as a fallback seed if the bus snapshot hasn't arrived). */
    suspend fun listWorkspaces(): List<WorkspaceInfo> = restApi.getWorkspaces()

    fun observeWorkspaces(): StateFlow<List<WorkspaceInfo>> = _workspaces.asStateFlow()

    // None of these mutate `_workspaces` directly: the server emits a
    // `projects.changed` event after every successful mutation, which the
    // init-block collector above applies to the StateFlow. This keeps the
    // client strictly server-authoritative for create/update/delete.

    suspend fun createWorkspace(body: WorkspaceWrite): WorkspaceInfo =
        mapConflict { restApi.createWorkspace(body) }

    suspend fun updateWorkspace(currentName: String, body: WorkspaceUpdate): WorkspaceInfo =
        mapConflict { restApi.updateWorkspace(currentName, body) }

    suspend fun deleteWorkspace(name: String) {
        mapConflict { restApi.deleteWorkspace(name) }
    }

    suspend fun reorderWorkspaces(names: List<String>): List<WorkspaceInfo> =
        mapConflict { restApi.reorderWorkspaces(WorkspaceOrderRequest(names)) }

    private inline fun <T> mapConflict(block: () -> T): T =
        try {
            block()
        } catch (e: ApiException) {
            if (e.code == 409) throw WorkspaceConflictException(parseDetail(e.errorBody))
            throw e
        }

    private fun parseDetail(body: String): String {
        if (body.isBlank()) return "Conflict"
        return try {
            val obj = errJson.parseToJsonElement(body) as? JsonObject
            obj?.get("detail")?.jsonPrimitive?.contentOrNull ?: body
        } catch (_: Exception) {
            body
        }
    }
}
