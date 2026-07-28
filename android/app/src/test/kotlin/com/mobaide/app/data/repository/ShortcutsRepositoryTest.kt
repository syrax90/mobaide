package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.ShortcutDto
import com.mobaide.app.data.network.dto.UpdateShortcutRequest
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
 * Unit tests for [ShortcutsRepository] — the saved-shortcut sibling of
 * [TemplatesRepository]. Exercises the [CentralApi] delegation, user-token
 * requirement via [UserIdentityStore], in-memory caching of the shortcut list
 * exposed through [ShortcutsRepository.observeShortcuts], and the cache
 * mutations triggered both by REST round-trips and by `applyUpserted` /
 * `applyDeleted` (the entrypoints called by `UserEventBus` for inbound
 * `shortcut.*` events).
 *
 * Mirrors the [TemplatesRepositoryTest] shape: MockK collaborators,
 * `runTest { }`, Turbine for StateFlow assertions.
 */
class ShortcutsRepositoryTest {

    private fun shortcut(
        id: String,
        kind: String = "claude",
        chordToken: String = "ctrl+$id",
        name: String? = null,
        position: Int = 0,
        version: Int = 1,
    ): ShortcutDto = ShortcutDto(
        shortcut_id = id,
        kind = kind,
        chord_token = chordToken,
        name = name,
        position = position,
        created_at = "2026-07-20T12:00:00Z",
        updated_at = "2026-07-20T12:00:00Z",
        version = version,
    )

    private data class Fixture(
        val repo: ShortcutsRepository,
        val centralApi: CentralApi,
        val userIdentityStore: UserIdentityStore,
    )

    private fun newFixture(userToken: String? = "tok-user-123"): Fixture {
        val centralApi = mockk<CentralApi>(relaxed = true)
        val userIdentityStore = mockk<UserIdentityStore>()
        every { userIdentityStore.getUserAccessToken() } returns userToken
        // Owner-guard reads: every REST path captures the user id before the
        // call and re-checks it after (see the owner-guard tests below).
        every { userIdentityStore.getUserId() } returns "user-1"
        return Fixture(
            repo = ShortcutsRepository(centralApi, userIdentityStore),
            centralApi = centralApi,
            userIdentityStore = userIdentityStore,
        )
    }

    @Test
    fun observeShortcuts_initialState_isEmptyList() = runTest {
        val (repo) = newFixture()
        assertTrue(repo.observeShortcuts().value.isEmpty())
    }

    @Test
    fun refreshShortcuts_throwsIllegalStateException_whenUserNotLoggedIn() = runTest {
        val (repo) = newFixture(userToken = null)
        try {
            repo.refreshShortcuts()
            fail("expected IllegalStateException")
        } catch (e: IllegalStateException) {
            assertEquals("User not logged in", e.message)
        }
    }

    @Test
    fun refreshShortcuts_delegatesToCentralApi_andUpdatesFlow() = runTest {
        val (repo, api) = newFixture()
        val shortcuts = listOf(shortcut("s-1"), shortcut("s-2"))
        coEvery { api.listShortcuts("tok-user-123") } returns shortcuts

        repo.observeShortcuts().test {
            assertEquals(emptyList<ShortcutDto>(), awaitItem())

            repo.refreshShortcuts()

            assertEquals(shortcuts, awaitItem())
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 1) { api.listShortcuts("tok-user-123") }
    }

