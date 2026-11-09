package com.mobaide.app.data.repository

import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.BusyByResponse
import com.mobaide.app.data.network.dto.BusyHolder
import com.mobaide.app.data.network.dto.GitCommitContextResponse
import com.mobaide.app.data.network.dto.GitCommitRequest
import com.mobaide.app.data.network.dto.GitCommitResponse
import com.mobaide.app.data.network.dto.GitConflictResolveRequest
import com.mobaide.app.data.network.dto.GitConflictResponse
import com.mobaide.app.data.network.dto.GitConflictRestoreRequest
import com.mobaide.app.data.network.dto.GitConflictTakeRequest
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitCheckoutRequest
import com.mobaide.app.data.network.dto.GitBranchResponse
import com.mobaide.app.data.network.dto.GitBranchCreateRequest
import com.mobaide.app.data.network.dto.GitDeleteRequest
import com.mobaide.app.data.network.dto.GitDeleteResponse
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.data.network.dto.GitDiscardRequest
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.network.dto.GitJobResponse
import com.mobaide.app.data.network.dto.GitPathsRequest
import com.mobaide.app.data.network.dto.GitPullRequest
import com.mobaide.app.data.network.dto.GitPushRequest
import com.mobaide.app.data.network.dto.GitRebaseRequest
import com.mobaide.app.data.network.dto.GitRebaseResponse
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.VersionInfoResponse
import com.mobaide.app.data.network.dto.WorktreeBasesResponse
import com.mobaide.app.data.network.dto.gitCapability
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.emitAll
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.transformWhile
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlin.time.Duration
import kotlin.time.Duration.Companion.seconds

/**
 * Typed 409 surface for git deletes — the server includes a structured
 * `busy_by` array so the UI can list the holding session(s) / scan(s).
 */
class GitBusyException(val busyBy: List<BusyHolder>) : Exception("Git item busy")

/**
 * One answer of the session Changes poll; [cached] is the last full body seen.
 * [manual] is true when a caller's explicit refresh request was among the
 * triggers coalesced into this fetch — a pull-to-refresh queued behind an
 * in-flight tick is answered by the *next* load, not the one already running.
 */
sealed interface SessionGitStatusLoad {
    val cached: GitStatusResponse?
    val manual: Boolean

    data class Loaded(
        val status: GitStatusResponse,
        override val manual: Boolean = false,
    ) : SessionGitStatusLoad {
        override val cached: GitStatusResponse get() = status
    }

    data class Failed(
        override val cached: GitStatusResponse?,
        val error: Throwable,
        override val manual: Boolean = false,
    ) : SessionGitStatusLoad
}

private enum class PollTrigger { Tick, Push, Manual }

/**
 * One answer of a fetch / pull / push job's feed: a snapshot from a socket
 * frame or the reconcile poll, a transient poll failure with the last snapshot
 * kept, or the agent no longer knowing the job at all.
 */
sealed interface SessionGitJobLoad {
    data class Loaded(val job: GitJobResponse) : SessionGitJobLoad
    data class Failed(val cached: GitJobResponse?, val error: Throwable) : SessionGitJobLoad
    data object Lost : SessionGitJobLoad
}

private sealed interface JobSignal {
    data object Tick : JobSignal
    data class Frame(val job: GitJobResponse) : JobSignal
}

/**
 * The agent's `capabilities.git` mutation keys. Every mutating control hangs
 * off an explicit `true`, so an older agent keeps a read-only Changes viewer.
 */
