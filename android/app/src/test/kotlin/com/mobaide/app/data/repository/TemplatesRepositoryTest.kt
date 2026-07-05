package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.TemplateDto
import com.mobaide.app.data.network.dto.UpdateTemplateRequest
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * Unit tests for [TemplatesRepository] -- exercises the [CentralApi] delegation,
 * user-token requirement via [UserIdentityStore], in-memory caching of the
 * template list exposed through [observeTemplates], and the cache mutations
 * triggered both by REST round-trips and by `applyUpserted` / `applyDeleted`
 * (the entrypoints called by `UserEventBus` for inbound `template.*` events).
 *
 * Mirrors the [AgentRepositoryTest] shape: MockK collaborators, `runTest { }`,
 * Turbine for StateFlow assertions. The repository delegates dispatchers to
 * the caller, so plain `runTest { }` is sufficient.
 */
class TemplatesRepositoryTest {

    // ---- Helpers ---------------------------------------------------------------

    private fun template(
        id: String,
        name: String = "tpl-$id",
        body: String = "body-$id",
        favorite: Boolean = false,
        lastUsedAt: String? = null,
        version: Int = 1,
    ): TemplateDto = TemplateDto(
        template_id = id,
        name = name,
        body = body,
        is_favorite = favorite,
        last_used_at = lastUsedAt,
        created_at = "2026-04-30T12:00:00Z",
        updated_at = "2026-04-30T12:00:00Z",
        version = version,
    )

    private data class Fixture(
        val repo: TemplatesRepository,
        val centralApi: CentralApi,
        val userIdentityStore: UserIdentityStore,
    )

    private fun newFixture(userToken: String? = "tok-user-123"): Fixture {
        val centralApi = mockk<CentralApi>(relaxed = true)
        val userIdentityStore = mockk<UserIdentityStore>()
        every { userIdentityStore.getUserAccessToken() } returns userToken
        return Fixture(
            repo = TemplatesRepository(centralApi, userIdentityStore),
            centralApi = centralApi,
            userIdentityStore = userIdentityStore,
        )
    }

    // ---- observeTemplates initial state ----------------------------------------

    @Test
    fun observeTemplates_initialState_isEmptyList() = runTest {
        val (repo) = newFixture()
        assertTrue(repo.observeTemplates().value.isEmpty())
    }

    // ---- requireUserToken guard ------------------------------------------------

    @Test
    fun refreshTemplates_throwsIllegalStateException_whenUserNotLoggedIn() = runTest {
        val (repo) = newFixture(userToken = null)
        try {
            repo.refreshTemplates()
            fail("expected IllegalStateException")
        } catch (e: IllegalStateException) {
            assertEquals("User not logged in", e.message)
        }
    }

    // ---- refreshTemplates ------------------------------------------------------

    @Test
    fun refreshTemplates_delegatesToCentralApi_andUpdatesFlow() = runTest {
        val (repo, api) = newFixture()
        val templates = listOf(template("t-1"), template("t-2"))
        coEvery { api.listTemplates("tok-user-123") } returns templates

        repo.observeTemplates().test {
            assertEquals(emptyList<TemplateDto>(), awaitItem())

            repo.refreshTemplates()

            assertEquals(templates, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { api.listTemplates("tok-user-123") }
    }

    @Test
    fun refreshTemplates_secondCall_replacesEntireList() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listTemplates(any()) } returns listOf(
            template("t-1"), template("t-2"),
        )

        repo.refreshTemplates()
        assertEquals(2, repo.observeTemplates().value.size)

        coEvery { api.listTemplates(any()) } returns listOf(template("t-3"))
        repo.refreshTemplates()
        assertEquals(
            listOf("t-3"),
            repo.observeTemplates().value.map { it.template_id },
        )
    }

    // ---- createTemplate --------------------------------------------------------

    @Test
    fun createTemplate_delegatesToCentralApi_andReturnsSamePayload() = runTest {
        val (repo, api) = newFixture()
        val response = template("t-1", name = "explain", body = "explain this")
        coEvery {
            api.createTemplate("tok-user-123", "explain", "explain this")
        } returns response

        val returned = repo.createTemplate("explain", "explain this")

        assertSame(response, returned)
        coVerify(exactly = 1) {
            api.createTemplate("tok-user-123", "explain", "explain this")
        }
    }

