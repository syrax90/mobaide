package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.GitHeadInfo
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import kotlin.time.Duration.Companion.milliseconds

/**
 * The session Changes poll, [GitRepository.observeSessionGitStatus].
 *
 * The ticker is an endless `delay` loop on the test scheduler, so every test
 * steps time with `advanceTimeBy` + `runCurrent` and cancels the collector
 * before its block ends — `advanceUntilIdle()` would follow the ticker
 * forever. [AgentEventBus.events] is a real [MutableSharedFlow], so
 * `git.changed` pushes can be injected directly.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class GitRepositorySessionStatusTest {

    private val pollMs = 10_000L

    private class Fixture(
        val repo: GitRepository,
        val restApi: RestApi,
        val eventFlow: MutableSharedFlow<AgentEvent>,
        val foreground: MutableStateFlow<Boolean>,
        val refreshRequests: MutableSharedFlow<Unit>,
    ) {
        var projectName: String? = "alpha"
        var seed: GitStatusResponse? = null
    }

    private fun newFixture(foreground: Boolean = true): Fixture {
        val restApi = mockk<RestApi>(relaxed = true)
        val eventFlow = MutableSharedFlow<AgentEvent>(replay = 0, extraBufferCapacity = 64)
        val bus = mockk<AgentEventBus>()
        every { bus.events } returns eventFlow
        return Fixture(
            repo = GitRepository(restApi, bus),
            restApi = restApi,
            eventFlow = eventFlow,
            foreground = MutableStateFlow(foreground),
            refreshRequests = MutableSharedFlow(extraBufferCapacity = 1),
        )
    }

    private fun Fixture.observe() = repo.observeSessionGitStatus(
        sessionId = "s1",
        projectName = { projectName },
        foreground = foreground,
        refreshRequests = refreshRequests,
        initial = { seed },
        interval = pollMs.milliseconds,
    )

    private fun status(
        etag: String,
        toplevel: String = "/repo",
        commonDir: String = "$toplevel/.git",
    ): GitStatusResponse = GitStatusResponse(
        session_id = "s1",
        cwd = toplevel,
        etag = etag,
        repo = GitRepoInfo(
            toplevel = toplevel,
            git_dir = commonDir,
            common_dir = commonDir,
            head = GitHeadInfo(branch = "main"),
        ),
    )

    private fun unchanged(etag: String): GitStatusResponse =
        GitStatusResponse(session_id = "s1", cwd = "/repo", etag = etag, unchanged = true)

    private fun TestScope.tick() {
        advanceTimeBy(pollMs)
        runCurrent()
    }

    private fun loaded(load: SessionGitStatusLoad): GitStatusResponse =
        (load as SessionGitStatusLoad.Loaded).status

    private fun failed(load: SessionGitStatusLoad): SessionGitStatusLoad.Failed =
        load as SessionGitStatusLoad.Failed

    @Test
    fun fetchesAtOnce_thenOnEveryTick() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } returns status("e1")

        f.observe().test {
            assertEquals("e1", loaded(awaitItem()).etag)
            expectNoEvents()

            tick()
            assertEquals("e1", loaded(awaitItem()).etag)
            tick()
            assertEquals("e1", loaded(awaitItem()).etag)

            coVerify(exactly = 3) { f.restApi.getSessionGitStatus("s1", any()) }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun staysQuietInTheBackground_andFetchesAtOnceWhenForegrounded() = runTest {
        val f = newFixture(foreground = false)
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } returns status("e1")

        f.observe().test {
            tick()
            expectNoEvents()
            coVerify(exactly = 0) { f.restApi.getSessionGitStatus(any(), any()) }

            f.foreground.value = true
            runCurrent()

            assertEquals("e1", loaded(awaitItem()).etag)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun backgrounding_stopsTheTicker() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } returns status("e1")

        f.observe().test {
            awaitItem()
            f.foreground.value = false
            runCurrent()

            tick()
            tick()
            expectNoEvents()
            coVerify(exactly = 1) { f.restApi.getSessionGitStatus("s1", any()) }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun manualRefresh_fetchesWithoutWaitingForTheTick_andIsMarkedManual() = runTest {
        val f = newFixture()
        val body = status("e1")
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } returns body

        f.observe().test {
            assertEquals(SessionGitStatusLoad.Loaded(body, manual = false), awaitItem())
            f.refreshRequests.emit(Unit)
            runCurrent()

            assertEquals(SessionGitStatusLoad.Loaded(body, manual = true), awaitItem())
            coVerify(exactly = 2) { f.restApi.getSessionGitStatus("s1", any()) }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun aManualRequestDuringAFetch_isAnsweredByTheNextLoad_notTheOneInFlight() = runTest {
        // The fetch already running when the user pulled may predate the
        // pull; only the load its request actually produced may end the spinner.
        val f = newFixture()
        val inFlight = CompletableDeferred<GitStatusResponse>()
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } coAnswers { inFlight.await() } andThen
            status("e2")

        f.observe().test {
            runCurrent()
            f.refreshRequests.emit(Unit)
            runCurrent()
            expectNoEvents()
            coVerify(exactly = 1) { f.restApi.getSessionGitStatus("s1", any()) }

            inFlight.complete(status("e1"))
            runCurrent()

            assertEquals(SessionGitStatusLoad.Loaded(status("e1"), manual = false), awaitItem())
            assertEquals(SessionGitStatusLoad.Loaded(status("e2"), manual = true), awaitItem())
            coVerify(exactly = 2) { f.restApi.getSessionGitStatus("s1", any()) }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun aManualRefreshThatFails_isStillMarkedManual() = runTest {
        val f = newFixture()
        val first = status("e1")
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } returns first andThenThrows
            IOException("down")

        f.observe().test {
            awaitItem()
            f.refreshRequests.emit(Unit)
            runCurrent()

            val failure = failed(awaitItem())
            assertTrue(failure.manual)
            assertEquals(first, failure.cached)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun theSecondFetch_forwardsTheEtag_andUnchangedKeepsTheCachedBody() = runTest {
        val f = newFixture()
        val first = status("e1")
        coEvery { f.restApi.getSessionGitStatus("s1", null) } returns first
        coEvery { f.restApi.getSessionGitStatus("s1", "e1") } returns unchanged("e1")

        f.observe().test {
            assertEquals(first, loaded(awaitItem()))

            tick()
            // The body with the repo, not the `unchanged` stub without one.
            assertEquals(first, loaded(awaitItem()))

            coVerify(exactly = 1) { f.restApi.getSessionGitStatus("s1", null) }
            coVerify(exactly = 1) { f.restApi.getSessionGitStatus("s1", "e1") }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun aChangedBody_replacesTheCache_andItsEtagGoesOutNext() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitStatus("s1", null) } returns status("e1")
        coEvery { f.restApi.getSessionGitStatus("s1", "e1") } returns status("e2")
        coEvery { f.restApi.getSessionGitStatus("s1", "e2") } returns unchanged("e2")

        f.observe().test {
            assertEquals("e1", loaded(awaitItem()).etag)
            tick()
            assertEquals("e2", loaded(awaitItem()).etag)
            tick()
            assertEquals("e2", loaded(awaitItem()).etag)
            coVerify(exactly = 1) { f.restApi.getSessionGitStatus("s1", "e2") }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun theInitialSeed_isSentAsTheFirstEtag_andShownWhenUnchanged() = runTest {
        // A resubscribe after the ViewModel's sharing timeout must not pay for
        // a full body it already holds.
        val f = newFixture()
        val seed = status("seed")
        f.seed = seed
        coEvery { f.restApi.getSessionGitStatus("s1", "seed") } returns unchanged("seed")

        f.observe().test {
            assertSame(seed, loaded(awaitItem()))
            coVerify(exactly = 0) { f.restApi.getSessionGitStatus("s1", null) }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun gitChanged_forTheSessionProject_refetches() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } returns status("e1")

        f.observe().test {
            awaitItem()
            f.eventFlow.emit(AgentEvent.GitChanged("alpha"))
            runCurrent()

            assertEquals("e1", loaded(awaitItem()).etag)
            coVerify(exactly = 2) { f.restApi.getSessionGitStatus("s1", any()) }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun gitChanged_forAnotherRepo_isIgnored_whileARepoIsCached() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } returns status("e1")

        f.observe().test {
            awaitItem()
            f.eventFlow.emit(
                AgentEvent.GitChanged("beta", toplevel = "/other", commonDir = "/other/.git"),
            )
            runCurrent()

            expectNoEvents()
            coVerify(exactly = 1) { f.restApi.getSessionGitStatus("s1", any()) }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun gitChanged_matchingTheCachedToplevel_refetches_evenUnderAnotherProjectName() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } returns
            status("e1", toplevel = "/repo")

        f.observe().test {
            awaitItem()
            f.eventFlow.emit(AgentEvent.GitChanged("beta", toplevel = "/repo"))
            runCurrent()

            assertTrue(awaitItem() is SessionGitStatusLoad.Loaded)
            coVerify(exactly = 2) { f.restApi.getSessionGitStatus("s1", any()) }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun gitChanged_matchingTheCommonDir_refetches_forALinkedWorktreeWithoutAProject() = runTest {
        // A session spawned in a linked worktree has no project name; the
        // project's own git dir is the only thing the event and the repo share.
        val f = newFixture()
        f.projectName = null
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } returns
            status("e1", toplevel = "/proj/.wt/feature", commonDir = "/proj/.git")

        f.observe().test {
            awaitItem()
            f.eventFlow.emit(
                AgentEvent.GitChanged("alpha", toplevel = "/proj", commonDir = "/proj/.git"),
            )
            runCurrent()

            assertTrue(awaitItem() is SessionGitStatusLoad.Loaded)
            coVerify(exactly = 2) { f.restApi.getSessionGitStatus("s1", any()) }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun anyGitChanged_refetches_whileNoRepoIsCachedYet() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } throws
            IOException("down") andThen status("e1")

        f.observe().test {
            assertTrue(failed(awaitItem()).error is SessionGitException.Network)
            f.eventFlow.emit(AgentEvent.GitChanged("beta", toplevel = "/elsewhere"))
            runCurrent()

            assertEquals("e1", loaded(awaitItem()).etag)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun sessionNotFound404_endsTheFeedWithSessionGone() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } throws
            ApiException(404, """{"detail":"Session 's1' not found"}""")

        f.observe().test {
            val failure = failed(awaitItem())
            assertTrue(failure.error is SessionGitException.SessionGone)
            assertNull(failure.cached)
            awaitComplete()
        }
    }

    @Test
    fun plain404_endsTheFeedWithAgentOutdated() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } throws
            ApiException(404, """{"detail":"Not Found"}""")

        f.observe().test {
            assertTrue(failed(awaitItem()).error is SessionGitException.AgentOutdated)
            awaitComplete()
        }
    }

    @Test
    fun gitTimeout504_reportsTheCode_andKeepsPolling() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } throws
            ApiException(504, """{"detail":{"code":"git_timeout","message":"git status timed out"}}""") andThen
            status("e1")

        f.observe().test {
            val error = failed(awaitItem()).error as SessionGitException.GitError
            assertEquals(SessionGitException.CODE_GIT_TIMEOUT, error.code)
            assertEquals("git status timed out", error.detailMessage)

            tick()
            assertEquals("e1", loaded(awaitItem()).etag)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun ioException_reportsANetworkFailure_withTheCachedBody_andKeepsPolling() = runTest {
        val f = newFixture()
        val first = status("e1")
        coEvery { f.restApi.getSessionGitStatus("s1", any()) } returns first andThenThrows
            IOException("down") andThen status("e2")

        f.observe().test {
            assertEquals(first, loaded(awaitItem()))

            tick()
            val failure = failed(awaitItem())
            assertTrue(failure.error is SessionGitException.Network)
            assertEquals(first, failure.cached)

            tick()
            assertEquals("e2", loaded(awaitItem()).etag)
            cancelAndIgnoreRemainingEvents()
        }
    }
}