data class GitCapabilities(
    val stage: Boolean = false,
    val discard: Boolean = false,
    val commit: Boolean = false,
    val fetch: Boolean = false,
    val pull: Boolean = false,
    val push: Boolean = false,
    /** The project Git screen's remote rows, New branch and Check out. */
    val branches: Boolean = false,
    /** *Rebase…* and the Changes screen's Continue / Skip / Abort. */
    val rebase: Boolean = false,
    /** `stage` refuses a conflicted file that still holds markers, so *Mark resolved* is safe. */
    val resolve: Boolean = false,
    /** The project Git screen may delete remote-tracking rows (on the remote). */
    val deleteRemote: Boolean = false,
    /** The conflict resolver's routes and `resolved_paths` on status. */
    val conflicts: Boolean = false,
) {
    /** The *Fetch / Pull / Push…* entry and the Sync button exist on any of the three. */
    val hasSync: Boolean get() = fetch || pull || push

    companion object {
        val NONE = GitCapabilities()
    }
}

fun VersionInfoResponse.toGitCapabilities(): GitCapabilities = GitCapabilities(
    stage = gitCapability("stage"),
    discard = gitCapability("discard"),
    commit = gitCapability("commit"),
    fetch = gitCapability("fetch"),
    pull = gitCapability("pull"),
    push = gitCapability("push"),
    branches = gitCapability("branches"),
    rebase = gitCapability("rebase"),
    resolve = gitCapability("resolve"),
    deleteRemote = gitCapability("delete_remote"),
    conflicts = gitCapability("conflicts"),
)

