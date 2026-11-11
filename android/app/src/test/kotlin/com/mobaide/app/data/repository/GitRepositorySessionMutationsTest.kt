package com.mobaide.app.data.repository

import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.GitCommitRequest
import com.mobaide.app.data.network.dto.GitCommitResponse
import com.mobaide.app.data.network.dto.GitConflictResolveRequest
import com.mobaide.app.data.network.dto.GitConflictResponse
import com.mobaide.app.data.network.dto.GitConflictRestoreRequest
import com.mobaide.app.data.network.dto.GitConflictTakeRequest
import com.mobaide.app.data.network.dto.GitDiscardRequest
import com.mobaide.app.data.network.dto.GitJobResponse
import com.mobaide.app.data.network.dto.GitMutationResponse
import com.mobaide.app.data.network.dto.GitPathsRequest
import com.mobaide.app.data.network.dto.GitPullRequest
import com.mobaide.app.data.network.dto.GitPushRequest
import com.mobaide.app.data.network.dto.GitRebaseRequest
import com.mobaide.app.data.network.dto.GitRebaseResponse
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
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

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
    fun gitCapabilities_readRebaseAndResolve_eachAsAnExplicitTrue() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.getAgentVersion() } returns
            version("""{"git":{"changes":true,"stage":true,"rebase":true,"resolve":"true"}}""")

        assertEquals(GitCapabilities(stage = true, rebase = true, resolve = false), repo.gitCapabilities())

        coEvery { restApi.getAgentVersion() } returns
            version("""{"git":{"changes":true,"stage":true,"resolve":true}}""")

        assertEquals(GitCapabilities(stage = true, rebase = false, resolve = true), repo.gitCapabilities())
    }

    @Test
    fun rebaseSession_sendsTheBase_andStepSessionRebase_namesTheStep() = runTest {
        val (repo, restApi) = newRepo()
        val sent = slot<GitRebaseRequest>()
        val stopped = GitRebaseResponse(GitRebaseResponse.OUTCOME_STOPPED)
        val aborted = GitRebaseResponse(GitRebaseResponse.OUTCOME_ABORTED)
        coEvery { restApi.rebaseSessionGit("s1", capture(sent)) } returns stopped
        coEvery { restApi.stepSessionGitRebase("s1", "abort") } returns aborted

        assertSame(stopped, repo.rebaseSession("s1", "origin/main"))
        assertSame(aborted, repo.stepSessionRebase("s1", GitRepository.REBASE_ABORT))

        assertEquals(GitRebaseRequest("origin/main"), sent.captured)
        assertEquals(
            listOf("continue", "skip", "abort"),
            listOf(GitRepository.REBASE_CONTINUE, GitRepository.REBASE_SKIP, GitRepository.REBASE_ABORT),
        )
    }

    @Test
    fun anOldAgent404_onARebase_isAgentOutdated() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.rebaseSessionGit(any(), any()) } throws
            ApiException(404, """{"detail":"Unknown proxy route"}""")

        try {
            repo.rebaseSession("s1", "main")
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

/** The conflict resolver's half of [GitRepository]: its capability key, the request each call builds and the refusals. */
class GitRepositorySessionConflictTest {

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
    fun gitCapabilities_readConflicts_asAnExplicitTrue_notImpliedByResolve() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.getAgentVersion() } returns
            version("""{"git":{"changes":true,"conflicts":true}}""")

        assertEquals(GitCapabilities(conflicts = true), repo.gitCapabilities())

        coEvery { restApi.getAgentVersion() } returns
            version("""{"git":{"changes":true,"rebase":true,"resolve":true,"conflicts":"true"}}""")

        assertEquals(GitCapabilities(rebase = true, resolve = true, conflicts = false), repo.gitCapabilities())
    }

    @Test
    fun resolveTakeAndRestore_buildTheirRequests() = runTest {
        val (repo, restApi) = newRepo()
        val resolved = mutableListOf<GitConflictResolveRequest>()
        val taken = slot<GitConflictTakeRequest>()
        val restored = mutableListOf<GitConflictRestoreRequest>()
        coEvery { restApi.resolveSessionGitConflict("s1", capture(resolved)) } returns GitMutationResponse()
        coEvery { restApi.takeSessionGitConflict("s1", capture(taken)) } returns GitMutationResponse()
        coEvery { restApi.restoreSessionGitConflict("s1", capture(restored)) } returns GitMutationResponse()

        repo.resolveSessionConflict("s1", "src/login.py", "t1", "merged\n", markResolved = true)
        repo.resolveSessionConflict("s1", "src/login.py", "t2", "<<<<<<< x\n", markResolved = false)
        repo.takeSessionConflict("s1", "docs/old.md", GitConflictResponse.SIDE_THEIRS)
        repo.restoreSessionConflict("s1", "src/session.py")
        repo.restoreSessionConflict("s1", "src/login.py", GitConflictRestoreRequest.STYLE_DIFF3, "t1")

        assertEquals(
            listOf(
                GitConflictResolveRequest("src/login.py", "t1", "merged\n", mark_resolved = true),
                GitConflictResolveRequest("src/login.py", "t2", "<<<<<<< x\n", mark_resolved = false),
            ),
            resolved,
        )
        assertEquals(GitConflictTakeRequest("docs/old.md", "theirs"), taken.captured)
        assertEquals(
            listOf(
                GitConflictRestoreRequest("src/session.py", "merge", null),
                GitConflictRestoreRequest("src/login.py", "diff3", "t1"),
            ),
            restored,
        )
    }

    @Test
    fun aConflictRefusal_isAGitError_withTheCodeAndThePathItNames() = runTest {
        val (repo, restApi) = newRepo()
        coEvery { restApi.resolveSessionGitConflict(any(), any()) } throws
            ApiException(409, """{"detail":{"code":"file_changed","message":"src/login.py"}}""")
        coEvery { restApi.getSessionGitConflict(any(), any()) } throws
            ApiException(409, """{"detail":{"code":"not_conflicted","message":"src/login.py"}}""")
        coEvery { restApi.restoreSessionGitConflict(any(), any()) } throws
            ApiException(400, """{"detail":{"code":"bad_path"}}""")

        val refusals = listOf<Pair<String, suspend () -> Unit>>(
            SessionGitException.CODE_FILE_CHANGED to
                { repo.resolveSessionConflict("s1", "src/login.py", "t1", "x", markResolved = true) },
            SessionGitException.CODE_NOT_CONFLICTED to { repo.sessionConflict("s1", "src/login.py") },
            "bad_path" to { repo.restoreSessionConflict("s1", "src/login.py") },
        )
        for ((code, call) in refusals) {
            try {
                call()
                fail("expected SessionGitException.GitError for $code")
            } catch (e: SessionGitException.GitError) {
                assertEquals(code, e.code)
                assertEquals(if (code == "bad_path") null else "src/login.py", e.detailMessage)
            }
        }
    }
}
