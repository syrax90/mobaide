package com.mobaide.app.ui.herdr

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.ui.common.pixel.PixelBottomSheet
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.terminal.TerminalPaneChrome
import com.mobaide.app.ui.terminal.TerminalScreen
import com.mobaide.app.ui.terminal.TerminalViewModel
import com.mobaide.app.ui.theme.MobaideTheme
import androidx.compose.ui.text.style.TextOverflow
import com.mobaide.app.ui.voice.VoiceViewModel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.receiveAsFlow

const val HERDR_HISTORY_TEXT_TAG = "herdr_history_text"

/**
 * One Herdr pane, live: the terminal screen on the pane's session, with the
 * pane's own top-bar actions (the watch/control switch, the history).
 */
@Composable
fun HerdrPaneRoute(
    onBack: () -> Unit,
    terminalViewModel: TerminalViewModel = hiltViewModel(),
    voiceViewModel: VoiceViewModel = hiltViewModel(),
    paneViewModel: HerdrPaneViewModel = hiltViewModel(),
) {
    val pane by paneViewModel.uiState.collectAsStateWithLifecycle()
    val paneState by terminalViewModel.paneState.collectAsStateWithLifecycle()
    val sessionState by terminalViewModel.sessionState.collectAsStateWithLifecycle()
    // Control belongs to one client; another device's control leaves this one a watcher.
    val controlling = paneState?.isControl == true
    val controlledElsewhere = paneState?.isControlledElsewhere == true
    val messages = androidx.compose.runtime.remember { Channel<String>(Channel.BUFFERED) }

    LaunchedEffect(controlling) { paneViewModel.onModeChanged(controlling) }

    LaunchedEffect(sessionState) {
        when (sessionState) {
            "stopped" -> paneViewModel.onSessionStopped()
            "running" -> paneViewModel.onSessionRunning()
        }
    }

    LaunchedEffect(paneViewModel) {
        paneViewModel.events.collect { event ->
            when (event) {
                is HerdrPaneEvent.ShowMessage -> messages.send(event.text)
                HerdrPaneEvent.Reattach -> terminalViewModel.reattach()
                HerdrPaneEvent.PaneGone -> onBack()
            }
        }
    }

    DisposableEffect(paneViewModel) {
        paneViewModel.markScreenVisible()
        onDispose { paneViewModel.markScreenHidden() }
    }

    TerminalScreen(
        viewModel = terminalViewModel,
        voiceViewModel = voiceViewModel,
        onBack = onBack,
        pane = TerminalPaneChrome(
            title = pane.title,
            notice = when {
                controlling -> "You control this pane. It has the size of this screen, also on your computer."
                controlledElsewhere -> "Another device controls this pane. It has the size of that device."
                !pane.typeInWatch -> "Watching. Use the message box, or take control to type freely."
                else -> null
            },
            canControl = pane.canControl,
            controlling = controlling,
            controlBusy = pane.controlBusy,
            onToggleControl = { paneViewModel.setControl(!controlling) },
            onOpenHistory = paneViewModel::openHistory,
        ),
        paneMessages = androidx.compose.runtime.remember(messages) { messages.receiveAsFlow() },
    )

    pane.history?.let { history ->
        HerdrHistorySheet(
            title = pane.title,
            state = history,
            onRefresh = paneViewModel::openHistory,
            onDismiss = paneViewModel::closeHistory,
        )
    }
}

/**
 * The pane's recent output as plain text. The live view shows only the screen,
 * so this is how earlier output is read and copied.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HerdrHistorySheet(
    title: String,
    state: HerdrHistoryState,
    onRefresh: () -> Unit,
    onDismiss: () -> Unit,
    modifier: Modifier = Modifier,
) {
    PixelBottomSheet(
        onDismissRequest = onDismiss,
        modifier = modifier,
        sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true),
    ) {
        Column(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = title,
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                PixelButton(
                    text = "Refresh",
                    onClick = onRefresh,
                    enabled = state != HerdrHistoryState.Loading,
                    compact = true,
                )
            }
            when (state) {
                HerdrHistoryState.Loading -> Text(
                    "Reading the pane…",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                is HerdrHistoryState.Failed -> Text(state.message, color = MaterialTheme.colorScheme.error)
                is HerdrHistoryState.Loaded -> {
                    if (state.truncated) {
                        Text(
                            "Older lines exist. This is the last part.",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    SelectionContainer(
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = 120.dp)
                            // Reversed: a terminal's news is at its end, so the sheet opens there.
                            .verticalScroll(rememberScrollState(), reverseScrolling = true)
                            .horizontalScroll(rememberScrollState())
                            .testTag(HERDR_HISTORY_TEXT_TAG),
                    ) {
                        Text(
                            text = state.text.ifEmpty { "The pane is empty." },
                            style = MaterialTheme.typography.bodySmall.copy(fontFamily = FontFamily.Monospace),
                            softWrap = false,
                        )
                    }
                }
            }
        }
    }
}

@Preview(name = "Herdr history dark", showBackground = true, backgroundColor = 0xFF121212)
@Composable
private fun HerdrHistorySheetPreviewDark() {
    MobaideTheme(darkTheme = true) {
        HerdrHistorySheet(
            title = "api refactor",
            state = HerdrHistoryState.Loaded("$ npm test\n  42 passing\n$ ", truncated = true),
            onRefresh = {},
            onDismiss = {},
        )
    }
}

@Preview(name = "Herdr history light", showBackground = true, backgroundColor = 0xFFF4F4F4)
@Composable
private fun HerdrHistorySheetPreviewLight() {
    MobaideTheme(darkTheme = false) {
        HerdrHistorySheet(
            title = "api refactor",
            state = HerdrHistoryState.Failed("This pane is closed."),
            onRefresh = {},
            onDismiss = {},
        )
    }
}
