package com.mobaide.app.ui.terminal.git

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.dto.GitStatusEntry
import com.mobaide.app.data.network.dto.GitStatusResponse
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.GitCapabilities
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.data.repository.SessionGitStatusLoad
import com.mobaide.app.data.repository.SessionRepository
import com.mobaide.app.notifications.AppForegroundTracker
import com.mobaide.app.notifications.ForegroundSessionTracker
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onCompletion
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class GitChangesViewModel @Inject constructor(
    private val savedStateHandle: SavedStateHandle,
    private val gitRepository: GitRepository,
    sessionRepository: SessionRepository,
    appForegroundTracker: AppForegroundTracker,
    private val foregroundSessionTracker: ForegroundSessionTracker,
) : ViewModel() {

    val sessionId: String = savedStateHandle.get<String>("sessionId").orEmpty()

    private val sessionRow: StateFlow<SessionResponse?> =
        sessionRepository.observeSessionList()
            .map { sessions -> sessions.firstOrNull { it.session_id == sessionId } }
            .stateIn(viewModelScope, SharingStarted.Eagerly, null)

    private val refreshRequests = MutableSharedFlow<Unit>(
        extraBufferCapacity = 1,
        onBufferOverflow = BufferOverflow.DROP_OLDEST,
    )
    private val manualRefreshing = MutableStateFlow(false)
    private val capabilities = MutableStateFlow(GitCapabilities.NONE)
    private val selectedKeys = MutableStateFlow<Set<String>>(emptySet())
    private val selecting = MutableStateFlow(false)
    private val busy = MutableStateFlow(false)

    /** Re-seeds the poll's etag cache each time the screen resubscribes. */
    private var lastStatus: GitStatusResponse? = null

    private val loads: Flow<SessionGitStatusLoad> =
        gitRepository.observeSessionGitStatus(
            sessionId = sessionId,
            projectName = { sessionRow.value?.project_name },
            foreground = appForegroundTracker.isForeground,
            refreshRequests = refreshRequests,
            initial = { lastStatus },
        ).onStart {
            manualRefreshing.value = false
            loadCapabilities()
        }
            .onEach { load ->
                load.cached?.let { status ->
                    lastStatus = status
                    pruneSelection(status)
                }
                if (load.manual) manualRefreshing.value = false
            }
            .onCompletion { manualRefreshing.value = false }

    private val selection: Flow<Pair<Set<String>, Boolean>> =
        combine(selectedKeys, selecting) { keys, entering -> keys to entering }

    val uiState: StateFlow<GitChangesUiState> =
        combine(loads, manualRefreshing, capabilities, selection, busy) {
            load, refreshing, caps, (selected, entering), inFlight ->
            toUiState(load, refreshing, caps, selected, entering, inFlight)
        }.stateIn(
            viewModelScope,
            SharingStarted.WhileSubscribed(5_000),
            GitChangesUiState.Loading,
        )

    private val _discardPrompt = MutableStateFlow<DiscardPrompt?>(null)
    val discardPrompt: StateFlow<DiscardPrompt?> = _discardPrompt.asStateFlow()

    private val _commitSheet = MutableStateFlow<CommitSheetState?>(null)
    val commitSheet: StateFlow<CommitSheetState?> = _commitSheet.asStateFlow()

    private val _events = Channel<GitChangesEvent>(Channel.BUFFERED)
    val events: Flow<GitChangesEvent> = _events.receiveAsFlow()

    init {
        // Entered from the terminal menu's Commit…: the sheet opens over the
        // list. Consumed once, so a restored screen does not reopen it.
        if (savedStateHandle.get<Boolean>("commit") == true) {
            savedStateHandle["commit"] = false
            openCommit()
        }
    }

    fun refresh() {
        // A pull on a finished feed (old agent, session gone) has nobody to
        // answer it; arming the indicator then would leave it spinning.
        if (refreshRequests.subscriptionCount.value > 0) manualRefreshing.value = true
        refreshRequests.tryEmit(Unit)
        loadCapabilities()
    }

    fun onEntryClick(entry: GitStatusEntry) {
        val ready = uiState.value as? GitChangesUiState.Ready
        if (ready != null && ready.inSelectionMode) {
            keyFor(ready, entry)?.let(::toggleSelection)
            return
        }
        viewModelScope.launch {
            _events.send(GitChangesEvent.OpenDiff(sessionId, entry, diffScopeFor(entry)))
        }
    }

    fun onEntryLongClick(entry: GitStatusEntry) {
        val ready = uiState.value as? GitChangesUiState.Ready ?: return
        if (!rowActions(entry, ready.capabilities).any) return
        keyFor(ready, entry)?.let(::toggleSelection)
    }

    fun toggleSelection(key: String) {
        selectedKeys.update { if (key in it) it - key else it + key }
    }

    /** The top bar's Select: the checkboxes appear before anything is ticked. */
    fun enterSelection() {
        selecting.value = true
    }

    fun clearSelection() {
        selecting.value = false
        selectedKeys.value = emptySet()
    }

    fun stage(entries: List<GitStatusEntry>) = mutate(entries) {
        gitRepository.stageSessionPaths(sessionId, pathsFor(entries))
    }

    fun unstage(entries: List<GitStatusEntry>) = mutate(entries) {
        gitRepository.unstageSessionPaths(sessionId, pathsFor(entries))
    }

    fun requestDiscard(entries: List<GitStatusEntry>) {
        if (entries.isNotEmpty()) _discardPrompt.value = DiscardPrompt(entries)
    }

    fun dismissDiscard() {
        _discardPrompt.value = null
    }

    // One request per scope: a selection can mix modified and untracked
    // files, and the route takes one scope at a time.
    fun confirmDiscard() {
        val prompt = _discardPrompt.value ?: return
        _discardPrompt.value = null
        mutate(prompt.entries) {
            val byScope = prompt.entries.groupBy(::discardScopeFor)
            for ((scope, entries) in byScope) {
                gitRepository.discardSessionPaths(sessionId, pathsFor(entries), scope)
            }
        }
    }

    fun stageSelection() = stage(selectionSubsets().stage)

    fun unstageSelection() = unstage(selectionSubsets().unstage)

    fun discardSelection() = requestDiscard(selectionSubsets().discard)

    fun openCommit() {
        _commitSheet.value = CommitSheetState()
        viewModelScope.launch {
            val context = try {
                val response = gitRepository.sessionCommitContext(sessionId)
                CommitContextState.Ready(response.author, response.head)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                CommitContextState.Failed(mutationErrorCopy(e))
            }
            _commitSheet.update { sheet -> sheet?.copy(context = context) }
        }
    }

    fun closeCommit() {
        if (_commitSheet.value?.committing == true) return
        _commitSheet.value = null
    }

    fun commit(summary: String, description: String, amend: Boolean, noVerify: Boolean) {
        if (busy.value) return
        val message = composeCommitMessage(summary, description)
        if (message.isBlank()) return
        viewModelScope.launch {
            busy.value = true
            _commitSheet.update { it?.copy(committing = true, error = null) }
            try {
                val result = gitRepository.commitSession(sessionId, message, amend, noVerify)
                _commitSheet.value = null
                _events.send(GitChangesEvent.ShowMessage(commitNotice(result)))
                refreshRequests.tryEmit(Unit)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val output = (e as? SessionGitException.GitError)
                    ?.takeIf { it.code == SessionGitException.CODE_HOOK_FAILED }
                    ?.detailMessage
                _commitSheet.update {
                    it?.copy(committing = false, error = CommitError(mutationErrorCopy(e), output))
                }
            } finally {
                busy.value = false
            }
        }
    }

    fun holdWatch() = foregroundSessionTracker.holdWatch(sessionId)

    fun releaseWatch() = foregroundSessionTracker.releaseWatch(sessionId)

    /**
     * One mutation at a time; on success the ticked rows are forgotten and
     * the feed re-reads the working tree at once — the agent's `git.changed`
     * echo would do the same a moment later through the etag path.
     */
    private fun mutate(entries: List<GitStatusEntry>, block: suspend () -> Unit) {
        if (entries.isEmpty() || busy.value) return
        viewModelScope.launch {
            busy.value = true
            try {
                block()
                val ready = uiState.value as? GitChangesUiState.Ready
                if (ready != null && selectedKeys.value.isNotEmpty()) {
                    // One lookup table, not an indexOf per entry: "Stage all" on a
                    // 5 000-row section would otherwise cost 25 M data-class
                    // comparisons on the main thread.
                    val keyByEntry = ready.sections
                        .flatMap { section -> section.entries.zip(section.keys) }
                        .toMap()
                    val done = entries.mapNotNull(keyByEntry::get).toSet()
                    selectedKeys.update { it - done }
                }
                // Selection mode ends with the last ticked row, whichever way
                // it was entered (top-bar Select or long-press).
                if (selectedKeys.value.isEmpty()) selecting.value = false
                refreshRequests.tryEmit(Unit)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _events.send(GitChangesEvent.ShowMessage(mutationErrorCopy(e)))
                refreshRequests.tryEmit(Unit)
            } finally {
                busy.value = false
            }
        }
    }

    /**
     * Ticked rows that vanished with a refresh are dropped from the source
     * flow itself, not only from the projected state: a stale key would keep
     * the selection bar open at "0 selected" after the next action and
     * re-tick the row if the same key came back (iOS and web prune the same way).
     */
    private fun pruneSelection(status: GitStatusResponse) {
        if (selectedKeys.value.isEmpty()) return
        val live = sectionsFor(status).flatMapTo(hashSetOf()) { it.keys }
        selectedKeys.update { keys -> if (keys.all { it in live }) keys else keys.intersect(live) }
    }

    private fun selectionSubsets(): SelectionSubsets {
        val ready = uiState.value as? GitChangesUiState.Ready ?: return SelectionSubsets()
        return selectionSubsets(ready.sections, ready.selectedKeys, ready.capabilities)
    }

    private fun keyFor(ready: GitChangesUiState.Ready, entry: GitStatusEntry): String? {
        for (section in ready.sections) {
            val index = section.entries.indexOf(entry)
            if (index >= 0) return section.keys[index]
        }
        return null
    }

    private fun loadCapabilities() {
        viewModelScope.launch {
            try {
                capabilities.value = gitRepository.gitCapabilities()
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // An unreadable probe keeps the read-only viewer; the poll's
                // own failure state reports the connection problem.
            }
        }
    }

    private fun toUiState(
        load: SessionGitStatusLoad,
        refreshing: Boolean,
        caps: GitCapabilities,
        selected: Set<String>,
        entering: Boolean,
        inFlight: Boolean,
    ): GitChangesUiState =
        when (load) {
            is SessionGitStatusLoad.Loaded ->
                fromStatus(load.status, refreshing, null, caps, selected, entering, inFlight)
            is SessionGitStatusLoad.Failed -> when (val error = load.error) {
                is SessionGitException.AgentOutdated -> GitChangesUiState.AgentOutdated
                is SessionGitException.SessionGone -> GitChangesUiState.SessionGone
                else -> {
                    val cached = load.cached
                    when {
                        // Retry with nothing cached: show progress rather than the
                        // unchanged Error, which StateFlow would dedupe into silence.
                        cached == null && refreshing -> GitChangesUiState.Loading
                        cached == null -> GitChangesUiState.Error(sessionGitErrorCopy(error))
                        else -> fromStatus(
                            cached,
                            refreshing = refreshing,
                            staleError = sessionGitErrorCopy(error),
                            caps = caps,
                            selected = selected,
                            entering = entering,
                            inFlight = inFlight,
                        )
                    }
                }
            }
        }

    private fun fromStatus(
        status: GitStatusResponse,
        refreshing: Boolean,
        staleError: String?,
        caps: GitCapabilities,
        selected: Set<String>,
        entering: Boolean,
        inFlight: Boolean,
    ): GitChangesUiState {
        val repo = status.repo
        val reason = status.unavailable_reason
        if (repo == null || reason != null) {
            return GitChangesUiState.Unavailable(
                reason ?: GitStatusResponse.UNAVAILABLE_NOT_A_REPO,
                status.cwd,
            )
        }
        val sections = sectionsFor(status)
        // The source flow is pruned as each body lands (`pruneSelection`); the
        // intersection here only covers the one emission in between.
        val live = sections.flatMapTo(mutableSetOf()) { it.keys }
        return GitChangesUiState.Ready(
            status = status,
            repo = repo,
            sections = sections,
            refreshing = refreshing,
            staleError = staleError,
            capabilities = caps,
            selectedKeys = if (selected.all { it in live }) selected else selected.intersect(live),
            selecting = entering,
            busy = inFlight,
        )
    }
}
