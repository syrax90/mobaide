package com.mobaide.app.ui.chat

import android.view.inputmethod.InputMethodManager
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.exclude
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.ime
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.systemBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.outlined.CenterFocusStrong
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material.icons.outlined.RestartAlt
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.mobaide.app.data.chat.ChatConfigOption
import com.mobaide.app.data.chat.ChatRequest
import com.mobaide.app.data.chat.ChatUsage
import com.mobaide.app.data.chat.ChatStatus
import com.mobaide.app.data.network.dto.SessionResponse
import com.mobaide.app.ui.common.pixel.PixelButton
import com.mobaide.app.ui.common.pixel.PixelButtonStyle
import com.mobaide.app.ui.common.pixel.PixelIconButton
import com.mobaide.app.ui.common.pixel.PixelScaffold
import com.mobaide.app.ui.common.pixel.PixelSnackbarHost
import com.mobaide.app.ui.common.pixel.PixelStatusDot
import com.mobaide.app.ui.common.pixel.PixelTopBar
import com.mobaide.app.ui.common.pixel.pixelFrame
import com.mobaide.app.ui.sessions.panel.SessionsDrawer
import com.mobaide.app.ui.sessions.panel.SessionsPanel
import com.mobaide.app.ui.sessions.panel.SessionsPanelEvent
import com.mobaide.app.ui.sessions.panel.SessionsPanelViewModel
import com.mobaide.app.ui.sessions.panel.StopSessionDialog
import com.mobaide.app.ui.sessions.panel.edgeSwipeToOpen
import com.mobaide.app.ui.sessions.sessionKindLabel
import com.mobaide.app.ui.sessions.sessionTitle
import com.mobaide.app.ui.terminal.FullScreenInputSheet
import com.mobaide.app.ui.terminal.SaveTemplateDialog
import com.mobaide.app.ui.terminal.TemplatesPickerSheet
import com.mobaide.app.ui.terminal.TerminalOverflowMenu
import com.mobaide.app.ui.theme.ChromeFontFamily
import com.mobaide.app.ui.theme.PxDarkRule
import com.mobaide.app.ui.theme.PxDarkSurface
import com.mobaide.app.ui.theme.PxDarkText
import com.mobaide.app.ui.theme.chromeCaps
import com.mobaide.app.ui.theme.pixel
import com.mobaide.app.ui.voice.VoiceViewModel
import com.mobaide.app.ui.voice.rememberVoiceComposerSlots
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** The bottom item counts as "at the latest" within this many pixels of its edge. */
private const val AT_BOTTOM_SLOP_PX = 48

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(
    viewModel: ChatViewModel,
    voiceViewModel: VoiceViewModel,
    onBack: () -> Unit,
    onSessionEnded: () -> Unit = onBack,
    panelViewModel: SessionsPanelViewModel = hiltViewModel(),
    onSwitchSession: (sessionId: String) -> Unit = {},
    onSwitchAfterStop: (sessionId: String, stoppedSessionId: String) -> Unit = { id, _ -> onSwitchSession(id) },
    stoppedSessionId: String? = null,
    onCreateSession: () -> Unit = {},
    onOpenGitChanges: () -> Unit = {},
    onOpenGitCommit: () -> Unit = {},
    onOpenGitSync: () -> Unit = {},
    onOpenGitBranches: (projectName: String) -> Unit = {},
    onOpenGitRebase: () -> Unit = {},
) {
    val ui by viewModel.uiState.collectAsStateWithLifecycle()
    val composer by viewModel.composer.collectAsStateWithLifecycle()
    val header by viewModel.header.collectAsStateWithLifecycle()
    val git by viewModel.git.collectAsStateWithLifecycle()
    val templates by viewModel.templates.collectAsStateWithLifecycle()
    val templatesLoading by viewModel.templatesLoading.collectAsStateWithLifecycle()
    val focusView by viewModel.focusView.collectAsStateWithLifecycle()
    val panelState by panelViewModel.uiState.collectAsStateWithLifecycle()

    val snackbarHostState = remember { SnackbarHostState() }
    val drawerState = rememberDrawerState(if (stoppedSessionId != null) DrawerValue.Open else DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val view = LocalView.current

    var pickerOptionId by rememberSaveable { mutableStateOf<String?>(null) }
    var showSettings by rememberSaveable { mutableStateOf(false) }
    var showTemplates by remember { mutableStateOf(false) }
    var showSaveTemplate by remember { mutableStateOf(false) }
    var showInfo by remember { mutableStateOf(false) }
    var showFullScreenInput by remember { mutableStateOf(false) }
    var stopTarget by remember { mutableStateOf<SessionResponse?>(null) }
    var closeSelf by remember { mutableStateOf(false) }

    LaunchedEffect(viewModel) { viewModel.sessionEnded.collect { onSessionEnded() } }
    LaunchedEffect(viewModel) { viewModel.messages.collect { snackbarHostState.showSnackbar(it) } }
    LaunchedEffect(panelViewModel) {
        panelViewModel.events.collect { event ->
            when (event) {
                is SessionsPanelEvent.Stopped -> if (event.sessionId == viewModel.sessionId) {
                    viewModel.markEndedByUser()
                    val next = event.nextSessionId
                    when {
                        next == null -> onSessionEnded()
                        drawerState.isOpen -> onSwitchAfterStop(next, event.sessionId)
                        else -> onSwitchSession(next)
                    }
                } else {
                    scope.launch { snackbarHostState.showSnackbar("Session stopped") }
                }
                is SessionsPanelEvent.StopFailed -> {
                    viewModel.noteReviewError()
                    scope.launch { snackbarHostState.showSnackbar(event.message) }
                }
            }
        }
    }

    LifecycleEventEffect(Lifecycle.Event.ON_START) { viewModel.onScreenVisible() }
    DisposableEffect(Unit) {
        viewModel.markScreenVisible()
        onDispose {
            viewModel.markScreenHidden()
            view.context.getSystemService(InputMethodManager::class.java)
                ?.hideSoftInputFromWindow(view.windowToken, 0)
        }
    }

    val voice = rememberVoiceComposerSlots(
        voiceViewModel,
        sessionKind = header.kind,
        onTranscript = viewModel::insertTranscript,
        // Launched, not awaited: a suspended showSnackbar would hold back the
        // next transcript.
        onNotice = { notice -> scope.launch { snackbarHostState.showSnackbar(notice) } },
    )
    val pickImages = rememberLauncherForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(maxItems = 4),
    ) { uris -> viewModel.addImages(uris) }

    var stickToBottom by rememberSaveable { mutableStateOf(true) }
    var hasNew by remember { mutableStateOf(false) }
    val atBottom by remember {
        derivedStateOf { listState.firstVisibleItemIndex == 0 && listState.firstVisibleItemScrollOffset <= AT_BOTTOM_SLOP_PX }
    }
    LaunchedEffect(listState) {
        snapshotFlow { listState.isScrollInProgress }.collect { scrolling ->
            stickToBottom = !scrolling && listState.firstVisibleItemIndex == 0 &&
                listState.firstVisibleItemScrollOffset <= AT_BOTTOM_SLOP_PX
            if (stickToBottom) hasNew = false
        }
    }
    val newest = ui.items.lastOrNull()
    val newestId = newest?.id
    LaunchedEffect(newestId) {
        if (newestId == null) return@LaunchedEffect
        if (stickToBottom) listState.scrollToItem(0) else hasNew = true
    }
    LaunchedEffect(newest) {
        if (newest != null && !stickToBottom && !atBottom) hasNew = true
    }
    val jumpToLatest: () -> Unit = {
        scope.launch {
            listState.animateScrollToItem(0)
            stickToBottom = true
            hasNew = false
        }
    }

    val running = ui.running
    val now by produceState(System.currentTimeMillis(), running, ui.state.turnStartedAt) {
        value = System.currentTimeMillis()
        while (running) {
            delay(1_000)
            value = System.currentTimeMillis()
        }
    }
    val statusLine = remember(ui.live, ui.state, ui.sessionClosed, now) {
        if (ui.sessionClosed) {
            ChatStatusLine("Session closed", ChatStatusTone.Muted)
        } else {
            chatStatusLine(ui.live, ui.state, now)
        }
    }

    val openDrawer = {
        view.context.getSystemService(InputMethodManager::class.java)
            ?.hideSoftInputFromWindow(view.windowToken, 0)
        scope.launch { drawerState.open() }
        Unit
    }
    val closeDrawer = {
        scope.launch { drawerState.close() }
        Unit
    }

    SessionsDrawer(
        drawerState = drawerState,
        drawerContent = {
            SessionsPanel(
                state = panelState,
                currentSessionId = viewModel.sessionId,
                departingSessionId = stoppedSessionId,
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
                    modifier = Modifier.windowInsetsPadding(WindowInsets.ime.exclude(WindowInsets.systemBars)),
                )
            },
            topBar = {
                ChatTopBar(
                    title = header.title,
                    statusLine = statusLine,
                    dotColor = chatDotColor(ui),
                    usage = ui.state.usage,
                    onBack = onBack,
                    onOpenSessions = openDrawer,
                    menu = {
                        val branchesProject = header.projectName
                        TerminalOverflowMenu(
                            onOpenGitChanges = onOpenGitChanges,
                            onOpenGitCommit = onOpenGitCommit,
                            commitAvailable = git.commit,
                            onOpenGitSync = onOpenGitSync,
                            syncAvailable = git.hasSync,
                            onOpenGitBranches = { if (branchesProject != null) onOpenGitBranches(branchesProject) },
                            branchesAvailable = git.branches && branchesProject != null,
                            onOpenGitRebase = onOpenGitRebase,
                            rebaseAvailable = git.rebase,
                            showGit = header.cwd != null,
                            sessionItems = { closeMenu ->
                                if (ui.state.status == ChatStatus.ERROR && !ui.sessionClosed) {
                                    DropdownMenuItem(
                                        text = { Text("Restart agent") },
                                        leadingIcon = { Icon(Icons.Outlined.RestartAlt, contentDescription = null) },
                                        enabled = ui.canRestart,
                                        onClick = {
                                            closeMenu()
                                            viewModel.restart()
                                        },
                                    )
                                }
                                DropdownMenuItem(
                                    text = { Text("Focus view") },
                                    leadingIcon = { Icon(Icons.Outlined.CenterFocusStrong, contentDescription = null) },
                                    trailingIcon = {
                                        if (focusView) Icon(Icons.Default.Check, contentDescription = null)
                                    },
                                    modifier = Modifier.semantics {
                                        stateDescription = if (focusView) "On" else "Off"
                                    },
                                    onClick = {
                                        closeMenu()
                                        viewModel.setFocusView(!focusView)
                                    },
                                )
                                DropdownMenuItem(
                                    text = { Text("Session info") },
                                    leadingIcon = { Icon(Icons.Outlined.Info, contentDescription = null) },
                                    onClick = {
                                        closeMenu()
                                        showInfo = true
                                    },
                                )
                                DropdownMenuItem(
                                    text = { Text("Close session") },
                                    leadingIcon = { Icon(Icons.Outlined.Close, contentDescription = null) },
                                    enabled = !ui.sessionClosed,
                                    onClick = {
                                        closeMenu()
                                        closeSelf = true
                                    },
                                )
                            },
                        )
                    },
                )
            },
        ) { padding ->
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .padding(padding)
                    .windowInsetsPadding(WindowInsets.ime.exclude(WindowInsets.systemBars))
                    .edgeSwipeToOpen(enabled = !drawerState.isOpen, onOpen = openDrawer),
            ) {
                Box(modifier = Modifier.weight(1f)) {
                    val agentName = ui.state.agent?.title ?: header.kindLabel
                    val status = ui.status
                    when {
                        !ui.loaded -> ChatLoadingState("Opening the session…")
                        ui.items.isEmpty() && status == ChatStatus.STARTING ->
                            ChatLoadingState(if (agentName.isBlank()) "Starting…" else "Starting $agentName…")
                        ui.items.isEmpty() && status == ChatStatus.LOADING -> ChatLoadingState("Opening the session…")
                        ui.items.isEmpty() && status == ChatStatus.ERROR -> ChatStartError(
                            message = ui.state.error ?: "The agent stopped.",
                            canRestart = ui.live && !ui.sessionClosed,
                            restartEnabled = ui.canRestart,
                            onRestart = viewModel::restart,
                        )
                        ui.items.isEmpty() && !ui.truncated -> ChatEmptyState(
                            title = agentName,
                            cwd = ui.state.cwd ?: header.cwd,
                            hasCommands = ui.state.commands.isNotEmpty(),
                        )
                        else -> ChatTimeline(
                            ui = ui,
                            listState = listState,
                            onRestart = viewModel::restart,
                            focusView = focusView,
                        )
                    }
                    JumpToLatestFab(
                        visible = ui.items.isNotEmpty() && !atBottom,
                        hasNew = hasNew,
                        onClick = jumpToLatest,
                        modifier = Modifier
                            .align(Alignment.BottomEnd)
                            .padding(12.dp),
                    )
                    if (!ui.live && ui.loaded && !ui.sessionClosed) ReconnectingBanner()
                }
                ChatDockArea(
                    ui = ui,
                    composer = composer,
                    kindLabel = header.kindLabel,
                    micButton = voice.micButton,
                    dictationBar = voice.dictationBar,
                    onComposerChange = viewModel::updateComposer,
                    onPickConfig = { pickerOptionId = it.id },
                    onOpenSettings = { showSettings = true },
                    onAttach = {
                        pickImages.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
                    },
                    onOpenTemplates = {
                        viewModel.refreshTemplates()
                        showTemplates = true
                    },
                    onSaveTemplate = { showSaveTemplate = true },
                    onExpand = { showFullScreenInput = true },
                    onSend = {
                        viewModel.sendPrompt()
                        stickToBottom = true
                        scope.launch { listState.scrollToItem(0) }
                    },
                    onStop = viewModel::stop,
                    onRemoveImage = viewModel::removeImage,
                    onDequeue = viewModel::dequeue,
                    onSendNow = viewModel::steer,
                    onPermission = viewModel::respondPermission,
                    onFormValue = viewModel::updateFormValue,
                    onFormAction = viewModel::respondForm,
                )
            }
        }
    }

    val pickerOption = pickerOptionId?.let { id -> ui.config.firstOrNull { it.id == id } }
    LaunchedEffect(pickerOptionId, pickerOption == null) {
        if (pickerOptionId != null && pickerOption == null) pickerOptionId = null
    }
    pickerOption?.let { option ->
        ChatConfigPickerSheet(
            option = option,
            onPick = { value ->
                pickerOptionId = null
                if (value != option.selectValue) viewModel.setConfig(option, value)
            },
            onDismiss = { pickerOptionId = null },
        )
    }

    if (showSettings) {
        val chips = remember(ui.config) { primaryConfigChips(ui.config).map(ChatConfigOption::id).toSet() }
        ChatSettingsSheet(
            options = ui.config.filter { it.id !in chips },
            enabled = ui.canAct,
            onSelect = { option, value -> viewModel.setConfig(option, value) },
            onToggle = { option, value -> viewModel.setConfig(option, value) },
            onDismiss = { showSettings = false },
        )
    }

    if (showTemplates) {
        TemplatesPickerSheet(
            templates = templates,
            isLoading = templatesLoading,
            onPick = { template ->
                viewModel.applyTemplate(template)
                showTemplates = false
            },
            onToggleFavorite = viewModel::toggleTemplateFavorite,
            onDelete = { viewModel.deleteTemplate(it.template_id) },
            onDismiss = { showTemplates = false },
        )
    }

    if (showSaveTemplate) {
        SaveTemplateDialog(
            bodyPreview = composer.text,
            onSave = { name ->
                viewModel.saveComposerAsTemplate(name)
                showSaveTemplate = false
            },
            onDismiss = { showSaveTemplate = false },
        )
    }

    if (showFullScreenInput) {
        FullScreenInputSheet(
            text = composer,
            onTextChange = viewModel::updateComposer,
            onSend = {
                viewModel.sendPrompt()
                showFullScreenInput = false
                stickToBottom = true
                scope.launch { listState.scrollToItem(0) }
            },
            onDismiss = { showFullScreenInput = false },
            enabled = !ui.sessionClosed,
            micButton = voice.micButton,
            dictationBar = voice.dictationBar,
            // The sheet shows no thumbnails, so it also wants text: images alone go from the dock.
            sendEnabled = ui.canSend(composer.text),
            keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences),
            placeholder = "Write your message…",
            sendLabel = if (ui.running) "Queue message" else "Send",
        )
    }

    if (showInfo) {
        ChatSessionInfoDialog(
            agent = ui.state.agent,
            cwd = ui.state.cwd ?: header.cwd,
            acpSessionId = ui.state.acpSessionId,
            onDismiss = { showInfo = false },
        )
    }

    if (closeSelf) {
        StopSessionDialog(
            label = header.title,
            onStop = { force ->
                panelViewModel.stopSession(viewModel.sessionId, force)
                closeSelf = false
            },
            onDismiss = { closeSelf = false },
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
}

@Composable
private fun ChatDockArea(
    ui: ChatUiState,
    composer: TextFieldValue,
    kindLabel: String,
    micButton: (@Composable () -> Unit)?,
    dictationBar: (@Composable () -> Unit)?,
    onComposerChange: (TextFieldValue) -> Unit,
    onPickConfig: (ChatConfigOption) -> Unit,
    onOpenSettings: () -> Unit,
    onAttach: () -> Unit,
    onOpenTemplates: () -> Unit,
    onSaveTemplate: () -> Unit,
    onExpand: () -> Unit,
    onSend: () -> Unit,
    onStop: () -> Unit,
    onRemoveImage: (Long) -> Unit,
    onDequeue: (String) -> Unit,
    onSendNow: (String) -> Unit,
    onPermission: (ChatRequest.Permission, String) -> Unit,
    onFormValue: (ChatRequest.Form, String, ChatFormValue) -> Unit,
    onFormAction: (ChatRequest.Form, String) -> Unit,
) {
    val commands = ui.state.commands
    val suggestions = remember(composer.text, commands) { slashSuggestions(composer.text, commands) }
    val commandHint = remember(composer.text, commands) {
        val text = composer.text
        if (text.startsWith("/") && text.endsWith(" ") && text.count { it.isWhitespace() } == 1) {
            commands.firstOrNull { "/${it.name} " == text }?.hint
        } else {
            null
        }
    }
    val requests = ui.state.requests
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.background),
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth()
                .height(2.dp)
                .background(MaterialTheme.colorScheme.outlineVariant),
        )
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            requests.firstOrNull()?.let { request ->
                ChatRequestCard(
                    request = request,
                    moreWaiting = requests.size - 1,
                    answering = ui.answering[request.id],
                    error = ui.requestErrors[request.id],
                    draft = ui.formDrafts[request.id],
                    enabled = ui.live && !ui.sessionClosed,
                    onPermission = onPermission,
                    onFormValue = onFormValue,
                    onFormAction = onFormAction,
                )
            }
            if (ui.state.plan.isNotEmpty()) ChatPlanBar(plan = ui.state.plan)
            if (ui.state.queue.isNotEmpty()) {
                ChatQueueRows(
                    queue = ui.state.queue,
                    enabled = ui.live,
                    onDequeue = onDequeue,
                    onSendNow = onSendNow.takeIf {
                        ui.state.steering && ui.state.status == ChatStatus.RUNNING
                    },
                    busy = ui.queueBusy,
                )
            }
            if (suggestions != null) {
                SlashSuggestions(
                    suggestions = suggestions,
                    onPick = { command ->
                        val text = "/${command.name} "
                        onComposerChange(TextFieldValue(text, TextRange(text.length)))
                    },
                )
            } else {
                ChatConfigChips(
                    config = ui.config,
                    enabled = ui.canAct,
                    onPick = onPickConfig,
                    onOpenSettings = onOpenSettings,
                )
            }
            ChatComposer(
                value = composer,
                onValueChange = onComposerChange,
                placeholder = if (kindLabel.isBlank()) "Message…" else "Message $kindLabel…",
                commandHint = commandHint,
                inputEnabled = !ui.sessionClosed,
                canAct = ui.canAct,
                running = ui.running,
                images = ui.images,
                imagesLoading = ui.imagesLoading,
                showAttach = ui.state.promptImages,
                onAttach = onAttach,
                onRemoveImage = onRemoveImage,
                onOpenTemplates = onOpenTemplates,
                onSaveTemplate = onSaveTemplate,
                onSend = onSend,
                onStop = onStop,
                micButton = micButton,
                dictationBar = dictationBar,
                onExpand = onExpand,
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatTopBar(
    title: String,
    statusLine: ChatStatusLine,
    dotColor: Color,
    usage: ChatUsage?,
    onBack: () -> Unit,
    onOpenSessions: () -> Unit,
    menu: @Composable () -> Unit,
) {
    PixelTopBar(
        title = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                PixelStatusDot(color = dotColor)
                Spacer(modifier = Modifier.width(8.dp))
                Column {
                    Text(
                        text = title,
                        style = MaterialTheme.typography.titleMedium.copy(fontFamily = ChromeFontFamily),
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        text = statusLine.text,
                        style = MaterialTheme.typography.bodySmall,
                        color = when (statusLine.tone) {
                            ChatStatusTone.Accent -> MaterialTheme.colorScheme.primary
                            ChatStatusTone.Danger -> MaterialTheme.pixel.danger
                            ChatStatusTone.Muted -> MaterialTheme.colorScheme.onSurfaceVariant
                        },
                        maxLines = 1,
                        // Announces state changes only, not every tick of the elapsed time.
                        modifier = Modifier.semantics {
                            contentDescription = statusLine.text.substringBefore(" · ")
                            liveRegion = LiveRegionMode.Polite
                        },
                    )
                }
            }
        },
        navigationIcon = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton(onClick = onBack) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                }
                PixelIconButton(
                    imageVector = Icons.Default.Menu,
                    contentDescription = "Sessions",
                    onClick = onOpenSessions,
                    compact = true,
                )
            }
        },
        actions = {
            if (usage != null) ChatUsageIndicator(usage)
            menu()
        },
    )
}

