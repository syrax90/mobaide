package com.mobaide.app.ui.sessions

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.ApiException
import com.mobaide.app.data.network.dto.ChatStoredSession
import com.mobaide.app.data.repository.SessionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import java.io.IOException
import javax.inject.Inject

sealed interface ChatResumeListState {
    data object Loading : ChatResumeListState
    data object Empty : ChatResumeListState
    data class Error(val message: String) : ChatResumeListState
    data class Loaded(
        val rows: List<ChatStoredSession>,
        val nextCursor: String?,
        val loadingMore: Boolean = false,
        val loadMoreError: String? = null,
        /** The agent can delete its stored sessions. */
        val canDelete: Boolean = false,
    ) : ChatResumeListState
}

/** The row waiting for the user to confirm Delete. */
data class ChatResumeDeleteState(
    val row: ChatStoredSession,
    val deleting: Boolean = false,
    val error: String? = null,
)

/** Delete is offered only when the agent supports it and nothing has the session open. */
fun canDeleteRow(canDelete: Boolean, row: ChatStoredSession): Boolean =
    canDelete && row.open_session_id == null && !row.open_elsewhere

/** What deleting does on the PC; the agent decides between a soft and a hard delete. */
fun chatDeleteNote(kind: String): String = if (kind == "codex") {
    "Codex moves this session to its archive on your PC. It no longer shows here or in the Codex terminal app."
} else {
    "This session is removed from your PC, also from the history of the agent's terminal app. You cannot undo this."
}

data class ChatResumeUiState(
    /** The agent kind whose stored sessions are listed (`claude` or `codex`). */
    val kind: String = "claude",
    /** The sessions stay in sync with the agent's own apps on the PC. */
    val live: Boolean = false,
    /** The folder "This folder" lists; null when the wizard has none, which forces All folders. */
    val folder: String? = null,
    val allFolders: Boolean = false,
    val list: ChatResumeListState = ChatResumeListState.Loading,
    val delete: ChatResumeDeleteState? = null,
)

/**
 * "Continue a session": the sessions the agent (Claude Code, Codex, Antigravity, OpenCode, GitHub Copilot,
 * Cursor, Junie or Pi) stored on the PC, newest first, one page at a time. Fetch-only; the first page can take a few seconds because
 * the agent starts its process to answer.
 */
@HiltViewModel
class ChatResumeViewModel @Inject constructor(
    private val sessionRepository: SessionRepository,
) : ViewModel() {

    private val _uiState = MutableStateFlow(ChatResumeUiState())
    val uiState: StateFlow<ChatResumeUiState> = _uiState.asStateFlow()

    private var loadJob: Job? = null
    private var opened = false

    fun open(kind: String, folder: String?, live: Boolean) {
        opened = true
        _uiState.value = ChatResumeUiState(
            kind = kind,
            live = live,
            folder = folder,
            allFolders = folder == null,
        )
        loadFirstPage()
    }

    /** Reopens after process death, when the sheet is restored but nothing was loaded. */
    fun ensureOpen(kind: String, folder: String?, live: Boolean) {
        if (!opened) open(kind, folder, live)
    }

    fun setAllFolders(all: Boolean) {
        val state = _uiState.value
        if (state.allFolders == all || (!all && state.folder == null)) return
        _uiState.value = state.copy(allFolders = all)
        loadFirstPage()
    }

    fun retry() = loadFirstPage()

    fun loadMore() {
        val loaded = _uiState.value.list as? ChatResumeListState.Loaded ?: return
        val cursor = loaded.nextCursor ?: return
        if (loaded.loadingMore) return
        setList(loaded.copy(loadingMore = true, loadMoreError = null))
        val scope = _uiState.value
        loadJob = viewModelScope.launch {
            try {
                val page = sessionRepository.listChatSessions(scope.kind, cwdFor(scope), cursor)
                val current = _uiState.value.list as? ChatResumeListState.Loaded ?: return@launch
                val known = current.rows.mapTo(HashSet()) { it.session_id }
                setList(
                    current.copy(
                        rows = current.rows + page.sessions.filter { it.session_id !in known },
                        nextCursor = page.next_cursor,
                        loadingMore = false,
                    ),
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val current = _uiState.value.list as? ChatResumeListState.Loaded ?: return@launch
                setList(current.copy(loadingMore = false, loadMoreError = errorMessage(e)))
            }
        }
    }

    private fun loadFirstPage() {
        loadJob?.cancel()
        setList(ChatResumeListState.Loading)
        val scope = _uiState.value
        loadJob = viewModelScope.launch {
            try {
                val page = sessionRepository.listChatSessions(scope.kind, cwdFor(scope), null)
                setList(
                    if (page.sessions.isEmpty()) {
                        ChatResumeListState.Empty
                    } else {
                        ChatResumeListState.Loaded(
                            rows = page.sessions,
                            nextCursor = page.next_cursor,
                            canDelete = page.can_delete,
                        )
                    },
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                setList(ChatResumeListState.Error(errorMessage(e)))
            }
        }
    }

    fun askDelete(row: ChatStoredSession) {
        val loaded = _uiState.value.list as? ChatResumeListState.Loaded ?: return
        if (!canDeleteRow(loaded.canDelete, row)) return
        _uiState.update { it.copy(delete = ChatResumeDeleteState(row)) }
    }

    fun dismissDelete() {
        if (_uiState.value.delete?.deleting == true) return
        _uiState.update { it.copy(delete = null) }
    }

    /** On success the row leaves the loaded list in place: a reload would restart paging. */
    fun confirmDelete() {
        val pending = _uiState.value.delete ?: return
        if (pending.deleting) return
        val kind = _uiState.value.kind
        _uiState.update { it.copy(delete = pending.copy(deleting = true, error = null)) }
        viewModelScope.launch {
            try {
                sessionRepository.deleteChatSession(kind, pending.row.session_id)
                _uiState.update { state ->
                    state.copy(list = withoutRow(state.list, pending.row.session_id), delete = null)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(delete = pending.copy(deleting = false, error = errorMessage(e, "delete the session")))
                }
            }
        }
    }

    private fun withoutRow(list: ChatResumeListState, sessionId: String): ChatResumeListState {
        if (list !is ChatResumeListState.Loaded) return list
        val rows = list.rows.filter { it.session_id != sessionId }
        return if (rows.isEmpty() && list.nextCursor == null) ChatResumeListState.Empty else list.copy(rows = rows)
    }

    private fun cwdFor(state: ChatResumeUiState): String? = if (state.allFolders) null else state.folder

    private fun setList(list: ChatResumeListState) = _uiState.update { it.copy(list = list) }

    private fun errorMessage(e: Exception, action: String = "list the sessions"): String = when (e) {
        is ApiException -> apiDetail(e.errorBody) ?: "Could not $action (HTTP ${e.code})."
        is IOException -> "Could not reach your PC."
        else -> e.message ?: "Could not $action."
    }

    /** `{"detail": str}`, or the same object nested under `detail`. */
    private fun apiDetail(body: String): String? = runCatching {
        val detail = Json.parseToJsonElement(body).jsonObject["detail"]
        when (detail) {
            is JsonPrimitive -> detail.content
            is JsonObject -> (detail["detail"] as? JsonPrimitive)?.content
            else -> null
        }
    }.getOrNull()?.takeIf { it.isNotBlank() }
}
