package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/** Request body for `POST /api/projects`. */
@Serializable
data class ProjectWrite(
    val name: String,
    val path: String,
    val linear_api_key: String? = null,
    val conda_env: String? = null,
    val default_flags: Map<String, JsonElement> = emptyMap(),
)
