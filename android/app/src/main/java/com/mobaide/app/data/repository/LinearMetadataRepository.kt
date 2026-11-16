package com.mobaide.app.data.repository

import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.LinearLabelDto
import com.mobaide.app.data.network.dto.LinearStateDto

/**
 * Result wrapper for stale-while-revalidate metadata loads. The repository
 * emits these in sequence so the UI can render cached chips immediately,
 * then update once the fresh fetch lands (or surface an error banner if
 * the refresh fails while keeping the stale cache visible).
 */
sealed interface MetadataResult<T> {
    data class Cached<T>(val items: List<T>) : MetadataResult<T>
    data class Fresh<T>(val items: List<T>) : MetadataResult<T>
    data class Error<T>(
        val message: String,
        val cached: List<T> = emptyList(),
    ) : MetadataResult<T>
}

/**
 * Per-workspace Linear discovery: labels and workflow states. SWR-cached in
 * DataStore Preferences keyed by workspace name so the workspace form's chip
 * groups paint instantly when the sheet opens, with a background refresh
 * to pick up label/state changes done in Linear.
 */
class LinearMetadataRepository(
    private val restApi: RestApi,
    private val preferencesStore: PreferencesStore,
) {
    /**
     * Fetch labels for the workspace. Reads the cache first; on `forceRefresh`
     * or after an empty cache, hits the network. Network results are
     * persisted before returning.
     */
    suspend fun getLabels(
        workspaceName: String,
        forceRefresh: Boolean = false,
    ): MetadataResult<LinearLabelDto> {
        val cached = preferencesStore.getCachedLinearLabels(workspaceName)
        if (cached.isNotEmpty() && !forceRefresh) {
            return MetadataResult.Cached(cached)
        }
        return try {
            val fresh = restApi.getLinearLabels(workspaceName)
            preferencesStore.setCachedLinearLabels(workspaceName, fresh)
            MetadataResult.Fresh(fresh)
        } catch (e: Exception) {
            MetadataResult.Error(
                message = e.message ?: "Failed to fetch Linear labels",
                cached = cached,
            )
        }
    }

    /**
     * Fetch workflow states for the workspace. Same SWR contract as
     * [getLabels] — see that method for caching semantics.
     */
    suspend fun getStates(
        workspaceName: String,
        forceRefresh: Boolean = false,
    ): MetadataResult<LinearStateDto> {
        val cached = preferencesStore.getCachedLinearStates(workspaceName)
        if (cached.isNotEmpty() && !forceRefresh) {
            return MetadataResult.Cached(cached)
        }
        return try {
            val fresh = restApi.getLinearStates(workspaceName)
            preferencesStore.setCachedLinearStates(workspaceName, fresh)
            MetadataResult.Fresh(fresh)
        } catch (e: Exception) {
            MetadataResult.Error(
                message = e.message ?: "Failed to fetch Linear workflow states",
                cached = cached,
            )
        }
    }

    /**
     * Force a network refresh regardless of cache state. The previous cache
     * is held aside so the UI can keep showing stale data while the new
     * fetch is in flight.
     */
    suspend fun refreshLabels(
        workspaceName: String,
    ): MetadataResult<LinearLabelDto> = getLabels(workspaceName, forceRefresh = true)

    suspend fun refreshStates(
        workspaceName: String,
    ): MetadataResult<LinearStateDto> = getStates(workspaceName, forceRefresh = true)
}