    @Test
    fun createTemplate_upsertsIntoCache_withoutCallingListAgain() = runTest {
        // The repo does NOT do a follow-up listTemplates() after create — it
        // applies the returned row optimistically into the cache. Behaviour
        // diverges here from AgentRepository, and this test guards that.
        val (repo, api) = newFixture()
        val created = template("t-new", name = "new-tpl")
        coEvery { api.createTemplate(any(), any(), any()) } returns created

        repo.observeTemplates().test {
            assertEquals(emptyList<TemplateDto>(), awaitItem())

            repo.createTemplate("new-tpl", "body")

            val updated = awaitItem()
            assertEquals(listOf("t-new"), updated.map { it.template_id })
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 0) { api.listTemplates(any()) }
    }

    // ---- updateTemplate --------------------------------------------------------

    @Test
    fun updateTemplate_delegatesToCentralApi_withPartialPayload() = runTest {
        val (repo, api) = newFixture()
        val updated = template("t-1", name = "renamed", version = 2)
        val captured = slot<UpdateTemplateRequest>()
        coEvery {
            api.updateTemplate("tok-user-123", "t-1", capture(captured))
        } returns updated

        val returned = repo.updateTemplate("t-1", name = "renamed")

        assertSame(updated, returned)
        // Only `name` was set — the other fields default to null so the server
        // treats them as "no change". This is the contract `TemplatesViewModel`
        // and `TerminalViewModel.toggleFavorite/save` rely on.
        assertEquals("renamed", captured.captured.name)
        assertNull(captured.captured.body)
        assertNull(captured.captured.is_favorite)
    }

    @Test
    fun updateTemplate_favoriteOnly_sendsFavoriteFieldOnly() = runTest {
        // Toggle-favorite path used by `TerminalViewModel.toggleTemplateFavorite`
        // and the picker long-press menu.
        val (repo, api) = newFixture()
        val updated = template("t-1", favorite = true, version = 2)
        val captured = slot<UpdateTemplateRequest>()
        coEvery {
            api.updateTemplate(any(), any(), capture(captured))
        } returns updated

        repo.updateTemplate("t-1", isFavorite = true)

        assertEquals(true, captured.captured.is_favorite)
        assertNull(captured.captured.name)
        assertNull(captured.captured.body)
    }

