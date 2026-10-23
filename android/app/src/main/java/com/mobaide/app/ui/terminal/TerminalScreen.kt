package com.mobaide.app.ui.terminal

import android.view.inputmethod.InputMethodManager
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.exclude
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Circle
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.rememberDrawerState
import com.mobaide.app.ui.theme.pixel
import com.mobaide.app.ui.theme.PxDarkRule
import com.mobaide.app.ui.theme.PxDarkSurface
import com.mobaide.app.ui.theme.PxDarkText
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.ui.common.pixel.PixelFab
import com.mobaide.app.ui.common.pixel.PixelIconButton
import com.mobaide.app.ui.common.pixel.PixelStatusDot
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.common.pixel.PixelScaffold
import androidx.compose.material3.SmallFloatingActionButton
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import com.mobaide.app.ui.common.pixel.PixelTopBar
import com.mobaide.app.ui.sessions.panel.SessionsDrawer
import com.mobaide.app.ui.sessions.panel.SessionsPanel
import com.mobaide.app.ui.sessions.panel.SessionsPanelEvent
import com.mobaide.app.ui.sessions.panel.SessionsPanelViewModel
import com.mobaide.app.ui.sessions.panel.StopSessionDialog
import com.mobaide.app.ui.sessions.panel.edgeSwipeToOpen
import com.mobaide.app.ui.sessions.sessionKindLabel
import com.mobaide.app.ui.sessions.sessionStateColor
import com.mobaide.app.ui.sessions.sessionTitle
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import com.mobaide.app.ui.theme.ChromeFontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.mobaide.app.data.voice.DictationState
import com.mobaide.app.data.voice.VoiceMicMode
import com.mobaide.app.ui.voice.VoiceDictationBar
import com.mobaide.app.ui.voice.VoiceEvent
import com.mobaide.app.ui.voice.VoiceMicButton
import com.mobaide.app.ui.voice.VoiceSetupSheet
import com.mobaide.app.ui.voice.VoiceViewModel
import com.mobaide.app.ui.voice.findActivity
import com.mobaide.app.ui.voice.rememberMicPermissionRequest
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.ui.theme.TerminalBackground
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/** Ceiling on how long a shortcut-sheet snackbar may hold the shared
 *  userMessages collector. Deliberately past Android's largest "Time to take
 *  action" accessibility timeout (2 min), which `SnackbarDuration.toMillis`
 *  honours: a smaller bound would cut those users' messages short while the
 *  host is alive and well. This is a stranded-collector backstop, not a
 *  duration control, so it must never be the thing that ends a snackbar. */
