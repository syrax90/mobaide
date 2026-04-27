package com.mobaide.app.data.repository

import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.ProjectInfo
import com.mobaide.app.data.network.dto.ProjectOrderRequest
import com.mobaide.app.data.network.dto.ProjectUpdate
import com.mobaide.app.data.network.dto.ProjectWrite
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
class ProjectConflictException(val detail: String) : Exception(detail)

class ProjectsRepository(
    private val restApi: RestApi,
    private val eventBus: AgentEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val _projects = MutableStateFlow<List<ProjectInfo>>(emptyList())

    // Lenient parser for `{"detail": "..."}` error bodies — used only to
    // extract the human-readable message. Never used for success decoding.
    private val errJson = Json { ignoreUnknownKeys = true; isLenient = true }

    init {
        scope.launch {
            eventBus.stream<AgentEvent.ProjectsChanged>().collect { ev ->
                _projects.value = ev.projects
            }
        }
    }

    /** One-shot fetch (still used as a fallback seed if the bus snapshot hasn't arrived). */
    suspend fun listProjects(): List<ProjectInfo> = restApi.getProjects()

    fun observeProjects(): StateFlow<List<ProjectInfo>> = _projects.asStateFlow()

    // None of these mutate `_projects` directly: the server emits a
    // `projects.changed` event after every successful mutation, which the
    // init-block collector above applies to the StateFlow. This keeps the
    // client strictly server-authoritative for create/update/delete.

    suspend fun createProject(body: ProjectWrite): ProjectInfo =
        mapConflict { restApi.createProject(body) }

    suspend fun updateProject(currentName: String, body: ProjectUpdate): ProjectInfo =
        mapConflict { restApi.updateProject(currentName, body) }

    suspend fun deleteProject(name: String) {
        mapConflict { restApi.deleteProject(name) }
    }

    suspend fun reorderProjects(names: List<String>): List<ProjectInfo> =
        mapConflict { restApi.reorderProjects(ProjectOrderRequest(names)) }

    private inline fun <T> mapConflict(block: () -> T): T =
        try {
            block()
        } catch (e: ApiException) {
            if (e.code == 409) throw ProjectConflictException(parseDetail(e.errorBody))
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
