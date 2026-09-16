package com.mobaide.app.data.repository

import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.GitCommitContextResponse
import com.mobaide.app.data.network.dto.GitCommitRequest
import com.mobaide.app.data.network.dto.GitCommitResponse
import com.mobaide.app.data.network.dto.GitDiscardRequest
import com.mobaide.app.data.network.dto.GitJobResponse
import com.mobaide.app.data.network.dto.GitMutationResponse
import com.mobaide.app.data.network.dto.GitPathsRequest
import com.mobaide.app.data.network.dto.GitPullRequest
import com.mobaide.app.data.network.dto.GitPushRequest
import com.mobaide.app.data.network.dto.VersionInfoResponse
import io.mockk.coEvery
import io.mockk.every
import io.mockk.mockk
import io.mockk.slot
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException

/**
 * The stage / unstage / discard / commit calls of [GitRepository] and the
 * `capabilities.git` probe behind them: the request DTO each builds, and the
 * [SessionGitException] every wire failure is mapped to.
 */
class GitRepositorySessionMutationsTest {

    private fun newRepo(): Pair<GitRepository, RestApi> {
        val restApi = mockk<RestApi>(relaxed = true)
        val bus = mockk<AgentEventBus>()
        every { bus.events } returns MutableSharedFlow()
        return GitRepository(restApi, bus) to restApi
    }

    private fun version(capabilities: String) = VersionInfoResponse(
        name = "mobaide-agent",
        server_version = "1.4.0",
        protocol_versions = listOf(7),
        min_client_version = 1,
        capabilities = Json.parseToJsonElement(capabilities).jsonObject,
    )

    @Test
    fun gitCapabilities_readOnlyAnExplicitTrue() = runTest {
        // Unlike `cliAvailable`, a stringified or absent flag keeps the
        // viewer read-only: a control that would 404 must not appear on a guess.
        val (repo, restApi) = newRepo()
        coEvery { restApi.getAgentVersion() } returns
            version("""{"git":{"changes":true,"stage":true,"discard":"true","commit":false}}""")

        assertEquals(GitCapabilities(stage = true, discard = false, commit = false), repo.gitCapabilities())
    }

    @Test
    fun gitCapabilities_isNone_forAnOlderAgent_orAnUnreadableBlock() = runTest {
        val (repo, restApi) = newRepo()

        coEvery { restApi.getAgentVersion() } returns version("{}")
        assertEquals(GitCapabilities.NONE, repo.gitCapabilities())

        coEvery { restApi.getAgentVersion() } returns version("""{"git":"yes"}""")
        assertEquals(GitCapabilities.NONE, repo.gitCapabilities())

        coEvery { restApi.getAgentVersion() } returns version("""{"git":{"changes":true}}""")
        assertEquals(GitCapabilities.NONE, repo.gitCapabilities())
    }

    @Test
    fun gitCapabilities_mapsAProbeFailure() = runTest {
        val (repo, restApi) = newRepo()
        val down = IOException("down")
        coEvery { restApi.getAgentVersion() } throws down

        try {
            repo.gitCapabilities()
            fail("expected SessionGitException.Network")
        } catch (e: SessionGitException.Network) {
            assertSame(down, e.cause)
        }
    }

    @Test
    fun stageAndUnstage_sendThePathsAsOneRequestEach() = runTest {
        val (repo, restApi) = newRepo()
        val staged = slot<GitPathsRequest>()
        val unstaged = slot<GitPathsRequest>()
        coEvery { restApi.stageSessionGit("s1", capture(staged)) } returns GitMutationResponse()
        coEvery { restApi.unstageSessionGit("s1", capture(unstaged)) } returns GitMutationResponse()

        repo.stageSessionPaths("s1", listOf("src/new name.py", "src/old.py"))
        repo.unstageSessionPaths("s1", listOf("keep.txt"))

        assertEquals(GitPathsRequest(listOf("src/new name.py", "src/old.py")), staged.captured)
        assertEquals(GitPathsRequest(listOf("keep.txt")), unstaged.captured)
    }

    @Test
    fun discard_sendsTheScopeWithThePaths() = runTest {
        val (repo, restApi) = newRepo()
        val sent = slot<GitDiscardRequest>()
        coEvery { restApi.discardSessionGit("s1", capture(sent)) } returns GitMutationResponse()

        repo.discardSessionPaths("s1", listOf("notes.txt"), GitDiscardRequest.SCOPE_UNTRACKED)

        assertEquals(GitDiscardRequest(listOf("notes.txt"), "untracked"), sent.captured)
    }

    @Test
    fun commit_sendsTheMessageAndFlags_andReturnsTheNewHead() = runTest {
        val (repo, restApi) = newRepo()
        val sent = slot<GitCommitRequest>()
        val head = GitCommitResponse("4890423520f4dcd158b85374f44fc8819a813ac2", "4890423", "Fix")
        coEvery { restApi.commitSessionGit("s1", capture(sent)) } returns head

        val result = repo.commitSession("s1", "Fix\n\nWhy.", amend = true, noVerify = true)

        assertSame(head, result)
        assertEquals(GitCommitRequest("Fix\n\nWhy.", amend = true, no_verify = true), sent.captured)
    }

    @Test
    fun sessionCommitContext_passesTheBodyThrough() = runTest {
        val (repo, restApi) = newRepo()
        val context = GitCommitContextResponse(author = null, head = null)
        coEvery { restApi.getSessionGitCommitContext("s1") } returns context

        assertSame(context, repo.sessionCommitContext("s1"))
    }