private const val SHEET_SNACKBAR_TIMEOUT_MS = 130_000L

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TerminalScreen(
    viewModel: TerminalViewModel,
    voiceViewModel: VoiceViewModel,
    onBack: () -> Unit,
    onSessionEnded: () -> Unit = onBack,
    panelViewModel: SessionsPanelViewModel = hiltViewModel(),
    onSwitchSession: (sessionId: String) -> Unit = {},
    onCreateSession: () -> Unit = {},
    onOpenGitChanges: () -> Unit = {},
    onOpenGitCommit: () -> Unit = {},
    onOpenGitSync: () -> Unit = {},
    onOpenGitBranches: (projectName: String) -> Unit = {},
    onOpenGitRebase: () -> Unit = {},
) {
    val sessionState by viewModel.sessionState.collectAsStateWithLifecycle()
    val sessionCwd by viewModel.sessionCwd.collectAsStateWithLifecycle()
    val sessionProjectName by viewModel.sessionProjectName.collectAsStateWithLifecycle()
    val gitCommitAvailable by viewModel.gitCommitAvailable.collectAsStateWithLifecycle()
    val gitSyncAvailable by viewModel.gitSyncAvailable.collectAsStateWithLifecycle()
    val gitBranchesAvailable by viewModel.gitBranchesAvailable.collectAsStateWithLifecycle()
    val gitRebaseAvailable by viewModel.gitRebaseAvailable.collectAsStateWithLifecycle()
    val isConnected by viewModel.isConnected.collectAsStateWithLifecycle()
    val connectionState by viewModel.connectionState.collectAsStateWithLifecycle()
    val followMode by viewModel.followMode.collectAsStateWithLifecycle()
    val unreadRows by viewModel.unreadRows.collectAsStateWithLifecycle()
    val inputText by viewModel.inputText.collectAsStateWithLifecycle()
    val requestInputFocus by viewModel.requestInputFocus.collectAsStateWithLifecycle()
    val templates by viewModel.templates.collectAsStateWithLifecycle()
    val templatesLoading by viewModel.templatesLoading.collectAsStateWithLifecycle()
    val displayConfig by viewModel.displayConfig.collectAsStateWithLifecycle()
    val sessionKind by viewModel.sessionKind.collectAsStateWithLifecycle()
    val headerLabel by viewModel.headerLabel.collectAsStateWithLifecycle()
    val composerVisible by viewModel.composerVisible.collectAsStateWithLifecycle()
    val shortcutRecents by viewModel.shortcutRecents.collectAsStateWithLifecycle()
    val savedShortcuts by viewModel.savedShortcutsForKind.collectAsStateWithLifecycle()
    val voiceState by voiceViewModel.uiState.collectAsStateWithLifecycle()
    var showFullScreenInput by remember { mutableStateOf(false) }
    var showTemplatesPicker by remember { mutableStateOf(false) }
    var showShortcutPicker by remember { mutableStateOf(false) }
    var showSaveTemplateDialog by remember { mutableStateOf(false) }
    var renameShortcutTarget by remember { mutableStateOf<SavedShortcutUi?>(null) }
    var terminalKeyboardRequest by remember { mutableStateOf(false) }
    val snackbarHostState = remember { SnackbarHostState() }
    val shortcutSheetSnackbarHostState = remember { SnackbarHostState() }
    val panelState by panelViewModel.uiState.collectAsStateWithLifecycle()
    val drawerState = rememberDrawerState(DrawerValue.Closed)
    val drawerScope = rememberCoroutineScope()
    var stopTarget by remember { mutableStateOf<SessionResponse?>(null) }

    LaunchedEffect(viewModel) {
        viewModel.sessionEnded.collect { onSessionEnded() }
    }

    LaunchedEffect(voiceViewModel) {
        voiceViewModel.events.collect { event ->
            when (event) {
                is VoiceEvent.InsertTranscript -> viewModel.insertTranscript(event.text)
            }
        }
    }

    // A tick when recording starts and when it stops.
    val voiceHaptics = LocalHapticFeedback.current
    val recording = voiceState.dictation is DictationState.Recording
    var wasRecording by remember { mutableStateOf(false) }
    LaunchedEffect(recording) {
        if (recording != wasRecording) {
            voiceHaptics.performHapticFeedback(HapticFeedbackType.LongPress)
            wasRecording = recording
        }
    }
    // Leaving the terminal (back, or another screen pushed over it) cancels a
    // dictation, so the microphone never stays open behind another screen; a
    // configuration change recreates this composition and must not.
    val voiceActivity = LocalContext.current.findActivity()
    DisposableEffect(voiceViewModel) {
        onDispose {
            if (voiceActivity?.isChangingConfigurations != true) voiceViewModel.cancelDictation()
        }
    }
    val requestMic = rememberMicPermissionRequest(
        onGranted = voiceViewModel::startDictation,
        onDenied = voiceViewModel::onMicPermissionDenied,
    )
    val micButton: (@Composable () -> Unit)? =
        if (voiceState.micMode == VoiceMicMode.Hidden) {
            null
        } else {
            {
                VoiceMicButton(
                    mode = voiceState.micMode,
                    status = voiceState.status,
                    onClick = {
                        if (voiceState.micMode == VoiceMicMode.Ready) requestMic() else voiceViewModel.showSetup()
                    },
                )
            }
        }
    val dictationBar: (@Composable () -> Unit)? =
        when (voiceState.dictation) {
            DictationState.Idle, is DictationState.Result -> null
            else -> {
                {
                    VoiceDictationBar(
                        state = voiceState.dictation,
                        agentName = voiceState.agentName,
                        onStop = voiceViewModel::stopDictation,
                        onCancel = voiceViewModel::cancelDictation,
                        onRetry = voiceViewModel::retryDictation,
                        onDismiss = voiceViewModel::dismissDictation,
                        onRecordAgain = requestMic,
                    )
                }
            }
        }

    LaunchedEffect(panelViewModel) {
        panelViewModel.events.collect { event ->
            when (event) {
                // `sessionEnded` never fires for a session this screen has seen
                // alive, so leaving the stopped one on screen is done here.
                is SessionsPanelEvent.Stopped -> if (event.sessionId == viewModel.sessionId) {
                    onSessionEnded()
                } else {
                    // Launched, not awaited: a suspended showSnackbar would hold
                    // back the next event until this one is dismissed.
                    drawerScope.launch { snackbarHostState.showSnackbar("Session stopped") }
                }
                is SessionsPanelEvent.StopFailed ->
                    drawerScope.launch { snackbarHostState.showSnackbar(event.message) }
            }
        }
    }

    LaunchedEffect(Unit) {
        // The Scaffold host sits behind the shortcut sheet's window and scrim,
        // so while that sheet is up its own save/remove failures would never be
        // seen. Route each message to whichever host is actually on top.
        viewModel.userMessages.collect { message ->
            if (showShortcutPicker) {
                // Backstop only. The sheet's own onDispose covers the reachable
                // case (host owns a snackbar, then the sheet goes away); the
                // read above and this suspend are one uninterrupted main-thread
                // run, so nothing can currently slip between them. This bounds
                // the damage anyway, because a stranded collector silently kills
                // every later message on this screen — too sharp an edge to
                // leave resting on an ordering argument.
                withTimeoutOrNull(SHEET_SNACKBAR_TIMEOUT_MS) {
                    shortcutSheetSnackbarHostState.showSnackbar(message)
                }
            } else {
                snackbarHostState.showSnackbar(message)
            }
        }
    }

    // STOPPED→STARTED edge: screen turned back on, or the user navigated
    // back to this terminal from another screen. If the WS dropped while
    // the activity was stopped (Doze restricts network on screen-off), the
    // session-WS backoff may sit at the 30 s ceiling — without this nudge
    // the user stares at the "Reconnecting…" overlay for up to half a
    // minute. The ViewModel skips when already Live.
    LifecycleEventEffect(Lifecycle.Event.ON_START) {
        viewModel.onScreenVisible()
    }

    // Tell the notification service which session is on screen so it suppresses
    // a redundant alert for the session the user is already viewing (alerts for
    // other sessions still fire). Cleared when this screen leaves composition.
    val view = LocalView.current
    DisposableEffect(Unit) {
        viewModel.markScreenVisible()
        onDispose {
            viewModel.markScreenHidden()
            // Leaving the session dismisses the keyboard. Hiding via the window token
            // covers both raise paths — the Compose composer and the native terminal
            // surface's InputConnection — since they share this window.
            view.context.getSystemService(InputMethodManager::class.java)
                ?.hideSoftInputFromWindow(view.windowToken, 0)
        }
    }

    // The drawer is not inset for the keyboard, so a raised IME would cover
    // its lower rows; both raise paths share this window token.
    val openDrawer = {
        view.context.getSystemService(InputMethodManager::class.java)
            ?.hideSoftInputFromWindow(view.windowToken, 0)
        drawerScope.launch { drawerState.open() }
        Unit
    }
    val closeDrawer = {
        drawerScope.launch { drawerState.close() }
        Unit
    }

    val stateColor = MaterialTheme.pixel.sessionStateColor(sessionState)

    SessionsDrawer(
        drawerState = drawerState,
        drawerContent = {
            SessionsPanel(
                state = panelState,
                currentSessionId = viewModel.sessionId,
                onSelectSession = onSwitchSession,
                onStopSession = { stopTarget = it },
                onCreateSession = {
                    closeDrawer()
                    onCreateSession()
                },
                onSelectAgent = panelViewModel::switchAgent,
                onClose = closeDrawer,
            )
        },
    ) {
        PixelScaffold(
            contentWindowInsets = WindowInsets.systemBars,
            snackbarHost = {
                PixelSnackbarHost(
                    snackbarHostState,
                    // Scaffold already insets this slot by systemBars; add only the part
                    // of the keyboard that exceeds them so snackbars float above the IME.
                    modifier = Modifier
                        .windowInsetsPadding(WindowInsets.ime.exclude(WindowInsets.systemBars)),
                )
            },
            topBar = {
                PixelTopBar(
                    title = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            PixelStatusDot(
                                color = if (isConnected) stateColor else MaterialTheme.pixel.statusStopped,
                                modifier = Modifier.semantics { contentDescription = sessionState },
                            )
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(
                                text = headerLabel,
                                style = MaterialTheme.typography.titleMedium.copy(
                                    fontFamily = ChromeFontFamily,
                                ),
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    },
                    navigationIcon = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            IconButton(onClick = onBack) {
                                Icon(
                                    Icons.AutoMirrored.Filled.ArrowBack,
                                    contentDescription = "Back",
                                )
                            }
                            PixelIconButton(
                                imageVector = Icons.Default.Menu,
                                contentDescription = "Sessions",
                                onClick = openDrawer,
                                compact = true,
                            )
                        }
                    },
                    actions = {
                        if (sessionCwd != null) {
                            val branchesProject = sessionProjectName
                            TerminalOverflowMenu(
                                onOpenGitChanges = onOpenGitChanges,
                                onOpenGitCommit = onOpenGitCommit,
                                commitAvailable = gitCommitAvailable,
                                onOpenGitSync = onOpenGitSync,
                                syncAvailable = gitSyncAvailable,
                                onOpenGitBranches = {
                                    if (branchesProject != null) onOpenGitBranches(branchesProject)
                                },
                                branchesAvailable = gitBranchesAvailable && branchesProject != null,
                                onOpenGitRebase = onOpenGitRebase,
                                rebaseAvailable = gitRebaseAvailable,
                            )
                        }
                    },
                )
            },
        ) { padding ->
            // Shared write-path gate: during replay/reconnect or on a stopped/crashed
            // session the ViewModel's input listener silently drops terminal bytes, so the
            // selection-menu Paste affordance that would feed that path is withheld rather
            // than offered and ignored.
            val sessionInputActive = isConnected && sessionState == "running" &&
                connectionState == ConnectionState.Live
            // Cursor-row bottom (px, view-local) reported by the terminal surface. Read ONLY
            // inside imeCursorCounterPan's placement block: per-output-frame writes then
            // re-run placement, never composition. MAX_VALUE (no report yet) means a zero
            // counter-pan, i.e. the plain full-pan behavior.
            val cursorAnchorBottomPx = remember { mutableIntStateOf(Int.MAX_VALUE) }
            // Clip host: its top edge is the app bar's bottom, so content panned up by
            // the keyboard is clipped instead of drawing over the app bar.
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .clipToBounds()
                    .edgeSwipeToOpen(enabled = !drawerState.isOpen, onOpen = openDrawer),
            ) {
                Column(
                    modifier = Modifier
                        .fillMaxSize()
                        // Pan, don't resize: the keyboard never changes the terminal's
                        // measured size (and thus never the grid or the remote PTY size) —
                        // the whole column just shifts up so the InputBar rides above the
                        // IME. The terminal surface inside counter-pans to keep the cursor
                        // row visible. See imeFloatingPan / imeCursorCounterPan.
                        .imeFloatingPan(WindowInsets.ime, WindowInsets.systemBars),
                ) {
                    Box(modifier = Modifier.weight(1f)) {
                        // clipToBounds: the counter-pan shifts the surface down by up to the
                        // pan amount; the overhang must not paint over the InputBar below.
                        Box(
                            modifier = Modifier
                                .fillMaxSize()
                                .background(TerminalBackground)
                                .clipToBounds(),
                        ) {
                            TerminalSurfaceView(
                                session = viewModel.session,
                                sessionCallback = viewModel.sessionCallback,
                                displayConfig = displayConfig,
                                // Ungated by design: a pre-connect layout pass must still cache the
                                // measured grid so enterLiveState()'s resendCachedSize() can deliver it
                                // on connect (matches iOS). resize() only enqueues the frame; it never
                                // touches the local emulator.
                                onResize = { rows, cols ->
                                    viewModel.resize(rows, cols)
                                },
                                onRowTapped = { text, isCurrentInput ->
                                    viewModel.sendTappedRow(text, isCurrentInput)
                                },
                                onRowCopyToInput = { text, _ ->
                                    viewModel.copyTextToInput(text)
                                },
                                onSelectionCopyToInput = { text ->
                                    viewModel.copyTextToInput(text)
                                },
                                onScrollSettled = viewModel::onUserScrollSettled,
                                onCursorAnchorChanged = { cursorAnchorBottomPx.intValue = it },
                                followMode = followMode,
                                requestKeyboard = terminalKeyboardRequest,
                                onKeyboardHandled = { terminalKeyboardRequest = false },
                                // Deliberately independent of composerVisible: pasting into the
                                // terminal is valid whether or not the composer is on screen.
                                pasteFromClipboardEnabled = sessionInputActive,
                                modifier = Modifier
                                    .fillMaxSize()
                                    .imeCursorCounterPan(WindowInsets.ime, WindowInsets.systemBars) {
                                        cursorAnchorBottomPx.intValue
                                    },
                            )
                            JumpToBottomFab(
                                visible = !followMode,
                                unreadRows = unreadRows,
                                onClick = viewModel::jumpToBottom,
                                modifier = Modifier
                                    .align(Alignment.BottomEnd)
                                    .padding(12.dp),
                            )
                        }
                    }
                    InputBar(
                        text = inputText,
                        onTextChange = viewModel::updateInputText,
                        onSend = { text -> viewModel.sendInput(text) },
                        onSendBytes = viewModel::sendBytes,
                        enabled = isConnected && sessionState == "running",
                        requestFocus = requestInputFocus,
                        onFocusHandled = viewModel::consumeFocusRequest,
                        onExpand = { showFullScreenInput = true },
                        onOpenTemplates = {
                            viewModel.refreshTemplates()
                            showTemplatesPicker = true
                        },
                        onOpenShortcuts = {
                            viewModel.refreshShortcutRecents()
                            viewModel.refreshSavedShortcuts()
                            showShortcutPicker = true
                        },
                        savedShortcuts = savedShortcuts,
                        onSendSavedShortcut = { viewModel.sendChord(it.chord) },
                        onRenameSavedShortcut = { renameShortcutTarget = it },
                        onRemoveSavedShortcut = { viewModel.removeShortcut(it.id) },
                        onSaveAsTemplate = { showSaveTemplateDialog = true },
                        sessionKind = sessionKind,
                        composerVisible = composerVisible,
                        onRevealComposer = viewModel::revealComposer,
                        onHideComposer = viewModel::hideComposer,
                        onRaiseTerminalKeyboard = { terminalKeyboardRequest = true },
                        micButton = micButton,
                        dictationBar = dictationBar,
                    )
                }
                // Stationary sibling of the panned Column: the reconnect/replay pill must
                // hold its screen position (and stay un-clipped) while the keyboard pans
                // the content underneath it.
                ConnectionOverlay(state = connectionState)
            }
        }
    }

    if (showFullScreenInput) {
        FullScreenInputSheet(
            text = inputText,
            onTextChange = viewModel::updateInputText,
            onSend = { text ->
                viewModel.sendInput(text)
                showFullScreenInput = false
            },
            onDismiss = { showFullScreenInput = false },
            enabled = isConnected && sessionState == "running",
            micButton = micButton,
            dictationBar = dictationBar,
        )
    }

    if (voiceState.setupVisible) {
        VoiceSetupSheet(
            state = voiceState,
            onDismiss = voiceViewModel::hideSetup,
            onEnable = voiceViewModel::install,
            onCancelInstall = voiceViewModel::cancelInstall,
            onRetry = voiceViewModel::install,
            onHideMic = { voiceViewModel.setMicHidden(true) },
        )
    }

    if (showTemplatesPicker) {
        TemplatesPickerSheet(
            templates = templates,
            isLoading = templatesLoading,
            onPick = { template ->
                viewModel.applyTemplate(template)
                showTemplatesPicker = false
            },
            onToggleFavorite = viewModel::toggleTemplateFavorite,
            onDelete = { template ->
                viewModel.deleteTemplate(template.template_id)
            },
            onDismiss = { showTemplatesPicker = false },
        )
    }

    if (showShortcutPicker) {
        ShortcutPickerSheet(
            recents = shortcutRecents,
            onSendChord = { chord ->
                viewModel.sendChord(chord)
                showShortcutPicker = false
            },
            onDismiss = { showShortcutPicker = false },
            enabled = isConnected && sessionState == "running",
            savedShortcuts = savedShortcuts,
            onSaveShortcut = viewModel::saveShortcut,
            onRenameSavedShortcut = { renameShortcutTarget = it },
            onRemoveSavedShortcut = { viewModel.removeShortcut(it.id) },
            snackbarHostState = shortcutSheetSnackbarHostState,
        )
    }

    renameShortcutTarget?.let { target ->
        RenameShortcutDialog(
            shortcut = target,
            onRename = { name ->
                viewModel.renameShortcut(target.id, name)
                renameShortcutTarget = null
            },
            onDismiss = { renameShortcutTarget = null },
        )
    }

    stopTarget?.let { target ->
        StopSessionDialog(
            label = sessionTitle(target, sessionKindLabel(target, panelState.customTypeNames)),
            onStop = { force ->
                panelViewModel.stopSession(target.session_id, force)
                stopTarget = null
            },
            onDismiss = { stopTarget = null },
        )
    }

    if (showSaveTemplateDialog) {
        SaveTemplateDialog(
            bodyPreview = inputText.text,
            onSave = { name ->
                viewModel.saveCurrentInputAsTemplate(name)
                showSaveTemplateDialog = false
            },
            onDismiss = { showSaveTemplateDialog = false },
        )
    }
}

