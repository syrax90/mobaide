package com.mobaide.app.ui.terminal

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.imeAnimationTarget
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
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SmallFloatingActionButton
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.ui.theme.StateCrashed
import com.mobaide.app.ui.theme.StateRunning
import com.mobaide.app.ui.theme.StateStarting
import com.mobaide.app.ui.theme.StateStopped
import com.mobaide.app.ui.theme.StateStopping
import com.mobaide.app.ui.theme.TerminalBackground

@OptIn(ExperimentalMaterial3Api::class, ExperimentalLayoutApi::class)
@Composable
fun TerminalScreen(
    viewModel: TerminalViewModel,
    onBack: () -> Unit,
) {
    val sessionState by viewModel.sessionState.collectAsStateWithLifecycle()
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
    val composerVisible by viewModel.composerVisible.collectAsStateWithLifecycle()
    val showComposerCoachMark by viewModel.showComposerCoachMark.collectAsStateWithLifecycle()
    var showFullScreenInput by remember { mutableStateOf(false) }
    var showTemplatesPicker by remember { mutableStateOf(false) }
    var showSaveTemplateDialog by remember { mutableStateOf(false) }
    var terminalKeyboardRequest by remember { mutableStateOf(false) }
    val snackbarHostState = remember { SnackbarHostState() }

    LaunchedEffect(Unit) {
        viewModel.userMessages.collect { snackbarHostState.showSnackbar(it) }
    }

    // Direct-input mode: the ViewModel asks for the IME to be raised on the terminal
    // surface itself (its InputConnection routes typing straight to the PTY).
    LaunchedEffect(Unit) {
        viewModel.terminalKeyboardRaise.collect { terminalKeyboardRequest = true }
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
    DisposableEffect(Unit) {
        viewModel.markScreenVisible()
        onDispose { viewModel.markScreenHidden() }
    }

    val stateColor = when (sessionState) {
        "running" -> StateRunning
        "starting" -> StateStarting
        "stopping" -> StateStopping
        "stopped" -> StateStopped
        "crashed" -> StateCrashed
        else -> StateStopped
    }

    Scaffold(
        contentWindowInsets = WindowInsets.systemBars,
        snackbarHost = { SnackbarHost(snackbarHostState) },
        topBar = {
            TopAppBar(
                title = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Icon(
                            Icons.Default.Circle,
                            contentDescription = sessionState,
                            tint = if (isConnected) stateColor else StateStopped,
                            modifier = Modifier.size(10.dp),
                        )
                        Spacer(modifier = Modifier.width(8.dp))
                        Text(
                            text = sessionState.replaceFirstChar { it.uppercase() },
                            style = MaterialTheme.typography.titleMedium.copy(
                                fontFamily = FontFamily.Monospace,
                            ),
                        )
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "Back",
                        )
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                // imeAnimationTarget instead of imePadding: pad by the IME animation's
                // END state so the terminal resizes in ONE jump instead of animating
                // through intermediate heights — each intermediate height reflows the
                // emulator and (after debounce) resizes the remote PTY, which makes the
                // whole transcript visibly shift while the keyboard animates. At rest
                // the two insets are identical.
                .windowInsetsPadding(WindowInsets.imeAnimationTarget),
        ) {
            BoxWithConstraints(modifier = Modifier.weight(1f)) {
                Box(modifier = Modifier.fillMaxSize().background(TerminalBackground)) {
                    TerminalSurfaceView(
                        session = viewModel.session,
                        sessionCallback = viewModel.sessionCallback,
                        displayConfig = displayConfig,
                        onResize = { rows, cols ->
                            if (isConnected) viewModel.resize(rows, cols)
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
                        followMode = followMode,
                        requestKeyboard = terminalKeyboardRequest,
                        onKeyboardHandled = { terminalKeyboardRequest = false },
                        // Same connectivity gate as the composer's `enabled` below: a tap
                        // must not raise an IME whose keystrokes would feed a stopped or
                        // disconnected session.
                        tapToRaiseKeyboard = !composerVisible &&
                            isConnected && sessionState == "running",
                        onTapToRaiseKeyboard = { terminalKeyboardRequest = true },
                        modifier = Modifier.fillMaxSize(),
                    )
                    ConnectionOverlay(state = connectionState)
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
                onSaveAsTemplate = { showSaveTemplateDialog = true },
                sessionKind = sessionKind,
                composerVisible = composerVisible,
                onRevealComposer = viewModel::revealComposer,
                onHideComposer = viewModel::hideComposer,
                onRaiseTerminalKeyboard = { terminalKeyboardRequest = true },
                showCoachMark = showComposerCoachMark,
                onCoachMarkDismissed = viewModel::markCoachMarkSeen,
            )
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
                    Badge { Text(text = if (unreadRows > 999) "999+" else unreadRows.toString()) }
                }
            },
        ) {
            SmallFloatingActionButton(onClick = onClick) {
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
    val (label, bg) = when (state) {
        ConnectionState.Reconnecting -> "Reconnecting\u2026" to Color(0xCC202020)
        ConnectionState.ReplayingBuffer -> "Replaying buffer\u2026" to Color(0xCC2A4D6E)
        ConnectionState.Live -> return
    }
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(12.dp),
        contentAlignment = Alignment.TopCenter,
    ) {
        Box(
            modifier = Modifier
                .clip(RoundedCornerShape(50))
                .background(bg)
                .padding(horizontal = 16.dp, vertical = 6.dp),
        ) {
            Text(
                text = label,
                color = Color.White,
                style = MaterialTheme.typography.labelMedium.copy(
                    fontFamily = FontFamily.Monospace,
                ),
            )
        }
    }
}