@Composable
private fun chatDotColor(ui: ChatUiState): Color {
    val pixel = MaterialTheme.pixel
    if (!ui.live || ui.sessionClosed) return pixel.statusStopped
    return when (ui.state.status) {
        ChatStatus.STARTING, ChatStatus.LOADING -> pixel.statusStarting
        ChatStatus.ERROR -> pixel.statusCrashed
        ChatStatus.STOPPED -> pixel.statusStopped
        else -> pixel.statusRunning
    }
}

@Composable
private fun ChatStartError(
    message: String,
    canRestart: Boolean,
    restartEnabled: Boolean,
    onRestart: () -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = message,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.pixel.danger,
            textAlign = TextAlign.Center,
        )
        if (canRestart) {
            Spacer(modifier = Modifier.height(16.dp))
            PixelButton(
                text = "Restart",
                onClick = onRestart,
                style = PixelButtonStyle.Primary,
                enabled = restartEnabled,
            )
        }
    }
}

/** Same pill as the terminal's: it floats over the timeline and keeps its place. */
@Composable
private fun ReconnectingBanner() {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(12.dp),
        contentAlignment = Alignment.TopCenter,
    ) {
        Box(
            modifier = Modifier
                .background(PxDarkSurface)
                .pixelFrame(PxDarkRule)
                .padding(horizontal = 16.dp, vertical = 6.dp),
        ) {
            Text(
                text = "Reconnecting…".chromeCaps(),
                color = PxDarkText,
                style = MaterialTheme.typography.labelMedium,
            )
        }
    }
}
