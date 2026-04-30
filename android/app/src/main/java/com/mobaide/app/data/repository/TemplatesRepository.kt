package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.network.dto.UpdateTemplateRequest
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/**
 * Repository for per-user prompt templates via the central server. Caches
 * the (non-deleted) template list in-memory and exposes it as a [StateFlow].
 *
 * Cache mutations come from two paths: explicit REST calls made by the UI
 * (`createTemplate`, `updateTemplate`, ...) and inbound `template.*` events
 * delivered over `/ws/user-events` and forwarded by `UserEventBus`.
 */
class TemplatesRepository(
    private val centralApi: CentralApi,
    private val userIdentityStore: UserIdentityStore,
) {
    private val _templates = MutableStateFlow<List<TemplateDto>>(emptyList())

    fun observeTemplates(): StateFlow<List<TemplateDto>> = _templates.asStateFlow()

    suspend fun refreshTemplates() {
        val token = requireUserToken()
        _templates.value = centralApi.listTemplates(token)
    }

    suspend fun createTemplate(name: String, body: String): TemplateDto {
        val token = requireUserToken()
        val created = centralApi.createTemplate(token, name, body)
        applyUpserted(created)
        return created
    }

    suspend fun updateTemplate(
        templateId: String,
        name: String? = null,
        body: String? = null,
        isFavorite: Boolean? = null,
    ): TemplateDto {
        val token = requireUserToken()
        val updated = centralApi.updateTemplate(
            token,
            templateId,
            UpdateTemplateRequest(name = name, body = body, is_favorite = isFavorite),
        )
        applyUpserted(updated)
        return updated
    }

    suspend fun deleteTemplate(templateId: String) {
        val token = requireUserToken()
        centralApi.deleteTemplate(token, templateId)
        applyDeleted(templateId)
    }

    suspend fun touchTemplate(templateId: String): TemplateDto {
        val token = requireUserToken()
        val touched = centralApi.touchTemplate(token, templateId)
        applyUpserted(touched)
        return touched
    }

    /** Insert or replace a template in the cache by `template_id`. Out-of-order
     *  events are tolerated: an older `version` overwrites a newer one only if
     *  the cache entry has been monkey-patched, but in practice `template_id`
     *  identity is sufficient because the server is the single writer. */
    fun applyUpserted(template: TemplateDto) {
        _templates.update { current ->
            val idx = current.indexOfFirst { it.template_id == template.template_id }
            if (idx == -1) current + template
            else current.toMutableList().also { it[idx] = template }
        }
    }

    fun applyDeleted(templateId: String) {
        _templates.update { current ->
            current.filterNot { it.template_id == templateId }
        }
    }

    fun clear() {
        _templates.value = emptyList()
    }

    private fun requireUserToken(): String =
        userIdentityStore.getUserAccessToken()
            ?: throw IllegalStateException("User not logged in")
}
