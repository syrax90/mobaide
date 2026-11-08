package com.mobaide.app.data.repository

import com.mobaide.app.data.auth.UserIdentityStore
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.CentralApi
import com.mobaide.app.data.network.dto.SessionModeOrderDto
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import java.util.Collections
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/**
 * The mode-picker order half of [CustomAgentTypesRepository]: the server
 * snapshot from `GET /api/session-mode-order` and the
 * `session_mode_order.updated` push (older versions dropped), the optimistic
 * order a save shows while it is in flight, the serial order of saves, the
 * revert on failure, the account-generation guard, and the reset on sign-out.
 *
 * Saves run on the repository's own `Dispatchers.Default` scope, so a held
 * PUT is a [CompletableDeferred] the test completes.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class CustomAgentTypesRepositoryModeOrderTest {

    private fun order(version: Int, vararg ids: String) =
        SessionModeOrderDto(order = ids.toList(), version = version, updated_at = "2026-11-04T10:00:00Z")

    private data class Fixture(
        val repo: CustomAgentTypesRepository,
        val centralApi: CentralApi,
        val userIdentityStore: UserIdentityStore,
    )

    private fun newFixture(): Fixture {
        val centralApi = mockk<CentralApi>()
        val userIdentityStore = mockk<UserIdentityStore>()
        every { userIdentityStore.getUserAccessToken() } returns "tok-user-123"
        every { userIdentityStore.getUserId() } returns "user-1"
        return Fixture(
            repo = CustomAgentTypesRepository(centralApi, userIdentityStore),
            centralApi = centralApi,
            userIdentityStore = userIdentityStore,
        )
    }

    @Test
    fun observeModeOrder_initialState_isEmpty() {
        val (repo) = newFixture()
        assertEquals(emptyList<String>(), repo.observeModeOrder().value)
    }

    @Test
    fun refreshModeOrder_showsTheServerOrder() = runTest {
        val (repo, api) = newFixture()
        coEvery { api.getSessionModeOrder("tok-user-123") } returns order(2, "shell", "claude")

        repo.refreshModeOrder()

        assertEquals(listOf("shell", "claude"), repo.observeModeOrder().value)
    }

    @Test
    fun refreshModeOrder_userChangedMidFlight_skipsTheWrite() = runTest {
        val (repo, api, identity) = newFixture()
        every { identity.getUserId() } returnsMany listOf("user-1", "user-2")
        coEvery { api.getSessionModeOrder(any()) } returns order(2, "shell")

        repo.refreshModeOrder()

        assertTrue(repo.observeModeOrder().value.isEmpty())
    }

    @Test
    fun applyModeOrder_olderVersion_isDropped() {
        // A push and the GET it raced can land in either order.
        val (repo) = newFixture()
        repo.applyModeOrder(order(3, "codex"))

        repo.applyModeOrder(order(2, "shell"))

        assertEquals(listOf("codex"), repo.observeModeOrder().value)
    }

    @Test
    fun saveModeOrder_showsTheNewOrderAtOnce_thenTheServerAnswer() = runTest {
        val (repo, api) = newFixture()
        repo.applyModeOrder(order(1, "claude", "codex"))
        val answer = CompletableDeferred<SessionModeOrderDto>()
        coEvery { api.putSessionModeOrder("tok-user-123", listOf("codex", "claude")) } coAnswers {
            answer.await()
        }

        val save = async { repo.saveModeOrder(listOf("codex", "claude")) }
        runCurrent()

        assertEquals(listOf("codex", "claude"), repo.observeModeOrder().value)

        answer.complete(order(2, "codex", "claude"))
        save.await()

        assertEquals(listOf("codex", "claude"), repo.observeModeOrder().value)
    }

    @Test
    fun saveModeOrder_failure_revertsToTheServerOrder_andRethrows() = runTest {
        val (repo, api) = newFixture()
        repo.applyModeOrder(order(1, "shell", "claude"))
        coEvery { api.putSessionModeOrder(any(), any()) } throws ApiException(503, "")

        try {
            repo.saveModeOrder(listOf("claude", "shell"))
            fail("expected ApiException")
        } catch (e: ApiException) {
            assertEquals(503, e.code)
        }

        assertEquals(listOf("shell", "claude"), repo.observeModeOrder().value)
    }

    @Test
    fun saveModeOrder_laterDrop_isNeitherOvertakenNorFlickeredBack() = runTest {
        // Two quick drops: the second PUT waits for the first, and the first's
        // answer must not briefly show its older order over the newer drop.
        val (repo, api) = newFixture()
        val sent = Collections.synchronizedList(mutableListOf<List<String>>())
        val firstSent = CompletableDeferred<Unit>()
        val firstAnswer = CompletableDeferred<SessionModeOrderDto>()
        coEvery { api.putSessionModeOrder(any(), listOf("codex", "claude", "shell")) } coAnswers {
            sent += secondArg<List<String>>()
            firstSent.complete(Unit)
            firstAnswer.await()
        }
        coEvery { api.putSessionModeOrder(any(), listOf("shell", "codex", "claude")) } coAnswers {
            sent += secondArg<List<String>>()
            order(2, "shell", "codex", "claude")
        }

        val first = async { repo.saveModeOrder(listOf("codex", "claude", "shell")) }
        runCurrent()
        firstSent.await()
        val second = async { repo.saveModeOrder(listOf("shell", "codex", "claude")) }
        runCurrent()

        assertEquals(listOf("shell", "codex", "claude"), repo.observeModeOrder().value)
        assertEquals(listOf(listOf("codex", "claude", "shell")), sent.toList())

        firstAnswer.complete(order(1, "codex", "claude", "shell"))
        first.await()
        assertEquals(listOf("shell", "codex", "claude"), repo.observeModeOrder().value)

        second.await()
        assertEquals(
            listOf(listOf("codex", "claude", "shell"), listOf("shell", "codex", "claude")),
            sent.toList(),
        )
        assertEquals(listOf("shell", "codex", "claude"), repo.observeModeOrder().value)
    }

    @Test
    fun saveModeOrder_twoDropsBehindASaveInFlight_reachTheServerInCallOrder() = runTest {
        // The server bumps the version per PUT, as the real one does, so a
        // later drop that reached it first would lose to the earlier one.
        val (repo, api) = newFixture()
        val a = listOf("codex", "claude", "shell")
        val b = listOf("shell", "codex", "claude")
        val c = listOf("claude", "shell", "codex")
        val sent = Collections.synchronizedList(mutableListOf<List<String>>())
        val firstSent = CompletableDeferred<Unit>()
        val releaseFirst = CompletableDeferred<Unit>()
        coEvery { api.putSessionModeOrder(any(), any()) } coAnswers {
            val body = secondArg<List<String>>()
            val version = synchronized(sent) {
                sent += body
                sent.size
            }
            if (version == 1) {
                firstSent.complete(Unit)
                releaseFirst.await()
            }
            order(version, *body.toTypedArray())
        }

        val first = async { repo.saveModeOrder(a) }
        runCurrent()
        firstSent.await()
        val second = async { repo.saveModeOrder(b) }
        val third = async { repo.saveModeOrder(c) }
        runCurrent()

        releaseFirst.complete(Unit)
        first.await()
        second.await()
        third.await()

        assertEquals(listOf(a, b, c), sent.toList())
        assertEquals(c, repo.observeModeOrder().value)
    }

    @Test
    fun saveModeOrder_userChangedMidFlight_doesNotKeepTheAnswer() = runTest {
        val (repo, api, identity) = newFixture()
        every { identity.getUserId() } returnsMany listOf("user-1", "user-2")
        coEvery { api.putSessionModeOrder(any(), any()) } returns order(4, "codex")

        repo.saveModeOrder(listOf("codex"))

        assertTrue(repo.observeModeOrder().value.isEmpty())
    }

    @Test
    fun saveModeOrder_queuedWhenTheAccountChanges_sendsNothing() = runTest {
        // The queued PUT would carry the next account's token.
        val (repo, api, identity) = newFixture()
        val userId = AtomicReference("user-1")
        every { identity.getUserId() } answers { userId.get() }
        val sent = Collections.synchronizedList(mutableListOf<List<String>>())
        val firstSent = CompletableDeferred<Unit>()
        val firstAnswer = CompletableDeferred<SessionModeOrderDto>()
        coEvery { api.putSessionModeOrder(any(), any()) } coAnswers {
            sent += secondArg<List<String>>()
            firstSent.complete(Unit)
            firstAnswer.await()
        }

        val first = async { repo.saveModeOrder(listOf("codex", "claude")) }
        runCurrent()
        firstSent.await()
        val second = async { repo.saveModeOrder(listOf("shell", "codex")) }
        runCurrent()
        userId.set("user-2")
        firstAnswer.complete(order(1, "codex", "claude"))
        first.await()
        second.await()

        assertEquals(listOf(listOf("codex", "claude")), sent.toList())
        assertTrue(repo.observeModeOrder().value.isEmpty())
    }

    @Test
    fun clear_resetsTheOrder_soTheNextAccountsFirstVersionApplies() {
        val (repo) = newFixture()
        repo.applyModeOrder(order(5, "codex"))

        repo.clear()
        assertTrue(repo.observeModeOrder().value.isEmpty())

        repo.applyModeOrder(order(1, "shell"))
        assertEquals(listOf("shell"), repo.observeModeOrder().value)
    }
}
