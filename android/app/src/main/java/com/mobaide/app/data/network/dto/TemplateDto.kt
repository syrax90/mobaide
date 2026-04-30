package com.mobaide.app.data.network.dto

import kotlinx.serialization.Serializable

@Serializable
data class TemplateDto(
    val template_id: String,
    val name: String,
    val body: String,
    val is_favorite: Boolean,
    val last_used_at: String? = null,
    val created_at: String,
    val updated_at: String,
    val version: Int,
)

@Serializable
data class CreateTemplateRequest(val name: String, val body: String)

@Serializable
data class UpdateTemplateRequest(
    val name: String? = null,
    val body: String? = null,
    val is_favorite: Boolean? = null,
)

@Serializable
data class TemplateDeletedPayload(
    val template_id: String,
    val version: Int,
    val updated_at: String,
)
