package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.auth.requireAccessToken
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.DeviceResponse

/**
 * Repository for the user's own device-management endpoints
 * (`GET /api/devices`, `DELETE /api/devices/{device_id}`).
 *
 * Routes through [CentralApi] with the user (dashboard) JWT — these
 * endpoints are user-scoped, not agent-scoped, so they intentionally
 * bypass the per-agent [com.mobaide.app.data.network.RestApi].
 */
class DeviceRepository(
    private val centralApi: CentralApi,
    private val userIdentityStore: UserIdentityStore,
) {
    suspend fun listDevices(): List<DeviceResponse> =
        centralApi.listDevices(userIdentityStore.requireAccessToken())

    suspend fun revokeDevice(deviceId: String) {
        centralApi.revokeDevice(userIdentityStore.requireAccessToken(), deviceId)
    }
}
