package com.claudemobile.app.data.repository

import com.claudemobile.app.data.events.ServerEvent
import com.claudemobile.app.data.events.ServerEventBus
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.network.AutoScanWsClient
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.network.dto.AutoScanListResponse
import com.claudemobile.app.data.network.dto.ClaudeOptionsModel
import com.claudemobile.app.data.network.dto.RunNowResponse
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

class AutoScanAlreadyRunningException(val projectName: String) :
    Exception("Auto scan for project '$projectName' is already running")

class AutoScanCapacityException(val maxScans: Int) :
    Exception("Maximum $maxScans concurrent auto scans reached")

/** One-shot UI events emitted by the repository (e.g. rollback errors). */
sealed interface AutoScanUiEvent {
    data class Error(val message: String) : AutoScanUiEvent
}

class AutoScanRepository(
    private val restApi: RestApi,
    private val eventBus: ServerEventBus,
    private val autoScanWsClient: AutoScanWsClient,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _list = MutableStateFlow(
        AutoScanListResponse(scans = emptyList(), capacity = 0, max_scans = 5, available_projects = emptyList())
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
            eventBus.stream<ServerEvent.AutoScanListChanged>().collect { ev ->
                _list.value = ev.list
            }
        }
        scope.launch {
            eventBus.stream<ServerEvent.AutoScanStatus>().collect { ev ->
                val current = _list.value
                val updated = current.scans.map {
                    if (it.project_name == ev.instance.project_name) ev.instance else it
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

    fun observeEvents(): Flow<ServerEvent.AutoScanEvent> =
        eventBus.stream<ServerEvent.AutoScanEvent>()

    fun getAutoScanPrompt(): String? = _list.value.auto_scan_prompt

    /** Throws [AutoScanAlreadyRunningException] on 409, [AutoScanCapacityException] on 429. */
    suspend fun startScan(projectName: String, claudeOptions: ClaudeOptionsModel? = null) {
        val prev = _list.value
        // Optimistic placeholder so the card appears immediately. The
        // server's `autoscan.list.changed` event will overwrite this with
        // authoritative state shortly after.
        if (prev.scans.none { it.project_name == projectName }) {
            val now = System.currentTimeMillis() / 1000.0
            val placeholder = AutoScanInstanceDto(
                project_name = projectName,
                status = "starting",
                started_at = now,
                last_activity_at = now,
            )
            _list.value = prev.copy(scans = prev.scans + placeholder)
        }
        try {
            restApi.startAutoScan(projectName, claudeOptions)
        } catch (e: Exception) {
            _list.value = prev
            val typed: Exception = if (e is ApiException) {
                when (e.code) {
                    409 -> AutoScanAlreadyRunningException(projectName)
                    429 -> AutoScanCapacityException(prev.max_scans)
                    else -> e
                }
            } else e
            _events.tryEmit(AutoScanUiEvent.Error(typed.message ?: "Failed to start auto scan"))
            throw typed
        }
    }

    suspend fun stopScan(projectName: String) {
        val prev = _list.value
        // Optimistic removal so the card disappears within ~50 ms.
        _list.value = prev.copy(
            scans = prev.scans.filterNot { it.project_name == projectName },
        )
        try {
            restApi.stopAutoScan(projectName)
        } catch (e: Exception) {
            _list.value = prev
            _events.tryEmit(AutoScanUiEvent.Error(e.message ?: "Failed to stop auto scan"))
            throw e
        }
    }

    /**
     * Returns the server's response so the caller (typically
     * [com.claudemobile.app.ui.autoscan.AutoScanDetailViewModel]) can
     * rebind its countdown synchronously, without waiting for the bus event.
     */
    suspend fun runNow(projectName: String): RunNowResponse {
        val prev = _list.value
        // Optimistic countdown reset.
        _list.value = prev.copy(
            scans = prev.scans.map {
                if (it.project_name == projectName) it.copy(next_scan_at = null) else it
            },
        )
        return try {
            val response = restApi.runAutoScanNow(projectName)
            // Reconcile from authoritative server response.
            _list.value = _list.value.copy(
                scans = _list.value.scans.map {
                    if (it.project_name == projectName) it.copy(next_scan_at = response.next_scan_at) else it
                },
            )
            response
        } catch (e: Exception) {
            _list.value = prev
            _events.tryEmit(AutoScanUiEvent.Error(e.message ?: "Failed to run scan now"))
            throw e
        }
    }

    /**
     * Open a /ws/autoscan stream subscribed to a single project (so the server
     * also relays heavy `log` events). Caller cancels the flow to disconnect.
     */
    fun observeDetailFrames(
        projectName: String,
        resumeFromSeq: Map<String, Long> = emptyMap(),
    ): Flow<AutoScanWsClient.Frame> =
        autoScanWsClient.connect(
            subscribe = listOf(projectName),
            resumeFromSeq = resumeFromSeq,
        )
}
