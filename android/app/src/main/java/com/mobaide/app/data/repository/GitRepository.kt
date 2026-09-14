package com.mobaide.app.data.repository

import com.mobaide.app.data.events.AgentEvent
import com.mobaide.app.data.events.AgentEventBus
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.RestApi
import com.mobaide.app.data.network.dto.BusyByResponse
import com.mobaide.app.data.network.dto.BusyHolder
import com.mobaide.app.data.network.dto.GitDeleteItem
import com.mobaide.app.data.network.dto.GitDeleteRequest
import com.mobaide.app.data.network.dto.GitDeleteResponse
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.data.network.dto.GitItem
import com.mobaide.app.data.network.dto.GitRepoInfo
import com.mobaide.app.data.network.dto.GitStatusResponse
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

    suspend fun deleteGitItems(
        projectName: String,
        items: List<GitDeleteItem>,
    ): GitDeleteResponse =
        mapBusy { restApi.deleteGitItems(projectName, GitDeleteRequest(items)) }

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
    }
}
