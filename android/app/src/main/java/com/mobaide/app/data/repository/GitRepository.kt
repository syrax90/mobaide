package com.mobaide.app.data.repository

import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.BusyByResponse
import com.mobaide.app.data.network.dto.BusyHolder
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitDeleteRequest
import com.mobaide.app.data.network.dto.GitDeleteResponse
import com.mobaide.app.data.network.dto.GitItem
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject

/**
 * Typed 409 surface for git deletes — the server includes a structured
 * `busy_by` array so the UI can list the holding session(s) / scan(s).
 */
class GitBusyException(val busyBy: List<BusyHolder>) : Exception("Git item busy")

class GitRepository(
    private val restApi: RestApi,
    eventBus: AgentEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _changed = MutableSharedFlow<String>(
        replay = 0,
        extraBufferCapacity = 8,
    )
    /** Emits the project name whenever the server publishes `git.changed`. */
    val changed: SharedFlow<String> = _changed.asSharedFlow()

    private val errJson = Json { ignoreUnknownKeys = true; isLenient = true }

    init {
        scope.launch {
            eventBus.stream<AgentEvent.GitChanged>().collect { event ->
                _changed.emit(event.projectName)
            }
        }
    }

    suspend fun listGitItems(projectName: String): List<GitItem> =
        restApi.getGitItems(projectName).items

    suspend fun deleteGitItems(
        projectName: String,
        items: List<GitDeleteItem>,
    ): GitDeleteResponse =
        mapBusy { restApi.deleteGitItems(projectName, GitDeleteRequest(items)) }

    private inline fun <T> mapBusy(block: () -> T): T =
        try {
            block()
        } catch (e: ApiException) {
            if (e.code == 409) throw GitBusyException(parseBusyBy(e.errorBody))
            throw e
        }

    private fun parseBusyBy(body: String): List<BusyHolder> {
        if (body.isBlank()) return emptyList()
        return try {
            // The server packs BusyByResponse into FastAPI's `detail` field.
            val root = errJson.parseToJsonElement(body) as? JsonObject ?: return emptyList()
            val detail = root["detail"] as? JsonObject ?: return emptyList()
            errJson.decodeFromJsonElement(BusyByResponse.serializer(), detail).busy_by
        } catch (_: Exception) {
            emptyList()
        }
    }
}
