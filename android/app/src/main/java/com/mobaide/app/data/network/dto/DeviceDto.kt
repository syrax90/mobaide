package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/** One entry in the user's `GET /api/devices` listing. */
@Serializable
data class DeviceResponse(
    val device_id: String,
    val name: String? = null,
    val created_at: String,
    val last_seen_at: String? = null,
)
