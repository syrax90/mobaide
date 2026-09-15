package com.mobaide.app.ui.terminal.git

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.dto.GitDiffResponse
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.notifications.ForegroundSessionTracker
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.transformLatest
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class GitDiffViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val gitRepository: GitRepository,
    private val foregroundSessionTracker: ForegroundSessionTracker,
) : ViewModel() {

    val sessionId: String = savedStateHandle.get<String>("sessionId").orEmpty()
    val path: String? = savedStateHandle.get<String>("path")
    val scope: String? = savedStateHandle.get<String>("scope")
    val origPath: String? = savedStateHandle.get<String>("origPath")

    private val context = MutableStateFlow(DIFF_CONTEXT_DEFAULT)
    private val reloads = MutableStateFlow(0)
    private var lastReady: GitDiffUiState.Ready? = null
    private val capabilities = MutableStateFlow(GitCapabilities.NONE)
    private val busy = MutableStateFlow(false)

    /**
     * The toolbar's Stage / Unstage, from the route's scope: null while the
     * agent advertises no `stage` capability or the scope is unknown.
     */
    val stageAction: StateFlow<DiffStageAction?> =
        combine(capabilities, busy) { caps, inFlight ->
            if (!caps.stage) return@combine null
            when (scope) {
                GitDiffResponse.SCOPE_STAGED -> DiffStageAction("Unstage", stage = false, busy = inFlight)
                GitDiffResponse.SCOPE_UNSTAGED, GitDiffResponse.SCOPE_UNTRACKED ->
                    DiffStageAction("Stage", stage = true, busy = inFlight)
                else -> null
            }
        }.onStart { loadCapabilities() }
            .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    private val _events = Channel<GitDiffEvent>(Channel.BUFFERED)
    val events: Flow<GitDiffEvent> = _events.receiveAsFlow()

    @OptIn(ExperimentalCoroutinesApi::class)
    val uiState: StateFlow<GitDiffUiState> =
        combine(context, reloads) { ctx, _ -> ctx }
            .transformLatest { ctx ->
                emit(lastReady?.copy(context = ctx, refreshing = true) ?: GitDiffUiState.Loading(ctx))
                emit(load(ctx))
            }
            .stateIn(
                viewModelScope,
                SharingStarted.WhileSubscribed(5_000),
                GitDiffUiState.Loading(DIFF_CONTEXT_DEFAULT),
            )

    fun setContext(lines: Int) {
        context.value = lines
    }

    fun refresh() {
        reloads.update { it + 1 }
    }

    fun holdWatch() = foregroundSessionTracker.holdWatch(sessionId)

    fun releaseWatch() = foregroundSessionTracker.releaseWatch(sessionId)

    /** Review-then-stage: on success the screen closes, the file now lives in another section. */
    fun runStageAction() {
        val action = stageAction.value ?: return
        val path = path ?: return
        if (busy.value) return
        viewModelScope.launch {
            busy.value = true
            try {
                val paths = listOfNotNull(path, origPath)
                if (action.stage) gitRepository.stageSessionPaths(sessionId, paths)
                else gitRepository.unstageSessionPaths(sessionId, paths)
                _events.send(GitDiffEvent.Close)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.send(GitDiffEvent.ShowMessage(mutationErrorCopy(e)))
            } finally {
                busy.value = false
            }
        }
    }

    private fun loadCapabilities() {
        viewModelScope.launch {
            try {
                capabilities.value = gitRepository.gitCapabilities()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // An unreadable probe keeps the read-only screen.
            }
        }
    }

    private suspend fun load(ctx: Int): GitDiffUiState {
        val path = path ?: return GitDiffUiState.Error("Missing file path", ctx)
        val scope = scope ?: return GitDiffUiState.Error("Missing diff scope", ctx)
        return try {
            val diff = gitRepository.sessionGitDiff(sessionId, path, scope, ctx, origPath)
            val rows = buildDiffRows(diff)
            GitDiffUiState.Ready(
                diff = diff,
                rows = rows,
                gutterDigits = gutterDigits(diff),
                widthCandidates = widthCandidates(rows),
                context = ctx,
                narrowCandidate = narrowCandidate(rows),
            ).also { lastReady = it }
        } catch (e: CancellationException) {
            throw e
        } catch (e: SessionGitException.AgentOutdated) {
            GitDiffUiState.AgentOutdated(ctx)
        } catch (e: SessionGitException.SessionGone) {
            GitDiffUiState.SessionGone(ctx)
        } catch (e: Exception) {
            GitDiffUiState.Error(sessionGitErrorCopy(e), ctx)
        }
    }
}
