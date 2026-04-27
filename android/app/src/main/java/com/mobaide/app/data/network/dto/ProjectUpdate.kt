package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Request body for `PUT /api/projects/{name}`. All fields optional with
 * three-state semantics:
 *  - field omitted from the JSON body → keep existing value
 *  - field sent as `""` (empty string) → clear (store None server-side)
 *  - field sent as a non-empty value → replace
 *
 * For `default_flags`: `null` / omitted = keep, `{}` = clear, non-empty = replace.
 *
 * To make "omitted" distinguishable from "explicit null", this DTO must be
 * encoded with `Json { explicitNulls = false; encodeDefaults = false }` so
 * that Kotlin `null` fields vanish from the wire.
 */
@Serializable
data class ProjectUpdate(
    val name: String? = null,
    val path: String? = null,
    val linear_api_key: String? = null,
    val conda_env: String? = null,
    val default_flags: Map<String, JsonElement>? = null,
)
