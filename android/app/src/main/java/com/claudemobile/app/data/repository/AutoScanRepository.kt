package com.claudemobile.app.data.repository

import com.claudemobile.app.data.events.ServerEvent
import com.claudemobile.app.data.events.ServerEventBus
import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.ScanStatusResponse
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

class AutoScanRepository(
    private val restApi: RestApi,
    private val eventBus: ServerEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val _status = MutableStateFlow<ScanStatusResponse?>(null)

    init {
        scope.launch {
            eventBus.stream<ServerEvent.AutoScanStatus>().collect { ev ->
                _status.value = ev.status
            }
        }
    }

    suspend fun getStatus(): ScanStatusResponse = restApi.getAutoScanStatus()

    fun observeStatus(): StateFlow<ScanStatusResponse?> = _status.asStateFlow()

    fun observeEvents(): Flow<ServerEvent.AutoScanEvent> =
        eventBus.stream<ServerEvent.AutoScanEvent>().map { it }

    suspend fun startScan(projectName: String) {
        restApi.startAutoScan(projectName)
    }

    suspend fun stopScan() {
        restApi.stopAutoScan()
    }
}
