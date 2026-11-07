package com.mobaide.app.ui.navigation

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.data.repository.SessionRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.mapNotNull
import kotlinx.coroutines.flow.merge
import kotlinx.coroutines.launch
import javax.inject.Inject

enum class SessionScreenKind { Terminal, Chat }

/**
 * Which screen the session route shows: the chat screen for a `ui: "chat"` session,
 * the terminal otherwise. Decided once per entry, from the live session list or a
 * one-shot REST read for a cold start; a session the synced list does not hold
 * goes to the terminal, whose own session-gone handling leaves it.
 */
@HiltViewModel
class SessionRouteViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val sessionRepository: SessionRepository,
) : ViewModel() {
    private val sessionId: String = savedStateHandle["sessionId"] ?: ""

    private val _kind = MutableStateFlow(
        sessionRepository.observeSessionList().value.firstOrNull { it.session_id == sessionId }?.screenKind(),
    )
    val kind: StateFlow<SessionScreenKind?> = _kind.asStateFlow()

    init {
        if (_kind.value == null) {
            viewModelScope.launch {
                _kind.value = merge(
                    sessionRepository.observeSessionList()
                        .mapNotNull { list -> list.firstOrNull { it.session_id == sessionId }?.screenKind() },
                    flow {
                        runCatching { sessionRepository.listSessions() }.getOrNull()
                            ?.firstOrNull { it.session_id == sessionId }
                            ?.let { emit(it.screenKind()) }
                    },
                    combine(
                        sessionRepository.observeSessionList(),
                        sessionRepository.observeSessionsSynced(),
                    ) { list, synced ->
                        SessionScreenKind.Terminal.takeIf { synced && list.none { it.session_id == sessionId } }
                    }.mapNotNull { it },
                ).first()
            }
        }
    }

    private fun SessionResponse.screenKind(): SessionScreenKind =
        if (isChat) SessionScreenKind.Chat else SessionScreenKind.Terminal
}
