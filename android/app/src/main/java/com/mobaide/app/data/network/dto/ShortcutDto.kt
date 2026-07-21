package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

@Serializable
data class ShortcutDto(
    val shortcut_id: String,
    val kind: String,
    val chord_token: String,
    val name: String? = null,
    val position: Int,
    val created_at: String,
    val updated_at: String,
    val version: Int,
)

@Serializable
data class CreateShortcutRequest(
    val kind: String,
    val chord_token: String,
    val name: String? = null,
)

/**
 * Rename-only update. The encoder omits null-valued defaults, so a null
 * [name] never reaches the wire — send `""` to clear the name (the server
 * treats the empty string as the "clear back to null" sentinel).
 */
@Serializable
data class UpdateShortcutRequest(
    val name: String? = null,
)

@Serializable
data class ShortcutDeletedPayload(
    val shortcut_id: String,
    val version: Int,
    val updated_at: String,
)
