package com.mobaide.app.ui.terminal.git

import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.text.TextRange
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.dto.GitConflictResponse
import com.mobaide.app.data.network.dto.GitConflictRestoreRequest
import com.mobaide.app.data.repository.GitRepository
import com.mobaide.app.data.repository.SessionGitException
import com.mobaide.app.notifications.ForegroundSessionTracker
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.FlowPreview
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.debounce
import kotlinx.coroutines.flow.receiveAsFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * The conflict resolver: one conflicted file in an editor.
 *
 * The draft lives in [editor], never in [SavedStateHandle] — a quarter-megabyte
 * string in a saved-state bundle is a `TransactionTooLargeException`. The file
 * is read once; nothing re-reads it behind the user's back, so the agent's
 * `file_changed` refusal is where an edit made in the session's terminal
 * surfaces, and the user then chooses between the file and the draft.
 */
@OptIn(FlowPreview::class)
@HiltViewModel
class GitConflictViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val gitRepository: GitRepository,
    private val foregroundSessionTracker: ForegroundSessionTracker,
) : ViewModel() {

    val sessionId: String = savedStateHandle.get<String>("sessionId").orEmpty()
    val path: String = savedStateHandle.get<String>("path").orEmpty()

    val editor = TextFieldState()

    private val _uiState = MutableStateFlow<GitConflictUiState>(GitConflictUiState.Loading)
    val uiState: StateFlow<GitConflictUiState> = _uiState.asStateFlow()

    private val _events = Channel<GitConflictEvent>(Channel.BUFFERED)
    val events: Flow<GitConflictEvent> = _events.receiveAsFlow()

    private var baseText = ""
    private var token: String? = null

    init {
        load(LoadMode.Seed)
        viewModelScope.launch {
            snapshotFlow { editor.text }
                .debounce(SCAN_DEBOUNCE_MS)
                .collect { text -> onTextSettled(text) }
        }
    }

    fun holdWatch() = foregroundSessionTracker.holdWatch(sessionId)

    fun releaseWatch() = foregroundSessionTracker.releaseWatch(sessionId)

    fun retry() = load(LoadMode.Seed)

    /** Drop the draft for the file as it is now. */
    fun reloadFile() = load(LoadMode.Seed)

    /** Keep the draft and adopt the file's new token: the next Apply overwrites the other change. */
    fun keepMyEdits() = load(LoadMode.TokenWithWarning)

    fun apply() = save(markResolved = true)

    fun saveDraft() = save(markResolved = false)

    fun take(choice: TakeChoice) = mutate {
        gitRepository.takeSessionConflict(sessionId, path, choice.side)
        _events.send(GitConflictEvent.Close(resolvedNotice(path)))
    }

    fun startOver(withOriginalLines: Boolean) = mutate {
        gitRepository.restoreSessionConflict(
            sessionId,
            path,
            style = if (withOriginalLines) GitConflictRestoreRequest.STYLE_DIFF3
            else GitConflictRestoreRequest.STYLE_MERGE,
            token = token,
        )
        reload(LoadMode.Seed)
    }

    /** Select the next `<<<<<<<` line; the field scrolls its caret into view. */
    fun jumpToNextConflict() {
        val range = nextConflictRange(editor.text, editor.selection.start) ?: return
        editor.edit { selection = TextRange(range.first, range.last + 1) }
    }

    private fun save(markResolved: Boolean) {
        val fileToken = token ?: return
        val content = editor.text.toString()
        mutate {
            gitRepository.resolveSessionConflict(sessionId, path, fileToken, content, markResolved)
            if (markResolved) {
                _events.send(GitConflictEvent.Close(resolvedNotice(path)))
            } else {
                // The draft is now the file: keep the text, take the file's new token.
                baseText = content
                updateReady { it.copy(dirty = false) }
                reload(LoadMode.Token)
                _events.send(GitConflictEvent.ShowMessage(draftSavedNotice(path)))
            }
        }
    }

    private fun mutate(block: suspend () -> Unit) {
        val ready = _uiState.value as? GitConflictUiState.Ready ?: return
        if (ready.busy) return
        viewModelScope.launch {
            updateReady { it.copy(busy = true) }
            try {
                block()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                onMutationFailed(e)
            } finally {
                updateReady { it.copy(busy = false) }
            }
        }
    }

    private suspend fun onMutationFailed(e: Exception) {
        val code = (e as? SessionGitException.GitError)?.code
        when (code) {
            SessionGitException.CODE_FILE_CHANGED -> updateReady { it.copy(stale = true) }
            SessionGitException.CODE_NOT_CONFLICTED -> updateReady { it.copy(gone = true) }
            else -> _events.send(GitConflictEvent.ShowMessage(mutationErrorCopy(e)))
        }
    }

    private fun load(mode: LoadMode) {
        viewModelScope.launch {
            if (_uiState.value !is GitConflictUiState.Ready) _uiState.value = GitConflictUiState.Loading
            else updateReady { it.copy(busy = true) }
            try {
                reload(mode)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                onLoadFailed(e)
            } finally {
                updateReady { it.copy(busy = false) }
            }
        }
    }

    private suspend fun onLoadFailed(e: Exception) {
        val notConflicted =
            (e as? SessionGitException.GitError)?.code == SessionGitException.CODE_NOT_CONFLICTED
        val ready = _uiState.value as? GitConflictUiState.Ready
        when {
            // A draft outlives a failed reload — a session that ended included:
            // it may be the only copy of the work.
            ready != null && notConflicted -> updateReady { it.copy(gone = true) }
            ready != null -> _events.send(GitConflictEvent.ShowMessage(sessionGitErrorCopy(e)))
            e is SessionGitException.AgentOutdated -> _uiState.value = GitConflictUiState.AgentOutdated
            e is SessionGitException.SessionGone -> _uiState.value = GitConflictUiState.SessionGone
            notConflicted -> _events.send(GitConflictEvent.Close(resolvedElsewhereNotice(path)))
            else -> _uiState.value = GitConflictUiState.Error(sessionGitErrorCopy(e))
        }
    }

    private suspend fun reload(mode: LoadMode) {
        val conflict = gitRepository.sessionConflict(sessionId, path)
        token = conflict.token
        val previous = _uiState.value as? GitConflictUiState.Ready
        if (mode == LoadMode.Seed || previous == null || !isEditableConflict(conflict)) {
            val text = conflict.content.orEmpty()
            baseText = text
            editor.edit {
                replace(0, length, text)
                selection = TextRange.Zero
            }
            _uiState.value = GitConflictUiState.Ready(
                conflict = conflict,
                scan = scanConflictText(text),
                busy = previous?.busy == true,
            )
        } else {
            // After a draft save the file should be the draft. If it is not,
            // someone wrote in between, and this token would let Apply
            // overwrite it unseen.
            val movedOn = mode == LoadMode.Token && conflict.content != baseText
            _uiState.value = previous.copy(
                conflict = conflict,
                stale = false,
                overwriteWarning = mode == LoadMode.TokenWithWarning || movedOn,
            )
        }
    }

    private fun onTextSettled(text: CharSequence) {
        updateReady {
            it.copy(
                scan = scanConflictText(text, it.conflict.byte_limit),
                dirty = !text.contentEquals(baseText),
            )
        }
    }

    private inline fun updateReady(transform: (GitConflictUiState.Ready) -> GitConflictUiState.Ready) {
        _uiState.update { state -> if (state is GitConflictUiState.Ready) transform(state) else state }
    }

    /** What a load does to the editor: fill it, or only adopt the file's new token. */
    private enum class LoadMode { Seed, Token, TokenWithWarning }

    private companion object {
        const val SCAN_DEBOUNCE_MS = 150L
    }
}
