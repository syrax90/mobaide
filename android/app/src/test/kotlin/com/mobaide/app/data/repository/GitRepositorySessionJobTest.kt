package com.mobaide.app.data.repository

import app.cash.turbine.test
import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.GitJobResponse
import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.every
import io.mockk.mockk
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import kotlin.time.Duration.Companion.milliseconds

/**
 * One fetch / pull / push job's feed, [GitRepository.observeSessionGitJob]:
 * the socket frames as the fast path, the reconcile poll every interval while
 * foregrounded, and the three ways it ends. Same arrangement as
 * `GitRepositorySessionStatusTest`: the ticker is a `delay` loop on the test
 * scheduler, so time is stepped with `advanceTimeBy` + `runCurrent` and the
 * collector is cancelled before the block ends; [AgentEventBus.events] is a
 * real [MutableSharedFlow] the frames are pushed into.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class GitRepositorySessionJobTest {

    private val pollMs = 3_000L

    private class Fixture(
        val repo: GitRepository,
        val restApi: RestApi,
        val eventFlow: MutableSharedFlow<AgentEvent>,
        val foreground: MutableStateFlow<Boolean>,
    ) {
        var seed: GitJobResponse? = null
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
        )
    }

    private fun Fixture.observe(jobId: String = "j1") = repo.observeSessionGitJob(
        sessionId = "s1",
        jobId = jobId,
        foreground = foreground,
        initial = { seed },
        interval = pollMs.milliseconds,
    )

    private fun job(
        id: String = "j1",
        sessionId: String = "s1",
        op: String = GitJobResponse.OP_PUSH,
        status: String = GitJobResponse.STATUS_RUNNING,
        percent: Int? = null,
        ok: Boolean? = null,
        errorCode: String? = null,
    ) = GitJobResponse(
        job_id = id, session_id = sessionId, op = op, status = status,
        percent = percent, ok = ok, error_code = errorCode,
    )

    private fun done(ok: Boolean = true, errorCode: String? = null) =
        job(status = GitJobResponse.STATUS_DONE, ok = ok, errorCode = errorCode)

    private fun TestScope.tick() {
        advanceTimeBy(pollMs)
        runCurrent()
    }

    private fun loaded(load: SessionGitJobLoad): GitJobResponse =
        (load as SessionGitJobLoad.Loaded).job

    @Test
    fun pollsAtOnce_thenOnEveryTick_andEndsOnceTheJobIsDone() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitJob("s1", "j1") } returns
            job(percent = 10) andThen job(percent = 60) andThen done()

        f.observe().test {
            assertEquals(10, loaded(awaitItem()).percent)
            expectNoEvents()

            tick()
            assertEquals(60, loaded(awaitItem()).percent)

            tick()
            val last = loaded(awaitItem())
            assertFalse(last.running)
            assertEquals(true, last.ok)
            awaitComplete()
        }
        coVerify(exactly = 3) { f.restApi.getSessionGitJob("s1", "j1") }
    }

    @Test
    fun theSeed_isShownFirst_beforeThePollConfirms() = runTest {
        // The start answer is the seed: the banner appears without a request.
        val f = newFixture()
        f.seed = job(percent = 5)
        coEvery { f.restApi.getSessionGitJob("s1", "j1") } returns job(percent = 10)

        f.observe().test {
            assertEquals(5, loaded(awaitItem()).percent)
            assertEquals(10, loaded(awaitItem()).percent)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun aDoneSeed_endsTheFeedWithoutARequest() = runTest {
        val f = newFixture()
        f.seed = done()

        f.observe().test {
            assertFalse(loaded(awaitItem()).running)
            awaitComplete()
        }
        coVerify(exactly = 0) { f.restApi.getSessionGitJob(any(), any()) }
    }

    @Test
    fun aFrameForTheJob_appliesWithoutARequest_andOtherJobsAreIgnored() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitJob("s1", "j1") } returns job(percent = 10)

        f.observe().test {
            assertEquals(10, loaded(awaitItem()).percent)

            f.eventFlow.emit(AgentEvent.GitJobProgress(job(percent = 45)))
            assertEquals(45, loaded(awaitItem()).percent)

            f.eventFlow.emit(AgentEvent.GitJobProgress(job(id = "other", percent = 99)))
            f.eventFlow.emit(AgentEvent.GitJobDone(job(id = "other", status = GitJobResponse.STATUS_DONE)))
            expectNoEvents()

            coVerify(exactly = 1) { f.restApi.getSessionGitJob("s1", "j1") }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun aDoneFrame_endsTheFeed_withTheOutcome() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitJob("s1", "j1") } returns job(percent = 10)

        f.observe().test {
            awaitItem()

            f.eventFlow.emit(AgentEvent.GitJobDone(done(ok = false, errorCode = "rejected")))

            val last = loaded(awaitItem())
            assertFalse(last.running)
            assertEquals("rejected", last.error_code)
            awaitComplete()
        }
    }

    @Test
    fun jobNotFound404_reportsLost_andEndsTheFeed() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitJob("s1", "j1") } throws
            ApiException(404, """{"detail":{"code":"job_not_found"}}""")

        f.observe().test {
            assertEquals(SessionGitJobLoad.Lost, awaitItem())
            awaitComplete()
        }
    }

    @Test
    fun aTransientFailure_keepsTheLastSnapshot_andKeepsPolling() = runTest {
        val f = newFixture()
        coEvery { f.restApi.getSessionGitJob("s1", "j1") } returns job(percent = 10) andThenThrows
            IOException("down") andThen job(percent = 20)

        f.observe().test {
            assertEquals(10, loaded(awaitItem()).percent)

            tick()
            val failure = awaitItem() as SessionGitJobLoad.Failed
            assertEquals(10, failure.cached?.percent)
            assertTrue(failure.error is SessionGitException.Network)

            tick()
            assertEquals(20, loaded(awaitItem()).percent)
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun staysQuietInTheBackground_stillTakesFrames_andPollsAtOnceWhenForegrounded() = runTest {
        val f = newFixture(foreground = false)
        coEvery { f.restApi.getSessionGitJob("s1", "j1") } returns job(percent = 40)

        f.observe().test {
            tick()
            expectNoEvents()
            coVerify(exactly = 0) { f.restApi.getSessionGitJob(any(), any()) }

            f.eventFlow.emit(AgentEvent.GitJobProgress(job(percent = 30)))
            assertEquals(30, loaded(awaitItem()).percent)

            f.foreground.value = true
            runCurrent()
            assertEquals(40, loaded(awaitItem()).percent)
            coVerify(exactly = 1) { f.restApi.getSessionGitJob("s1", "j1") }
            cancelAndIgnoreRemainingEvents()
        }
    }

    @Test
    fun observeSessionGitJobFrames_carriesEveryJobOfTheSession_andNoOther() = runTest {
        val f = newFixture()

        f.repo.observeSessionGitJobFrames("s1").test {
            f.eventFlow.emit(AgentEvent.GitJobProgress(job(id = "j1")))
            f.eventFlow.emit(AgentEvent.GitJobProgress(job(id = "j3", sessionId = "s2")))
            f.eventFlow.emit(AgentEvent.GitJobDone(job(id = "j2", status = GitJobResponse.STATUS_DONE)))

            assertEquals("j1", awaitItem().job_id)
            assertEquals("j2", awaitItem().job_id)
            expectNoEvents()
            cancelAndIgnoreRemainingEvents()
        }
    }
}