class GitRepository(
    private val restApi: RestApi,
    private val eventBus: AgentEventBus,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    private val _changed = MutableSharedFlow<String>(
        replay = 0,
        extraBufferCapacity = 8,
    )
    /** Emits the project name whenever the server publishes `git.changed`. */
    val changed: SharedFlow<String> = _changed.asSharedFlow()

    init {
        scope.launch {
            eventBus.stream<AgentEvent.GitChanged>().collect { event ->
                _changed.emit(event.projectName)
            }
        }
    }

    suspend fun listGitItems(projectName: String): List<GitItem> =
        restApi.getGitItems(projectName).items

    suspend fun worktreeBases(projectName: String): WorktreeBasesResponse =
        restApi.getWorktreeBases(projectName)

    suspend fun deleteGitItems(
        projectName: String,
        items: List<GitDeleteItem>,
    ): GitDeleteResponse =
        mapBusy { restApi.deleteGitItems(projectName, GitDeleteRequest(items)) }

    suspend fun createBranch(
        projectName: String,
        request: GitBranchCreateRequest,
    ): GitBranchResponse =
        mapBranchRoute { restApi.createGitBranch(projectName, request) }

    /** `ref` is a row's `branch` verbatim — a local name or `<remote>/<name>`. */
    suspend fun checkout(projectName: String, ref: String, force: Boolean = false): GitBranchResponse =
        mapBranchRoute { restApi.checkoutGitRef(projectName, GitCheckoutRequest(ref, force)) }

    suspend fun sessionGitStatus(sessionId: String, etag: String?): GitStatusResponse =
        mapSessionGit { restApi.getSessionGitStatus(sessionId, etag) }

    suspend fun sessionGitDiff(
        sessionId: String,
        path: String,
        scope: String,
        context: Int,
        origPath: String?,
    ): GitDiffResponse =
        mapSessionGit { restApi.getSessionGitDiff(sessionId, path, scope, context, origPath) }

    /** Fresh per-call probe of `GET /api/version`, like the mode picker's CLI probe. */
    suspend fun gitCapabilities(): GitCapabilities = mapSessionGit {
        restApi.getAgentVersion().toGitCapabilities()
    }

    /** Start a fetch, pull or push; the answer is the job's first snapshot, not its result. */
    suspend fun startSessionGitJob(
        sessionId: String,
        op: String,
        pullMode: String = GitPullRequest.MODE_FF_ONLY,
        force: Boolean = false,
    ): GitJobResponse = mapSessionGit {
        when (op) {
            GitJobResponse.OP_FETCH -> restApi.fetchSessionGit(sessionId)
            GitJobResponse.OP_PULL -> restApi.pullSessionGit(sessionId, GitPullRequest(pullMode))
            else -> restApi.pushSessionGit(sessionId, GitPushRequest(force))
        }
    }

    suspend fun sessionGitJob(sessionId: String, jobId: String): GitJobResponse =
        mapSessionGit { restApi.getSessionGitJob(sessionId, jobId) }

    suspend fun cancelSessionGitJob(sessionId: String, jobId: String): GitJobResponse =
        mapSessionGit { restApi.cancelSessionGitJob(sessionId, jobId) }

    /**
     * Every job the agent reports for [sessionId], as the socket frames arrive —
     * how a screen adopts a job another client started, and how the starter
     * sees its own job before the HTTP answer lands (the first frame goes out
     * before the response).
     */
    fun observeSessionGitJobFrames(sessionId: String): Flow<GitJobResponse> =
        merge(
            eventBus.stream<AgentEvent.GitJobProgress>().map { it.job },
            eventBus.stream<AgentEvent.GitJobDone>().map { it.job },
        ).filter { it.session_id == sessionId }

    /**
     * One job's feed: its socket frames as they arrive, plus a reconcile poll
     * every [interval] while the app is foregrounded — the frames are the fast
     * path, the poll is what survives a dropped `git.job.done`, a background
     * stretch, or a job started before this screen subscribed. A `running`
     * snapshot never overwrites a `done` one (a poll issued just before the
     * terminal frame can answer after it). Ends once the job is done, the
     * agent has forgotten it, or the session is gone.
     */
    @OptIn(ExperimentalCoroutinesApi::class)
    fun observeSessionGitJob(
        sessionId: String,
        jobId: String,
        foreground: Flow<Boolean>,
        initial: () -> GitJobResponse? = { null },
        interval: Duration = SESSION_GIT_JOB_POLL_INTERVAL,
    ): Flow<SessionGitJobLoad> = flow {
        var latest = initial()
        val ticks = foreground.distinctUntilChanged().flatMapLatest { inForeground ->
            if (inForeground) ticker(interval) else emptyFlow()
        }.map<Unit, JobSignal> { JobSignal.Tick }
        val frames = merge(
            eventBus.stream<AgentEvent.GitJobProgress>().map { it.job },
            eventBus.stream<AgentEvent.GitJobDone>().map { it.job },
        ).filter { it.job_id == jobId }.map<GitJobResponse, JobSignal> { JobSignal.Frame(it) }
        emitAll(
            merge(ticks, frames)
                .conflate()
                .map { signal ->
                    when (signal) {
                        is JobSignal.Frame -> {
                            latest = newer(latest, signal.job)
                            SessionGitJobLoad.Loaded(latest!!)
                        }
                        JobSignal.Tick -> try {
                            latest = newer(latest, sessionGitJob(sessionId, jobId))
                            SessionGitJobLoad.Loaded(latest!!)
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            if (isJobLost(e)) SessionGitJobLoad.Lost
                            else SessionGitJobLoad.Failed(latest, e)
                        }
                    }
                }
                .onStart { latest?.let { emit(SessionGitJobLoad.Loaded(it)) } }
                .transformWhile { load ->
                    emit(load)
                    when (load) {
                        is SessionGitJobLoad.Loaded -> load.job.running
                        is SessionGitJobLoad.Failed -> !isTerminal(load.error)
                        SessionGitJobLoad.Lost -> false
                    }
                },
        )
    }

    private fun newer(current: GitJobResponse?, incoming: GitJobResponse): GitJobResponse =
        if (current != null && !current.running && incoming.running) current else incoming

    private fun isJobLost(error: Throwable): Boolean =
        error is SessionGitException.GitError &&
            error.code == SessionGitException.CODE_JOB_NOT_FOUND

    suspend fun stageSessionPaths(sessionId: String, paths: List<String>) {
        mapSessionGit { restApi.stageSessionGit(sessionId, GitPathsRequest(paths)) }
    }

    suspend fun unstageSessionPaths(sessionId: String, paths: List<String>) {
        mapSessionGit { restApi.unstageSessionGit(sessionId, GitPathsRequest(paths)) }
    }

    suspend fun discardSessionPaths(sessionId: String, paths: List<String>, scope: String) {
        mapSessionGit { restApi.discardSessionGit(sessionId, GitDiscardRequest(paths, scope)) }
    }

    suspend fun sessionCommitContext(sessionId: String): GitCommitContextResponse =
        mapSessionGit { restApi.getSessionGitCommitContext(sessionId) }

    suspend fun commitSession(
        sessionId: String,
        message: String,
        amend: Boolean,
        noVerify: Boolean,
    ): GitCommitResponse =
        mapSessionGit {
            restApi.commitSessionGit(sessionId, GitCommitRequest(message, amend, noVerify))
        }

    suspend fun rebaseSession(sessionId: String, onto: String): GitRebaseResponse =
        mapSessionGit { restApi.rebaseSessionGit(sessionId, GitRebaseRequest(onto)) }

    suspend fun sessionConflict(sessionId: String, path: String): GitConflictResponse =
        mapSessionGit { restApi.getSessionGitConflict(sessionId, path) }

    /** `markResolved = false` saves a draft: the file is written and stays unmerged. */
    suspend fun resolveSessionConflict(
        sessionId: String,
        path: String,
        token: String,
        content: String,
        markResolved: Boolean,
    ) {
        mapSessionGit {
            restApi.resolveSessionGitConflict(
                sessionId,
                GitConflictResolveRequest(path, token, content, markResolved),
            )
        }
    }

    /** `side` is [GitConflictResponse.SIDE_OURS] or [GitConflictResponse.SIDE_THEIRS]. */
    suspend fun takeSessionConflict(sessionId: String, path: String, side: String) {
        mapSessionGit { restApi.takeSessionGitConflict(sessionId, GitConflictTakeRequest(path, side)) }
    }

    /** Rebuild the conflict; a `token` refuses it once the file moved on under an open editor. */
    suspend fun restoreSessionConflict(
        sessionId: String,
        path: String,
        style: String = GitConflictRestoreRequest.STYLE_MERGE,
        token: String? = null,
    ) {
        mapSessionGit {
            restApi.restoreSessionGitConflict(
                sessionId,
                GitConflictRestoreRequest(path, style, token),
            )
        }
    }

    /** `step` is [REBASE_CONTINUE], [REBASE_SKIP] or [REBASE_ABORT]. */
    suspend fun stepSessionRebase(sessionId: String, step: String): GitRebaseResponse =
        mapSessionGit { restApi.stepSessionGitRebase(sessionId, step) }

    /**
     * The session Changes feed: a status body per refresh trigger, for as long
     * as the flow is collected.
     *
     * Polling, not just push: `git.changed` only fires for the app's own git
     * mutations, so a commit typed into the PTY — the whole point of the viewer
     * — never announces itself. The ticker therefore runs while the app is
     * foregrounded ([foreground] restarts it, which doubles as "refresh on
     * foreground"); the caller stops the poll off-screen by simply not
     * collecting. Each request carries the last `etag`, so an idle repo costs a
     * tiny `unchanged` round trip. [initial] re-seeds that cache across
     * re-collections, which the ViewModel's `WhileSubscribed` sharing causes on
     * every return to the screen.
     *
     * The feed ends after an old-agent 404 or a session-not-found 404: neither
     * answer can change, so there is nothing left to poll.
     */
    @OptIn(ExperimentalCoroutinesApi::class)
    fun observeSessionGitStatus(
        sessionId: String,
        projectName: () -> String?,
        foreground: Flow<Boolean>,
        refreshRequests: Flow<Unit>,
        initial: () -> GitStatusResponse?,
        interval: Duration = SESSION_GIT_POLL_INTERVAL,
    ): Flow<SessionGitStatusLoad> = flow {
        var cached = initial()
        // Set on the producer side, before conflation can drop the trigger.
        var manualPending = false
        val ticks = foreground.distinctUntilChanged().flatMapLatest { inForeground ->
            if (inForeground) ticker(interval) else emptyFlow()
        }.map { PollTrigger.Tick }
        val pushes = eventBus.stream<AgentEvent.GitChanged>()
            .filter { event -> matchesSession(event, projectName(), cached?.repo) }
            .map { PollTrigger.Push }
        val manual = refreshRequests.map { PollTrigger.Manual }
        emitAll(
            merge(ticks, pushes, manual)
                .onEach { trigger -> if (trigger == PollTrigger.Manual) manualPending = true }
                .conflate()
                .map {
                    val answersManual = manualPending
                    manualPending = false
                    try {
                        val fresh = sessionGitStatus(sessionId, cached?.etag)
                        val body = when {
                            !fresh.unchanged -> fresh
                            cached != null -> cached!!
                            else -> sessionGitStatus(sessionId, null)
                        }
                        cached = body
                        SessionGitStatusLoad.Loaded(body, manual = answersManual)
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        SessionGitStatusLoad.Failed(cached, e, manual = answersManual)
                    }
                }
                .transformWhile { load ->
                    emit(load)
                    load !is SessionGitStatusLoad.Failed || !isTerminal(load.error)
                }
        )
    }

    private fun ticker(interval: Duration): Flow<Unit> = flow {
        while (true) {
            emit(Unit)
            delay(interval)
        }
    }

    private fun isTerminal(error: Throwable): Boolean =
        error is SessionGitException.AgentOutdated || error is SessionGitException.SessionGone

    private fun matchesSession(
        event: AgentEvent.GitChanged,
        projectName: String?,
        repo: GitRepoInfo?,
    ): Boolean {
        if (repo == null) return true
        if (projectName != null && event.projectName == projectName) return true
        if (event.toplevel != null && event.toplevel == repo.toplevel) return true
        return event.commonDir != null && event.commonDir == repo.common_dir
    }

    private inline fun <T> mapSessionGit(block: () -> T): T =
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            throw mapSessionGitError(e)
        }

    private inline fun <T> mapBusy(block: () -> T): T =
        try {
            block()
        } catch (e: ApiException) {
            if (e.code == 409) throw GitBusyException(parseBusyBy(e.errorBody))
            throw e
        }

    // The busy 409 keeps the project screen's own exception; every other coded
    // detail — the branch refusals and the session vocabulary they reuse —
    // maps like the session routes.
    private inline fun <T> mapBranchRoute(block: () -> T): T =
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (e: ApiException) {
            if (e.code == 409) {
                val holders = parseBusyBy(e.errorBody)
                if (holders.isNotEmpty()) throw GitBusyException(holders)
            }
            throw mapSessionGitError(e)
        } catch (e: Exception) {
            throw mapSessionGitError(e)
        }

    private fun parseBusyBy(body: String): List<BusyHolder> {
        if (body.isBlank()) return emptyList()
        return try {
            // The server packs BusyByResponse into FastAPI's `detail` field.
            val root = lenientDetailJson.parseToJsonElement(body) as? JsonObject ?: return emptyList()
            val detail = root["detail"] as? JsonObject ?: return emptyList()
            lenientDetailJson.decodeFromJsonElement(BusyByResponse.serializer(), detail).busy_by
        } catch (_: Exception) {
            emptyList()
        }
    }

    companion object {
        val SESSION_GIT_POLL_INTERVAL: Duration = 10.seconds
        val SESSION_GIT_JOB_POLL_INTERVAL: Duration = 3.seconds

        const val REBASE_CONTINUE = "continue"
        const val REBASE_SKIP = "skip"
        const val REBASE_ABORT = "abort"
    }
}
