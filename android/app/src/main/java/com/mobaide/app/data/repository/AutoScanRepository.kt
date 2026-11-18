package com.mobaide.app.data.repository

import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.AutoScanWsClient
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.AUTO_SCAN_SOURCE_LINEAR
import com.mobaide.app.data.network.dto.AutoScanCapability
import com.mobaide.app.data.network.dto.AutoScanInstanceDto
import com.mobaide.app.data.network.dto.AutoScanListResponse
import com.mobaide.app.data.network.dto.ClaudeOptionsModel
import com.mobaide.app.data.network.dto.GitHubLabelDto
import com.mobaide.app.data.network.dto.GitHubStatusDto
import com.mobaide.app.data.network.dto.RunNowResponse
import com.mobaide.app.data.network.dto.StartScanRequest
import com.mobaide.app.data.network.dto.autoScanCapability
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

class AutoScanAlreadyRunningException(val workspaceName: String) :
    Exception("Auto scan for workspace '$workspaceName' is already running")

class AutoScanCapacityException(val maxScans: Int) :
    Exception("Maximum $maxScans concurrent auto scans reached")

/** A `400` from the agent: a choice is missing or the source or agent is not usable. */
class AutoScanRejectedException(val detail: String) : Exception(detail)

/** The `detail` of an agent error body, or null when the body has none. */
internal fun apiErrorDetail(body: String): String? = runCatching {
    ((Json.parseToJsonElement(body) as? JsonObject)?.get("detail") as? JsonPrimitive)
        ?.takeIf { it.isString }?.content?.takeIf { it.isNotBlank() }
}.getOrNull()

/** One-shot UI events emitted by the repository (e.g. rollback errors). */
sealed interface AutoScanUiEvent {
    data class Error(val message: String) : AutoScanUiEvent
}

