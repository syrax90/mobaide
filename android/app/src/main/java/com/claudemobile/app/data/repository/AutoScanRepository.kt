package com.claudemobile.app.data.repository

import com.claudemobile.app.data.network.RestApi
import com.claudemobile.app.data.network.dto.ScanStatusResponse

class AutoScanRepository(
    private val restApi: RestApi,
) {
    suspend fun getStatus(): ScanStatusResponse {
        return restApi.getAutoScanStatus()
    }

    suspend fun startScan(projectName: String) {
        restApi.startAutoScan(projectName)
    }

    suspend fun stopScan() {
        restApi.stopAutoScan()
    }
}
