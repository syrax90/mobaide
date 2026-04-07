package com.claudemobile.app.data.repository

import com.claudemobile.app.data.events.ServerEvent
import com.claudemobile.app.data.events.ServerEventBus
import com.claudemobile.app.data.network.ApiException
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.AutoScanInstanceDto
import com.claudemobile.app.data.network.dto.AutoScanListResponse
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

class AutoScanAlreadyRunningException(val projectName: String) :
    Exception("Auto scan for project '$projectName' is already running")

class AutoScanCapacityException(val maxScans: Int) :
    Exception("Maximum $maxScans concurrent auto scans reached")

class AutoScanRepository(
    private val restApi: RestApi,
    private val eventBus: ServerEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _list = MutableStateFlow(
        AutoScanListResponse(scans = emptyList(), capacity = 0, max_scans = 5, available_projects = emptyList())
    )

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

    /** Throws [AutoScanAlreadyRunningException] on 409, [AutoScanCapacityException] on 429. */
    suspend fun startScan(projectName: String) {
        try {
            restApi.startAutoScan(projectName)
        } catch (e: ApiException) {
            when (e.code) {
                409 -> throw AutoScanAlreadyRunningException(projectName)
                429 -> throw AutoScanCapacityException(_list.value.max_scans)
                else -> throw e
            }
        }
    }

    suspend fun stopScan(projectName: String) {
        restApi.stopAutoScan(projectName)
    }
}