class AutoScanRepository(
    private val restApi: RestApi,
    private val eventBus: AgentEventBus,
    private val autoScanWsClient: AutoScanWsClient,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _list = MutableStateFlow(
        AutoScanListResponse(scans = emptyList(), capacity = 0, max_scans = 5, available_workspaces = emptyList())
    )

    // One-shot events for the UI (Snackbar). replay=0 so we never re-show
    // a stale error after a screen is reopened.
    private val _events = MutableSharedFlow<AutoScanUiEvent>(
        replay = 0,
        extraBufferCapacity = 4,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    val events: SharedFlow<AutoScanUiEvent> = _events.asSharedFlow()

    init {
        scope.launch {
            eventBus.stream<AgentEvent.AutoScanListChanged>().collect { ev ->
                _list.value = ev.list
            }
        }
        scope.launch {
            eventBus.stream<AgentEvent.AutoScanStatus>().collect { ev ->
                val current = _list.value
                val updated = current.scans.map {
                    if (it.workspace_name == ev.instance.workspace_name) ev.instance else it
                }
                _list.value = current.copy(scans = updated)
            }
        }
    }

    fun observeList(): StateFlow<AutoScanListResponse> = _list.asStateFlow()

    fun observeScans(): Flow<List<AutoScanInstanceDto>> =
        _list.map { it.scans }

    suspend fun refresh(): AutoScanListResponse {
        val list = restApi.getAutoScanList()
        _list.value = list
        return list
    }

    fun observeEvents(): Flow<AgentEvent.AutoScanEvent> =
        eventBus.stream<AgentEvent.AutoScanEvent>()

    fun getAutoScanPrompt(): String? = _list.value.auto_scan_prompt

    /** Throws [AutoScanAlreadyRunningException] on 409, [AutoScanCapacityException] on 429. */
    suspend fun startScan(workspaceName: String, claudeOptions: ClaudeOptionsModel? = null) =
        startScan(StartScanRequest(workspaceName, claudeOptions))

    /**
     * Throws [AutoScanAlreadyRunningException] on 409, [AutoScanCapacityException]
     * on 429 and [AutoScanRejectedException] (the agent's `detail`) on 400.
     */
    suspend fun startScan(request: StartScanRequest) {
        val workspaceName = request.workspace_name
        val prev = _list.value
        // Optimistic placeholder so the card appears immediately. The
        // server's `autoscan.list.changed` event will overwrite this with
        // authoritative state shortly after.
        if (prev.scans.none { it.workspace_name == workspaceName }) {
            val now = System.currentTimeMillis() / 1000.0
            val placeholder = AutoScanInstanceDto(
                workspace_name = workspaceName,
                status = "starting",
                started_at = now,
                last_activity_at = now,
                source = request.source ?: AUTO_SCAN_SOURCE_LINEAR,
                cli = request.cli ?: "claude",
            )
            _list.value = prev.copy(scans = prev.scans + placeholder)
        }
        try {
            restApi.startAutoScan(request)
        } catch (e: Exception) {
            _list.value = prev
            val typed: Exception = if (e is ApiException) {
                when (e.code) {
                    409 -> AutoScanAlreadyRunningException(workspaceName)
                    429 -> AutoScanCapacityException(prev.max_scans)
                    400 -> apiErrorDetail(e.errorBody)?.let(::AutoScanRejectedException) ?: e
                    else -> e
                }
            } else e
            _events.tryEmit(AutoScanUiEvent.Error(typed.message ?: "Failed to start auto scan"))
            throw typed
        }
    }

    suspend fun stopScan(workspaceName: String) {
        val prev = _list.value
        // Optimistic removal so the card disappears within ~50 ms.
        _list.value = prev.copy(
            scans = prev.scans.filterNot { it.workspace_name == workspaceName },
        )
        try {
            restApi.stopAutoScan(workspaceName)
        } catch (e: Exception) {
            _list.value = prev
            _events.tryEmit(AutoScanUiEvent.Error(e.message ?: "Failed to stop auto scan"))
            throw e
        }
    }

    /**
     * Trigger an immediate scan and return the server response so the caller can
     * rebind its countdown synchronously, without waiting for the bus event.
     */
    suspend fun runNow(workspaceName: String): RunNowResponse {
        val prev = _list.value
        _list.value = prev.copy(
            scans = prev.scans.map {
                if (it.workspace_name == workspaceName) it.copy(next_scan_at = null) else it
            },
        )
        return try {
            val response = restApi.runAutoScanNow(workspaceName)
            _list.value = _list.value.copy(
                scans = _list.value.scans.map {
                    if (it.workspace_name == workspaceName) it.copy(next_scan_at = response.next_scan_at) else it
                },
            )
            response
        } catch (e: Exception) {
            _list.value = prev
            _events.tryEmit(AutoScanUiEvent.Error(e.message ?: "Failed to run scan now"))
            throw e
        }
    }

    /** `capabilities.autoscan` of the connected agent; null for an agent that predates it. */
    suspend fun capability(): AutoScanCapability? = restApi.getAgentVersion().autoScanCapability()

    /** What a GitHub scan of [workspaceName] still needs; a stateless read of the PC. */
    suspend fun githubStatus(workspaceName: String): GitHubStatusDto =
        restApi.getGitHubStatus(workspaceName)

    /** The labels of the workspace's GitHub repository. */
    suspend fun githubLabels(workspaceName: String): List<GitHubLabelDto> =
        restApi.getGitHubLabels(workspaceName).labels

    /**
     * Open a WebSocket stream subscribed to a single workspace, causing the server
     * to also relay heavy `log` events. Caller cancels the flow to disconnect.
     */
    fun observeDetailFrames(
        workspaceName: String,
        resumeFromSeq: Map<String, Long> = emptyMap(),
    ): Flow<AutoScanWsClient.Frame> =
        autoScanWsClient.connect(
            subscribe = listOf(workspaceName),
            resumeFromSeq = resumeFromSeq,
        )
}
