package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.CustomAgentTypeDto
import com.mobaide.app.data.network.dto.UpdateCustomAgentTypeRequest
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Unit tests for [CustomAgentTypesRepository] — the custom-agent-type sibling
 * of [ShortcutsRepository]. Exercises the [CentralApi] delegation, user-token
 * requirement via [UserIdentityStore], in-memory caching of the type list
 * exposed through [CustomAgentTypesRepository.observeTypes], the cache
 * mutations triggered both by REST round-trips and by `applyUpserted` /
 * `applyDeleted` (the entrypoints called by `UserEventBus` for inbound
 * `custom_type.*` events), and the account-generation owner guard that keeps
 * a request resolving after logout/account-switch from repopulating the
 * previous account's cache.
 *
 * Mirrors the [ShortcutsRepositoryTest] shape: MockK collaborators,
 * `runTest { }`, Turbine for StateFlow assertions.
 */
class CustomAgentTypesRepositoryTest {

    private fun type(
        id: String,
        name: String = "Agent $id",
        command: String = "run-$id",
    ): CustomAgentTypeDto = CustomAgentTypeDto(
        id = id,
        name = name,
        command = command,
        created_at = "2026-07-28T12:00:00Z",
        updated_at = "2026-07-28T12:00:00Z",
    )

    private data class Fixture(
        val repo: CustomAgentTypesRepository,
        val centralApi: CentralApi,
        val userIdentityStore: UserIdentityStore,
    )

    private fun newFixture(userToken: String? = "tok-user-123"): Fixture {
        val centralApi = mockk<CentralApi>(relaxed = true)
        val userIdentityStore = mockk<UserIdentityStore>()
        every { userIdentityStore.getUserAccessToken() } returns userToken
        every { userIdentityStore.getUserId() } returns "user-1"
        return Fixture(
            repo = CustomAgentTypesRepository(centralApi, userIdentityStore),
            centralApi = centralApi,
            userIdentityStore = userIdentityStore,
        )
    }

    @Test
    fun observeTypes_initialState_isEmptyList() = runTest {
        val (repo) = newFixture()
        assertTrue(repo.observeTypes().value.isEmpty())
    }

    @Test
    fun refreshTypes_throwsIllegalStateException_whenUserNotLoggedIn() = runTest {
        val (repo) = newFixture(userToken = null)
        try {
            repo.refreshTypes()
            fail("expected IllegalStateException")
        } catch (e: IllegalStateException) {
            assertEquals("User not logged in", e.message)
        }
    }

    @Test
    fun refreshTypes_delegatesToCentralApi_andUpdatesFlow() = runTest {
        val (repo, api) = newFixture()
        val types = listOf(type("t-1"), type("t-2"))
        coEvery { api.listCustomAgentTypes("tok-user-123") } returns types

        repo.observeTypes().test {
            assertEquals(emptyList<CustomAgentTypeDto>(), awaitItem())

            repo.refreshTypes()

            assertEquals(types, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { api.listCustomAgentTypes("tok-user-123") }
    }

    @Test
    fun createType_delegatesToCentralApi_upsertsIntoCache_andReturnsPayload() = runTest {
        val (repo, api) = newFixture()
        val created = type("t-new", name = "Aider", command = "aider")
        coEvery { api.createCustomAgentType("tok-user-123", "Aider", "aider") } returns created

        val returned = repo.createType("Aider", "aider")

        assertSame(created, returned)
        assertEquals(listOf("t-new"), repo.observeTypes().value.map { it.id })
        coVerify(exactly = 0) { api.listCustomAgentTypes(any()) }
    }

    @Test
    fun updateType_alwaysSendsBothFields_andReplacesCachedRowInPlace() = runTest {
        // The PATCH body must carry both trimmed fields every time: `{}` is a
        // 422 server-side and the repository does no dirty-tracking.
        val (repo, api) = newFixture()
        coEvery { api.listCustomAgentTypes(any()) } returns listOf(
            type("t-1", name = "Old"), type("t-2"),
        )
        repo.refreshTypes()
        val updated = type("t-1", name = "New", command = "new-cmd")
        val captured = slot<UpdateCustomAgentTypeRequest>()
        coEvery {
            api.updateCustomAgentType("tok-user-123", "t-1", capture(captured))
        } returns updated

        val returned = repo.updateType("t-1", "New", "new-cmd")

        assertSame(updated, returned)
        assertEquals("New", captured.captured.name)
        assertEquals("new-cmd", captured.captured.command)
        val cached = repo.observeTypes().value
        assertEquals(listOf("t-1", "t-2"), cached.map { it.id })
        assertEquals("New", cached[0].name)
    }

    @Test
    fun deleteType_delegatesToCentralApi_andRemovesFromCache() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listCustomAgentTypes(any()) } returns listOf(type("t-1"), type("t-2"))
        repo.refreshTypes()
        coEvery { api.deleteCustomAgentType(any(), any()) } returns Unit

        repo.deleteType("t-1")

        coVerify(exactly = 1) { api.deleteCustomAgentType("tok-user-123", "t-1") }
        assertEquals(listOf("t-2"), repo.observeTypes().value.map { it.id })
    }

    @Test
    fun applyUpserted_unknownId_appends_existingId_replacesInPlace() = runTest {
        // Paths triggered when `UserEventBus` parses `custom_type.created` /
        // `custom_type.updated` envelopes from a sibling device.
        val (repo) = newFixture()
        repo.applyUpserted(type("t-1"))
        repo.applyUpserted(type("t-2", name = "Old"))
        repo.applyUpserted(type("t-3"))

        repo.applyUpserted(type("t-2", name = "Renamed"))

        val cached = repo.observeTypes().value
        assertEquals(listOf("t-1", "t-2", "t-3"), cached.map { it.id })
        assertEquals("Renamed", cached[1].name)
    }

    @Test
    fun applyDeleted_removesMatchingRow_andUnknownIdIsNoOp() = runTest {
        val (repo) = newFixture()
        repo.applyUpserted(type("t-1"))
        repo.applyUpserted(type("t-2"))

        repo.applyDeleted("t-1")
        repo.applyDeleted("ghost-id")

        assertEquals(listOf("t-2"), repo.observeTypes().value.map { it.id })
    }

    @Test
    fun clear_emptiesCache() = runTest {
        val (repo) = newFixture()
        repo.applyUpserted(type("t-1"))

        repo.clear()

        assertEquals(emptyList<CustomAgentTypeDto>(), repo.observeTypes().value)
    }

    // ---- Account-generation owner guard -------------------------------------

    @Test
    fun refreshTypes_userChangedMidFlight_skipsCacheWrite() = runTest {
        // getUserId is captured before the call and re-read after: a response
        // landing after an account switch must not repopulate the new
        // account's cache with the old account's rows.
        val (repo, api, identity) = newFixture()
        every { identity.getUserId() } returnsMany listOf("user-1", "user-2")
        coEvery { api.listCustomAgentTypes(any()) } returns listOf(type("t-stale"))

        repo.refreshTypes()

        assertTrue(repo.observeTypes().value.isEmpty())
    }

    @Test
    fun createType_userChangedMidFlight_returnsPayload_withoutCacheWrite() = runTest {
        // The server response still goes back to the caller either way — only
        // the cache write is discarded.
        val (repo, api, identity) = newFixture()
        every { identity.getUserId() } returnsMany listOf("user-1", "user-2")
        val created = type("t-stale")
        coEvery { api.createCustomAgentType(any(), any(), any()) } returns created

        val returned = repo.createType("Aider", "aider")

        assertSame(created, returned)
        assertTrue(repo.observeTypes().value.isEmpty())
    }
}
