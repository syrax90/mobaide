package com.mobaide.app.ui.herdr

import com.mobaide.app.data.network.dto.HerdrServer

sealed interface HerdrUiState {
    data object Loading : HerdrUiState

    /** No herdr server runs on the agent's computer, or the agent has no dashboard. */
    data object Empty : HerdrUiState

    data class Success(
        val servers: List<HerdrServer>,
        /** Every pane, in tree order. */
        val rows: List<HerdrPaneRow>,
        /** The panes with an agent, the ones that need the user first. */
        val agents: List<HerdrPaneRow>,
        val summary: String,
        /** An action is running: its dialog shows progress and blocks a second tap. */
        val busy: Boolean = false,
        /**
         * Why the action of the open dialog failed. Shown inside the dialog: a
         * snackbar would be under its scrim and under the keyboard.
         */
        val actionError: String? = null,
        /** The [HerdrPaneRow.key] of the pane that is being opened: its row shows progress. */
        val openingKey: String? = null,
    ) : HerdrUiState
}

/** One-shot side effects of the Herdr screen. */
sealed interface HerdrEvent {
    data class OpenPane(val sessionId: String, val serverId: String, val paneId: String) : HerdrEvent
    data class ShowMessage(val text: String) : HerdrEvent

    /** The running action finished: its dialog closes. */
    data object ActionDone : HerdrEvent
}
