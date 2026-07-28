package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

/**
 * Account-scoped custom AI agent type (proto-v7). [id] is 12 lowercase hex
 * chars; the session-kind wire string for this type is `"custom_" + id`.
 */
@Serializable
data class CustomAgentTypeDto(
    val id: String,
    val name: String,
    val command: String,
    val created_at: String,
    val updated_at: String,
)

@Serializable
data class CreateCustomAgentTypeRequest(
    val name: String,
    val command: String,
)

/**
 * PATCH body: omitted fields stay unchanged server-side. The encoder must
 * drop null defaults (CentralApi's Json has `encodeDefaults = false`) —
 * `{"name": null}`, blank values, and `{}` are all rejected with 422; there
 * is no empty-string-clears form (unlike shortcut rename).
 */
@Serializable
data class UpdateCustomAgentTypeRequest(
    val name: String? = null,
    val command: String? = null,
)

/** Payload of `custom_type.deleted` — the key is `id`, not `type_id`. */
@Serializable
data class CustomTypeDeletedPayload(
    val id: String,
)