    @Test
    fun a409Refusal_isAGitError_withItsCodeAndMessage() = runTest {
        val (repo, restApi) = newRepo()
        val cause = ApiException(
            409,
            """{"detail":{"code":"pathspec_mismatch","message":"fatal: pathspec 'x' did not match"}}""",
        )
        coEvery { restApi.stageSessionGit(any(), any()) } throws cause

        try {
            repo.stageSessionPaths("s1", listOf("x"))
            fail("expected SessionGitException.GitError")
        } catch (e: SessionGitException.GitError) {
            assertEquals(SessionGitException.CODE_PATHSPEC_MISMATCH, e.code)
            assertEquals("fatal: pathspec 'x' did not match", e.detailMessage)
            assertSame(cause, e.cause)
        }
    }

    @Test
    fun anOldAgent404_onACommit_isAgentOutdated() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.commitSessionGit(any(), any()) } throws
            ApiException(404, """{"detail":"Unknown proxy route"}""")

        try {
            repo.commitSession("s1", "x", amend = false, noVerify = false)
            fail("expected SessionGitException.AgentOutdated")
        } catch (e: SessionGitException) {
            assertTrue(e is SessionGitException.AgentOutdated)
        }
    }
}

/** The job half of [GitRepository]'s capability probe and start calls. */
class GitRepositorySessionJobStartTest {

    private fun newRepo(): Pair<GitRepository, RestApi> {
        val restApi = mockk<RestApi>(relaxed = true)
        val bus = mockk<AgentEventBus>()
        every { bus.events } returns MutableSharedFlow()
        return GitRepository(restApi, bus) to restApi
    }

    private fun version(capabilities: String) = VersionInfoResponse(
        name = "mobaide-agent",
        server_version = "1.4.0",
        protocol_versions = listOf(7),
        min_client_version = 1,
        capabilities = Json.parseToJsonElement(capabilities).jsonObject,
    )

    private fun job(op: String) = GitJobResponse(job_id = "j1", session_id = "s1", op = op)

    @Test
    fun gitCapabilities_readTheThreeJobKeys_eachAsAnExplicitTrue() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.getAgentVersion() } returns version(
            """{"git":{"changes":true,"stage":true,"discard":true,"commit":true,"fetch":true,"pull":"true","push":false}}""",
        )

        val caps = repo.gitCapabilities()

        assertEquals(
            GitCapabilities(stage = true, discard = true, commit = true, fetch = true, pull = false, push = false),
            caps,
        )
        assertTrue(caps.hasSync)
    }

    @Test
    fun hasSync_isTrueOnAnyJobKey_andFalseForAStageAndCommitAgent() {
        assertTrue(GitCapabilities(push = true).hasSync)
        assertTrue(GitCapabilities(pull = true).hasSync)
        assertTrue(GitCapabilities(fetch = true).hasSync)
        assertFalse(GitCapabilities(stage = true, discard = true, commit = true).hasSync)
        assertFalse(GitCapabilities.NONE.hasSync)
    }

    @Test
    fun startSessionGitJob_routesEachOp_withItsBody() = runTest {
        val (repo, restApi) = newRepo()
        val pulled = slot<GitPullRequest>()
        val pushed = slot<GitPushRequest>()
        coEvery { restApi.fetchSessionGit("s1") } returns job("fetch")
        coEvery { restApi.pullSessionGit("s1", capture(pulled)) } returns job("pull")
        coEvery { restApi.pushSessionGit("s1", capture(pushed)) } returns job("push")

        assertEquals("fetch", repo.startSessionGitJob("s1", GitJobResponse.OP_FETCH).op)
        assertEquals("pull", repo.startSessionGitJob("s1", GitJobResponse.OP_PULL, pullMode = "rebase").op)
        assertEquals("push", repo.startSessionGitJob("s1", GitJobResponse.OP_PUSH, force = true).op)

        assertEquals(GitPullRequest("rebase"), pulled.captured)
        assertEquals(GitPushRequest(force = true), pushed.captured)
    }

    @Test
    fun aJobRunningRefusal_carriesTheActiveJobId() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.fetchSessionGit("s1") } throws
            ApiException(409, """{"detail":{"code":"job_running","message":"ab12cd34"}}""")

        try {
            repo.startSessionGitJob("s1", GitJobResponse.OP_FETCH)
            fail("expected SessionGitException.GitError")
        } catch (e: SessionGitException.GitError) {
            assertEquals(SessionGitException.CODE_JOB_RUNNING, e.code)
            assertEquals("ab12cd34", e.detailMessage)
        }
    }

    @Test
    fun anUnknownJob_onGetOrCancel_isAGitErrorNotAnOutdatedAgent() = runTest {
        val (repo, restApi) = newRepo()
        val body = """{"detail":{"code":"job_not_found"}}"""
        coEvery { restApi.getSessionGitJob("s1", "gone") } throws ApiException(404, body)
        coEvery { restApi.cancelSessionGitJob("s1", "gone") } throws ApiException(404, body)

        for (call in listOf<suspend () -> GitJobResponse>(
            { repo.sessionGitJob("s1", "gone") },
            { repo.cancelSessionGitJob("s1", "gone") },
        )) {
            try {
                call()
                fail("expected SessionGitException.GitError")
            } catch (e: SessionGitException.GitError) {
                assertEquals(SessionGitException.CODE_JOB_NOT_FOUND, e.code)
            }
        }
    }
}