@Composable
private fun JumpToBottomFab(
    visible: Boolean,
    unreadRows: Int,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn() + scaleIn(),
        exit = fadeOut() + scaleOut(),
        modifier = modifier,
    ) {
        BadgedBox(
            badge = {
                if (unreadRows > 0) {
                    Badge(
                        containerColor = MaterialTheme.pixel.brand,
                        contentColor = Color.White,
                    ) {
                        Text(text = if (unreadRows > 999) "999+" else unreadRows.toString())
                    }
                }
            },
        ) {
            PixelFab(onClick = onClick, modifier = Modifier.size(40.dp)) {
                Icon(
                    imageVector = Icons.Default.KeyboardArrowDown,
                    contentDescription = "Jump to bottom",
                )
            }
        }
    }
}

@Composable
private fun ConnectionOverlay(state: ConnectionState) {
    if (state == ConnectionState.Live) return
    val label = when (state) {
        ConnectionState.Reconnecting -> "Reconnecting\u2026"
        ConnectionState.ReplayingBuffer -> "Replaying buffer\u2026"
        // Rarely on screen: returning to the terminal resumes the stream in the
        // same pass that recomposes this. It exists so the state is honest for
        // the frame in between, and so the input gates read a suspend as "not
        // live" rather than as a dropped connection.
        ConnectionState.Suspended -> "Paused"
        ConnectionState.Live -> return
    }
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(12.dp),
        contentAlignment = Alignment.TopCenter,
    ) {
        // Floats over the always-dark terminal grid, so it hardcodes the dark
        // tokens instead of following the app theme.
        Box(
            modifier = Modifier
                .background(PxDarkSurface)
                .pixelFrame(PxDarkRule)
                .padding(horizontal = 16.dp, vertical = 6.dp),
        ) {
            Text(
                text = label.chromeCaps(),
                color = PxDarkText,
                style = MaterialTheme.typography.labelMedium,
            )
        }
    }
}