    @Test
    fun refreshShortcuts_secondCall_replacesEntireList() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listShortcuts(any()) } returns listOf(
            shortcut("s-1"), shortcut("s-2"),
        )

        repo.refreshShortcuts()
        assertEquals(2, repo.observeShortcuts().value.size)

        coEvery { api.listShortcuts(any()) } returns listOf(shortcut("s-3"))
        repo.refreshShortcuts()
        assertEquals(
            listOf("s-3"),
            repo.observeShortcuts().value.map { it.shortcut_id },
        )
    }

    @Test
    fun createShortcut_delegatesToCentralApi_andReturnsSamePayload() = runTest {
        val (repo, api) = newFixture()
        val response = shortcut("s-1", kind = "codex", chordToken = "ctrl+k", name = "Klear")
        coEvery {
            api.createShortcut("tok-user-123", "codex", "ctrl+k", "Klear")
        } returns response

        val returned = repo.createShortcut("codex", "ctrl+k", "Klear")

        assertSame(response, returned)
        coVerify(exactly = 1) {
            api.createShortcut("tok-user-123", "codex", "ctrl+k", "Klear")
        }
    }

    @Test
    fun createShortcut_upsertsIntoCache_withoutCallingListAgain() = runTest {
        // The repo does NOT do a follow-up listShortcuts() after create — it
        // applies the returned row optimistically into the cache, exactly
        // like TemplatesRepository.
        val (repo, api) = newFixture()
        val created = shortcut("s-new")
        coEvery { api.createShortcut(any(), any(), any(), any()) } returns created

        repo.observeShortcuts().test {
            assertEquals(emptyList<ShortcutDto>(), awaitItem())

            repo.createShortcut("claude", "ctrl+s-new", null)

            val updated = awaitItem()
            assertEquals(listOf("s-new"), updated.map { it.shortcut_id })
            cancelAndIgnoreRemainingEvents()
        }
        coVerify(exactly = 0) { api.listShortcuts(any()) }
    }

    @Test
    fun createShortcut_serverUpsertReturningExistingRow_replacesInPlace() = runTest {
        // POST /api/shortcuts is an upsert: re-saving a live (kind, chord)
        // may return an already-cached row with a new name/version. The
        // cache must replace it in place, never grow a duplicate button.
        val (repo, api) = newFixture()
        coEvery { api.listShortcuts(any()) } returns listOf(
            shortcut("s-1", chordToken = "ctrl+x", name = "Old"),
            shortcut("s-2", chordToken = "ctrl+y"),
        )
        repo.refreshShortcuts()
        val upserted = shortcut("s-1", chordToken = "ctrl+x", name = "New", version = 2)
        coEvery { api.createShortcut(any(), any(), any(), any()) } returns upserted

        repo.createShortcut("claude", "ctrl+x", "New")

        val cached = repo.observeShortcuts().value
        assertEquals(listOf("s-1", "s-2"), cached.map { it.shortcut_id })
        assertEquals("New", cached[0].name)
        assertEquals(2, cached[0].version)
    }

    @Test
    fun renameShortcut_delegatesToCentralApi_andUpsertsBumpedRow() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listShortcuts(any()) } returns listOf(
            shortcut("s-1", name = "Old"),
        )
        repo.refreshShortcuts()
        val renamed = shortcut("s-1", name = "New", version = 2)
        val captured = slot<UpdateShortcutRequest>()
        coEvery {
            api.updateShortcut("tok-user-123", "s-1", capture(captured))
        } returns renamed

        val returned = repo.renameShortcut("s-1", "New")

        assertSame(renamed, returned)
        assertEquals("New", captured.captured.name)
        assertEquals("New", repo.observeShortcuts().value.single().name)
    }

    @Test
    fun renameShortcut_emptyName_sendsEmptyStringClearSentinel() = runTest {
        // The encoder omits null-valued defaults, so `""` is the only way to
        // express "clear the name" on the wire — the request must carry it
        // verbatim, not null.
        val (repo, api) = newFixture()
        val cleared = shortcut("s-1", name = null, version = 2)
        val captured = slot<UpdateShortcutRequest>()
        coEvery {
            api.updateShortcut(any(), any(), capture(captured))
        } returns cleared

        repo.renameShortcut("s-1", "")

        assertEquals("", captured.captured.name)
    }

    @Test
    fun deleteShortcut_delegatesToCentralApi_andRemovesFromCache() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listShortcuts(any()) } returns listOf(
            shortcut("s-1"), shortcut("s-2"),
        )
        repo.refreshShortcuts()
        coEvery { api.deleteShortcut(any(), any()) } returns Unit

        repo.deleteShortcut("s-1")

        coVerify(exactly = 1) { api.deleteShortcut("tok-user-123", "s-1") }
        assertEquals(
            listOf("s-2"),
            repo.observeShortcuts().value.map { it.shortcut_id },
        )
    }

    @Test
    fun applyUpserted_unknownId_appendsToCache() = runTest {
        // Path triggered when `UserEventBus` parses a `shortcut.created`
        // envelope from a sibling device.
        val (repo) = newFixture()
        repo.applyUpserted(shortcut("s-1"))
        repo.applyUpserted(shortcut("s-2"))

        assertEquals(
            listOf("s-1", "s-2"),
            repo.observeShortcuts().value.map { it.shortcut_id },
        )
    }

    @Test
    fun applyUpserted_existingId_replacesInPlace_preservingOrder() = runTest {
        // Path triggered by `shortcut.updated` (e.g. a rename on another
        // device) — the replacement must occupy the same slot.
        val (repo, api) = newFixture()
        coEvery { api.listShortcuts(any()) } returns listOf(
            shortcut("s-1"),
            shortcut("s-2", name = "Old"),
            shortcut("s-3"),
        )
        repo.refreshShortcuts()

        repo.applyUpserted(shortcut("s-2", name = "New", version = 7))

        val cached = repo.observeShortcuts().value
        assertEquals(listOf("s-1", "s-2", "s-3"), cached.map { it.shortcut_id })
        assertEquals("New", cached[1].name)
        assertEquals(7, cached[1].version)
    }

    @Test
    fun applyDeleted_removesMatchingRow_keepingOthers() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listShortcuts(any()) } returns listOf(
            shortcut("s-1"), shortcut("s-2"), shortcut("s-3"),
        )
        repo.refreshShortcuts()

        repo.applyDeleted("s-2")

        assertEquals(
            listOf("s-1", "s-3"),
            repo.observeShortcuts().value.map { it.shortcut_id },
        )
    }

    @Test
    fun applyDeleted_unknownId_isNoOp() = runTest {
        // Forward-compat: a `shortcut.deleted` envelope for a row we never
        // fetched must not throw or alter the cache.
        val (repo, api) = newFixture()
        coEvery { api.listShortcuts(any()) } returns listOf(shortcut("s-1"))
        repo.refreshShortcuts()

        repo.applyDeleted("ghost-id")

        assertEquals(
            listOf("s-1"),
            repo.observeShortcuts().value.map { it.shortcut_id },
        )
    }

    @Test
    fun purgeKind_removesEveryShortcutOfThatKind_keepingOthers() = runTest {
        // Client half of the server-side cascade on `custom_type.deleted`:
        // the type's shortcuts are soft-deleted server-side without
        // per-shortcut events, so the cache purges by kind locally.
        val (repo, api) = newFixture()
        coEvery { api.listShortcuts(any()) } returns listOf(
            shortcut("s-1", kind = "claude"),
            shortcut("s-2", kind = "custom_ab12cd34ef56"),
            shortcut("s-3", kind = "custom_ab12cd34ef56"),
            shortcut("s-4", kind = "codex"),
        )
        repo.refreshShortcuts()

        repo.purgeKind("custom_ab12cd34ef56")

        assertEquals(
            listOf("s-1", "s-4"),
            repo.observeShortcuts().value.map { it.shortcut_id },
        )
    }

    @Test
    fun purgeKind_unknownKind_isNoOp() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.listShortcuts(any()) } returns listOf(shortcut("s-1"))
        repo.refreshShortcuts()

        repo.purgeKind("custom_never_seen")

        assertEquals(
            listOf("s-1"),
            repo.observeShortcuts().value.map { it.shortcut_id },
        )
    }

    // ---- Account-generation owner guard -------------------------------------

    @Test
    fun refreshShortcuts_userChangedMidFlight_skipsCacheWrite() = runTest {
        // The user id is captured before the call and re-read after: a
        // response landing after an account switch must not repopulate the
        // new account's cache with the old account's rows.
        val (repo, api, identity) = newFixture()
        every { identity.getUserId() } returnsMany listOf("user-1", "user-2")
        coEvery { api.listShortcuts(any()) } returns listOf(shortcut("s-stale"))

        repo.refreshShortcuts()

        assertTrue(repo.observeShortcuts().value.isEmpty())
    }

    @Test
    fun createShortcut_userChangedMidFlight_returnsPayload_withoutCacheWrite() = runTest {
        // The server response still goes back to the caller either way —
        // only the cache write is discarded.
        val (repo, api, identity) = newFixture()
        every { identity.getUserId() } returnsMany listOf("user-1", "user-2")
        val created = shortcut("s-stale")
        coEvery { api.createShortcut(any(), any(), any(), any()) } returns created

        val returned = repo.createShortcut("claude", "ctrl+x", null)

        assertSame(created, returned)
        assertTrue(repo.observeShortcuts().value.isEmpty())
    }

    @Test
    fun clear_emptiesCache() = runTest {
        // Used during sign-out flows to drop the previous user's data without
        // a network round-trip.
        val (repo, api) = newFixture()
        coEvery { api.listShortcuts(any()) } returns listOf(
            shortcut("s-1"), shortcut("s-2"),
        )
        repo.refreshShortcuts()
        assertEquals(2, repo.observeShortcuts().value.size)

        repo.clear()

        assertEquals(emptyList<ShortcutDto>(), repo.observeShortcuts().value)
    }
}
