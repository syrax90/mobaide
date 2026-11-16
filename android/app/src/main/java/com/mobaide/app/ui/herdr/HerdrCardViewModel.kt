package com.mobaide.app.ui.herdr

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.mobaide.app.data.local.HerdrSeenStore
import com.mobaide.app.data.repository.HerdrRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/** The Herdr entry on the session list, or null while there is nothing to show. */
data class HerdrCardUiState(val summary: String, val status: HerdrStatus)

/**
 * Feeds the session list's Herdr card. The card shows only while a herdr
 * server runs on the agent's computer: an agent without the dashboard, and a
 * computer without herdr, show nothing.
 */
@HiltViewModel
class HerdrCardViewModel @Inject constructor(
    private val repository: HerdrRepository,
    seenStore: HerdrSeenStore,
) : ViewModel() {

    val uiState: StateFlow<HerdrCardUiState?> =
        combine(repository.snapshot, seenStore.seen) { snapshot, seen ->
            if (snapshot == null || snapshot.servers.isEmpty()) return@combine null
            val rows = herdrPaneRows(repository.agentId(), snapshot, seen)
            HerdrCardUiState(summary = herdrSummary(rows), status = herdrRollup(rows))
        }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), null)

    init {
        refresh()
    }

    /** REST seed; `herdr.changed` keeps the state current afterwards. */
    fun refresh() {
        viewModelScope.launch { repository.refresh() }
    }
}