    @Test
    fun updateTemplate_replacesExistingRowInCache() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listTemplates(any()) } returns listOf(
            template("t-1", name = "old", version = 1),
            template("t-2", name = "untouched", version = 1),
        )
        repo.refreshTemplates()
        val replacement = template("t-1", name = "new", version = 2)
        coEvery { api.updateTemplate(any(), eq("t-1"), any()) } returns replacement

        repo.updateTemplate("t-1", name = "new")

        val cached = repo.observeTemplates().value
        assertEquals(2, cached.size)
        assertEquals("new", cached.first { it.template_id == "t-1" }.name)
        assertEquals(2, cached.first { it.template_id == "t-1" }.version)
        // The other row is unchanged — order-preserving upsert.
        assertEquals("untouched", cached.first { it.template_id == "t-2" }.name)
    }

    // ---- deleteTemplate --------------------------------------------------------

    @Test
    fun deleteTemplate_delegatesToCentralApi_andRemovesFromCache() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listTemplates(any()) } returns listOf(
            template("t-1"), template("t-2"),
        )
        repo.refreshTemplates()
        coEvery { api.deleteTemplate(any(), any()) } returns Unit

        repo.deleteTemplate("t-1")

        coVerify(exactly = 1) { api.deleteTemplate("tok-user-123", "t-1") }
        assertEquals(
            listOf("t-2"),
            repo.observeTemplates().value.map { it.template_id },
        )
    }

    @Test
    fun deleteTemplate_unknownId_isNoOpOnCache() = runTest {
        // The HTTP call still runs (server returns 403/404 in practice) but
        // the local cache is untouched: `applyDeleted` filters by id.
        val (repo, api) = newFixture()
        coEvery { api.listTemplates(any()) } returns listOf(template("t-1"))
        repo.refreshTemplates()
        coEvery { api.deleteTemplate(any(), any()) } returns Unit

        repo.deleteTemplate("ghost-id")

        coVerify(exactly = 1) { api.deleteTemplate("tok-user-123", "ghost-id") }
        assertEquals(listOf("t-1"), repo.observeTemplates().value.map { it.template_id })
    }

    // ---- touchTemplate ---------------------------------------------------------

    @Test
    fun touchTemplate_delegatesToCentralApi_andUpsertsBumpedRow() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listTemplates(any()) } returns listOf(
            template("t-1", lastUsedAt = null, version = 1),
        )
        repo.refreshTemplates()
        val touched = template("t-1", lastUsedAt = "2026-04-30T13:00:00Z", version = 2)
        coEvery { api.touchTemplate("tok-user-123", "t-1") } returns touched

        val returned = repo.touchTemplate("t-1")

        assertSame(touched, returned)
        coVerify(exactly = 1) { api.touchTemplate("tok-user-123", "t-1") }
        val cached = repo.observeTemplates().value.single()
        assertEquals("2026-04-30T13:00:00Z", cached.last_used_at)
        assertEquals(2, cached.version)
    }

    // ---- applyUpserted (push-event entrypoint) ---------------------------------

    @Test
    fun applyUpserted_unknownId_appendsToCache() = runTest {
        // Path triggered when `UserEventBus` parses a `template.created`
        // envelope from a sibling device.
        val (repo) = newFixture()
        repo.applyUpserted(template("t-1"))
        repo.applyUpserted(template("t-2"))

        assertEquals(
            listOf("t-1", "t-2"),
            repo.observeTemplates().value.map { it.template_id },
        )
    }

    @Test
    fun applyUpserted_existingId_replacesInPlace_preservingOrder() = runTest {
        // Path triggered by `template.updated`. The replacement must occupy
        // the same slot, otherwise the picker's "Recents/Favorites/All" sort
        // would reshuffle every time the user starred a template on another
        // device.
        val (repo, api) = newFixture()
        coEvery { api.listTemplates(any()) } returns listOf(
            template("t-1", name = "first"),
            template("t-2", name = "second"),
            template("t-3", name = "third"),
        )
        repo.refreshTemplates()

        repo.applyUpserted(template("t-2", name = "second-edited", version = 7))

        val cached = repo.observeTemplates().value
        assertEquals(listOf("t-1", "t-2", "t-3"), cached.map { it.template_id })
        assertEquals("second-edited", cached[1].name)
        assertEquals(7, cached[1].version)
    }

    @Test
    fun applyUpserted_emitsNewValue_onChange() = runTest {
        val (repo) = newFixture()
        repo.observeTemplates().test {
            assertEquals(emptyList<TemplateDto>(), awaitItem())

            repo.applyUpserted(template("t-1", name = "x"))
            val first = awaitItem()
            assertEquals(1, first.size)

            repo.applyUpserted(template("t-1", name = "y", version = 2))
            val second = awaitItem()
            assertEquals("y", second.single().name)

            cancelAndIgnoreRemainingEvents()
        }
    }

    // ---- applyDeleted (push-event entrypoint) ----------------------------------

    @Test
    fun applyDeleted_removesMatchingRow_keepingOthers() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listTemplates(any()) } returns listOf(
            template("t-1"), template("t-2"), template("t-3"),
        )
        repo.refreshTemplates()

        repo.applyDeleted("t-2")

        assertEquals(
            listOf("t-1", "t-3"),
            repo.observeTemplates().value.map { it.template_id },
        )
    }

    @Test
    fun applyDeleted_unknownId_isNoOp() = runTest {
        // Forward-compat: a `template.deleted` envelope for a row we never
        // fetched (e.g. created and deleted between two of our refreshes)
        // must not throw or alter the cache.
        val (repo, api) = newFixture()
        coEvery { api.listTemplates(any()) } returns listOf(template("t-1"))
        repo.refreshTemplates()

        repo.applyDeleted("ghost-id")

        assertEquals(listOf("t-1"), repo.observeTemplates().value.map { it.template_id })
    }

    @Test
    fun applyDeleted_onEmptyCache_isNoOp() = runTest {
        val (repo) = newFixture()

        repo.applyDeleted("any-id")

        assertEquals(emptyList<TemplateDto>(), repo.observeTemplates().value)
    }

    // ---- clear -----------------------------------------------------------------

    @Test
    fun clear_emptiesCache() = runTest {
        // Used during sign-out flows to drop the previous user's data without
        // a network round-trip.
        val (repo, api) = newFixture()
        coEvery { api.listTemplates(any()) } returns listOf(
            template("t-1"), template("t-2"),
        )
        repo.refreshTemplates()
        assertEquals(2, repo.observeTemplates().value.size)

        repo.clear()

        assertEquals(emptyList<TemplateDto>(), repo.observeTemplates().value)
    }
}
