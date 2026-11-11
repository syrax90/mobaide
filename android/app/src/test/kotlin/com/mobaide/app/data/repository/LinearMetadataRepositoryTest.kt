package com.mobaide.app.data.repository

import com.mobaide.app.data.local.PreferencesStore
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.LinearLabelDto
import com.mobaide.app.data.network.dto.LinearStateDto
import io.mockk.Runs
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.just
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Unit tests for [LinearMetadataRepository] — SWR cache contract:
 *   1. Non-empty cache + no force → `Cached` result; no network call.
 *   2. Force refresh → network call; result persisted to cache.
 *   3. Empty cache → network call; result persisted; returned as `Fresh`.
 *   4. Network failure → `Error` result with the cached snapshot attached.
 */
class LinearMetadataRepositoryTest {

    private fun newRepo(): Triple<LinearMetadataRepository, RestApi, PreferencesStore> {
        val restApi = mockk<RestApi>()
        val store = mockk<PreferencesStore>()
        return Triple(LinearMetadataRepository(restApi, store), restApi, store)
    }

    @Test
    fun getLabels_servesCache_whenPresent_andNotForced() = runTest {
        val (repo, restApi, store) = newRepo()
        val cached = listOf(LinearLabelDto("AutoPipeline", "#ff0000"))
        coEvery { store.getCachedLinearLabels("alpha") } returns cached

        val result = repo.getLabels("alpha", forceRefresh = false)

        assertTrue(result is MetadataResult.Cached)
        assertEquals(cached, (result as MetadataResult.Cached).items)
        coVerify(exactly = 0) { restApi.getLinearLabels(any()) }
    }

    @Test
    fun getLabels_emptyCache_fallsThroughToNetwork_andPersists() = runTest {
        val (repo, restApi, store) = newRepo()
        coEvery { store.getCachedLinearLabels("alpha") } returns emptyList()
        val fresh = listOf(LinearLabelDto("AutoPipeline", "#ff0000"))
        coEvery { restApi.getLinearLabels("alpha") } returns fresh
        coEvery { store.setCachedLinearLabels("alpha", fresh) } just Runs

        val result = repo.getLabels("alpha", forceRefresh = false)

        assertTrue(result is MetadataResult.Fresh)
        assertEquals(fresh, (result as MetadataResult.Fresh).items)
        coVerify(exactly = 1) { store.setCachedLinearLabels("alpha", fresh) }
    }

    @Test
    fun getLabels_force_skipsCache_andPersistsFresh() = runTest {
        val (repo, restApi, store) = newRepo()
        coEvery { store.getCachedLinearLabels("alpha") } returns
            listOf(LinearLabelDto("Stale"))
        val fresh = listOf(LinearLabelDto("AutoPipeline"), LinearLabelDto("AI-ready"))
        coEvery { restApi.getLinearLabels("alpha") } returns fresh
        coEvery { store.setCachedLinearLabels("alpha", fresh) } just Runs

        val result = repo.getLabels("alpha", forceRefresh = true)

        assertTrue(result is MetadataResult.Fresh)
        assertEquals(fresh, (result as MetadataResult.Fresh).items)
        coVerify(exactly = 1) { store.setCachedLinearLabels("alpha", fresh) }
    }

    @Test
    fun getLabels_networkError_returnsCachedSnapshotInError() = runTest {
        val (repo, restApi, store) = newRepo()
        val cached = listOf(LinearLabelDto("Stale"))
        coEvery { store.getCachedLinearLabels("alpha") } returns cached
        coEvery { restApi.getLinearLabels("alpha") } throws RuntimeException("net down")

        // Force refresh hits the network, fails — error must carry the cache.
        val result = repo.getLabels("alpha", forceRefresh = true)

        assertTrue(result is MetadataResult.Error)
        val err = result as MetadataResult.Error
        assertEquals("net down", err.message)
        assertEquals(cached, err.cached)
    }

    @Test
    fun getStates_force_persistsAndReturnsFresh() = runTest {
        val (repo, restApi, store) = newRepo()
        coEvery { store.getCachedLinearStates("alpha") } returns emptyList()
        val fresh = listOf(
            LinearStateDto("Proposals", "backlog"),
            LinearStateDto("In Progress", "started"),
            LinearStateDto("Done", "completed"),
        )
        coEvery { restApi.getLinearStates("alpha") } returns fresh
        coEvery { store.setCachedLinearStates("alpha", fresh) } just Runs

        val result = repo.getStates("alpha", forceRefresh = true)

        assertTrue(result is MetadataResult.Fresh)
        assertEquals(fresh, (result as MetadataResult.Fresh).items)
        coVerify(exactly = 1) { store.setCachedLinearStates("alpha", fresh) }
    }
}
